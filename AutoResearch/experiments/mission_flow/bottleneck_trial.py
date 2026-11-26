"""Experimental portable dispatcher: public motion estimates."""
from controller.portable import PortablePolicy, point, horizon_of, minimum_assignment, AssignmentError, _path_clear
import math

class BottleneckPolicy(PortablePolicy):
    def __init__(self,*args,variant='eta',**kw):
        self.early=variant.startswith('scout')
        self.adaptive=variant.startswith('scout_adapt')
        self.staged=variant.startswith('scout_staged')
        self.extra=int(variant[-1]) if variant.startswith('scout_stagedplus') else 0
        self.scout_guard=self.adaptive or self.staged or variant=='scout_guard3'
        self.reachable_only=variant in ('scout_reachable','scout_combined') or variant.startswith('scout_cap') or self.scout_guard
        self.repeat_cap=int(variant[-1]) if variant.startswith('scout_cap') else 3 if self.scout_guard else None
        self.threshold=.40 if variant in ('scout40','scout_combined') or variant.startswith('scout_cap') or self.scout_guard else 0. if variant=='scout0' else .65
        if self.early:variant='eta_strict'
        self.eta=variant in ('eta','eta_single','eta_deadline','eta_strict')
        self.deadline=variant in ('deadline','eta_deadline','eta_strict')
        self.margin=1.05 if variant=='eta_strict' else 1.35
        self.single=variant in ('eta_single','single','deadline','eta_deadline','eta_strict')
        super().__init__(*args,**kw)
    def _reset(self,episode):
        super()._reset(episode)
        self.speeds={};self.last_motion={};self.visits={};self.scout_enabled=False
    def decide(self,observation):
        if self.episode!=observation['episode_id']:
            self._reset(observation['episode_id']);self.started=observation['time']
        if self.public.identity and not self.public.short_actions:
            now=observation['time']
            for a in observation['agents']:
                aid=a['id'];pos=a.get('position');connected=a['communication'].get('reachable',a['communication'].get('connected',True)) is not False
                prev=self.last_motion.get(aid);pending=self.pending.get(aid)
                if pos is not None and connected and a['kind']=='ground':
                    if prev and pending and pending[3][1]=='move' and 0<now-prev[0]<=2:
                        v=math.dist(pos,prev[1])/(now-prev[0])
                        if v>1e-6:self.speeds[aid]=max(v,self.speeds.get(aid,0))
                    self.last_motion[aid]=(now,tuple(pos))
                else:self.last_motion.pop(aid,None)
        if self.scout_guard:
            ground=[a for a in observation['agents'] if a['kind']=='ground' and 'carry' in a['capabilities']]
            self.scout_enabled=bool(ground) and all((e:=a['resources'].get('energy')) and e.get('maximum',e['minimum'])>e['minimum'] and (e['value']-e['minimum'])/(e['maximum']-e['minimum'])>=.25 for a in ground)
        else:self.scout_enabled=self.early
        if not self.early or not self.scout_enabled:return super().decide(observation)
        # Research-only injection; each process executes episodes sequentially.
        import controller.portable as portable
        old=portable.due
        portable.due=lambda now,start,horizon,covered:covered and horizon is not None and start<horizon and now>=start+self.threshold*(horizon-start)
        try:return super().decide(observation)
        finally:portable.due=old
    def _scout_points(self,zones,agent):
        pts=list(super()._scout_points(zones,agent))
        if not self.scout_enabled or not self.reachable_only or agent['kind']!='aerial' or not self.public.identity or self.public.short_actions:return pts
        # Connected component of public, straight-line feasible candidate scan locations.
        reached=[];frontier=[tuple(agent['position'])];remaining=set(pts)
        while frontier:
            origin=frontier.pop()
            accessible=sorted(p for p in remaining if _path_clear(zones,{**agent,'position':origin},p))
            reached.extend(accessible);frontier.extend(accessible);remaining.difference_update(accessible)
        return reached
    def observe_response(self,response):
        before=self.scanned_at.copy()
        super().observe_response(response)
        for p,t in self.scanned_at.items():
            if p in before and t>before[p]:self.visits[p]=self.visits.get(p,0)+1
    def _repeat_sector(self,observation,zones,agent,points,claimed,scouts):
        if self.repeat_cap is not None and self.scout_enabled:
            horizon=horizon_of(observation['mission']);fraction=(observation['time']-self.started)/(horizon-self.started) if horizon and horizon>self.started else 1.
            if self.adaptive and fraction>=.65:
                free=sum(a['kind']=='ground' and 'carry' in a['capabilities'] and a['status']=='active' and a['id'] not in self.pending and a['id'] not in self.cargo and a['communication'].get('reachable',True) for a in observation['agents'])
                waiting=sum(t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('evacuation_state','waiting')=='waiting' and ('casualty',t['id']) not in claimed for t in observation['targets'])
                if free>waiting:return super()._repeat_sector(observation,zones,agent,points,claimed,scouts)
                return None,'patrol_wait_for_capacity'
            cap=(1 if fraction<.55 else 2 if fraction<.70 else 3) if self.staged else self.repeat_cap
            if self.extra and fraction>=.70:
                free=sum(a['kind']=='ground' and 'carry' in a['capabilities'] and a['status']=='active' and a['id'] not in self.pending and a['id'] not in self.cargo and a['communication'].get('reachable',True) for a in observation['agents'])
                waiting=sum(t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('evacuation_state','waiting')=='waiting' and ('casualty',t['id']) not in claimed for t in observation['targets'])
                if free>waiting:cap+=self.extra
            if sum(self.visits.values())>=cap:return None,'repeat_budget_used' 
            points=[p for p in points if self.visits.get(p,0)==0]
            if not points:return None,'repeat_pass_complete' 
        return super()._repeat_sector(observation,zones,agent,points,claimed,scouts)
    def _flow_wave(self, observation, zones, agents, claimed, orders):
        """One-wave matching using only declared public geometry and actions.

        No guessed speeds, energy consumption, or hidden target positions.
        Unknown/nonstandard action profiles retain the baseline dispatcher.
        """
        if (not self.flow_dispatch or not self.public.identity or self.public.short_actions or
                self.program or orders or observation['observation_quality']['contradiction_count'] or
                not {'pickup','handover','move'} <= self.actions.keys()):
            return {}
        crew = [a for a in agents if a['id'] not in self.pending and a['id'] not in self.cargo
                and 'carry' in a['capabilities'] and a['kind']=='ground'
                and (not a['resources'].get('energy') or a['resources']['energy']['value']>a['resources']['energy']['minimum'])
                and not any(c['status']=='failed' for c in self._constraint_checks(observation,a))]
        # A geometry-only cost cannot certify energy feasibility. Retain the
        # stock dispatcher if any free ground carrier is near its public reserve.
        # 25% of the declared usable range is an explicit conservative gate,
        # not a model of ground energy consumption or a safety certificate.
        for a in crew:
            energy=a['resources'].get('energy')
            if energy:
                span=energy.get('maximum',energy['minimum'])-energy['minimum']
                if span<=0 or (energy['value']-energy['minimum'])/span<.25:
                    return {}
        targets = sorted((t for t in observation['targets'] if t.get('kind')=='person'
                          and t.get('status')=='confirmed' and t.get('position') is not None
                          and t.get('evacuation_state','waiting')=='waiting'
                          and ('casualty',t['id']) not in claimed and ('casualty',t['id']) not in self.completed),
                         key=lambda t:t['id'])
        stations = [s for s in observation['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        if not ((1 if self.single else 2)<=len(crew)<=32 and 1<=len(targets)<=64 and stations):return {}
        edges={};excluded=0
        horizon=horizon_of(observation['mission'])
        priorities={o.get('target_id'):o.get('priority',0) for o in observation['mission']['objectives'] if o.get('type')=='rescue'}
        for i,a in enumerate(crew):
            for j,t in enumerate(targets):
                goal=point(t['position']);dist=math.dist(a['position'],goal)
                action,params=('pickup',{'target_id':t['id']}) if dist<=1 else ('move',{'target':list(goal)})
                if not self._supports(a,action,params) or self.cooldowns.get((a['id'],action,repr(params)),-1)>observation['time']:continue
                if dist>1 and not _path_clear(zones,a,goal):continue
                at={**a,'position':goal}
                back=[math.dist(goal,s['position']) for s in stations if _path_clear(zones,at,s['position'])]
                if not back:continue
                p=t.get('priority',priorities.get(t['id'],0))
                length=dist+min(back)
                speed=self.speeds.get(a['id'])
                if self.deadline and speed and horizon is not None and max(0.,length-2)/(self.margin*speed)>horizon-observation['time']:
                    excluded+=1
                    continue
                if self.eta and all(c['id'] in self.speeds for c in crew):
                    length=length/self.speeds[a['id']]
                edges[i,j]=(max(0,round(p*1000)),round(length*1000))
        if not edges:
            if excluded and all(a['id'] in self.speeds for a in crew):
                return {a['id']:None for a in crew}
            return {}
        n=len(crew);cost_bound=n*max(d for p,d in edges.values())+1
        count_weight=n*max(p for p,d in edges.values())*cost_bound+cost_bound
        costs=[[(-count_weight-p*cost_bound+d if (i,j) in edges else None)
                for j in range(len(targets)) for p,d in [edges.get((i,j),(0,0))]]+[0]*n for i in range(n)]
        try:chosen,_,_,work=minimum_assignment(costs)
        except AssignmentError:return {}
        result={a['id']:targets[j]['id'] if j<len(targets) else None for a,j in zip(crew,chosen)}
        self.traces.append({'kind':'flow_wave','type':'portable_flow_wave', 'episode_id':self.episode,
                            'time':observation['time'], 'request_id':observation['request_id'],
                            'matches':result,'relaxations':work,
                            'candidate_edges':[{'agent_id':crew[i]['id'],'target_id':targets[j]['id'],
                                                'priority_milli':p,'cost_milli':d}
                                               for (i,j),(p,d) in edges.items()]})
        return result

