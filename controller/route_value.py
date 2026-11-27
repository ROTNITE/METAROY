"""Experimental planar public-Core route-aware rescue allocation.
No native or foreign geometry assumptions; opt-in research policy.
"""
import math, heapq
from statistics import median
from controller.reliable_dispatch import ReliablePolicy
from controller.portable import _path_clear, _box, _allowed, point, horizon_of, minimum_assignment, AssignmentError

class VisibilityRouter:
    def __init__(self,zones):
        self.zones=zones; self.cache={}; self.nodes=[]
        for z,b in zones:
            if z['type'] not in ('obstacle','restricted','hazard') or not b: continue
            for x in (b.minimum[0]-.35,b.maximum[0]+.35):
                for y in (b.minimum[1]-.35,b.maximum[1]+.35):
                    q=(x,y,0.)
                    if self.clear(q,q): self.nodes.append(q)
        self.nodes=sorted(set(self.nodes)); self.adj=[[] for _ in self.nodes]
        for i,p in enumerate(self.nodes):
            for j in range(i):
                q=self.nodes[j]
                if self.clear(p,q):
                    d=math.dist(p,q);self.adj[i].append((j,d));self.adj[j].append((i,d))
    def clear(self,a,b):return _path_clear(self.zones,{'kind':'ground','position':a},b)
    def path(self,a,b):
        a,b=tuple(a),tuple(b)
        key=(a,b)
        if key in self.cache:return self.cache[key]
        if self.clear(a,b): result=(math.dist(a,b),b)
        else:
            n=len(self.nodes); best={}; heap=[]
            ends={i:math.dist(q,b) for i,q in enumerate(self.nodes) if self.clear(q,b)}
            for i,q in enumerate(self.nodes):
                if self.clear(a,q):
                    d=math.dist(a,q);best[i]=d;heapq.heappush(heap,(d+math.dist(q,b),d,i,i))
            result=None
            while heap:
                _,d,i,first=heapq.heappop(heap)
                if d!=best[i]:continue
                if i==n:result=(d,self.nodes[first]);break
                if i in ends:
                    nd=d+ends[i]
                    if nd<best.get(n,math.inf):best[n]=nd;heapq.heappush(heap,(nd,nd,n,first))
                for j,w in self.adj[i]:
                    nd=d+w
                    if nd<best.get(j,math.inf):
                        best[j]=nd;heapq.heappush(heap,(nd+math.dist(self.nodes[j],b),nd,j,first))
        if len(self.cache)>4096:self.cache.clear()
        self.cache[key]=result;return result

class RouteValuePolicy(ReliablePolicy):
    def __init__(self,*args,route_mode='route',**kw):
        self.route_mode=route_mode
        super().__init__(*args,**kw)
    def _reset(self,episode):
        super()._reset(episode);self.router=None;self.route_key=None;self.route_active=False
    def decide(self,obs):
        if self.episode!=obs['episode_id']:
            self._reset(obs['episode_id']);self.started=obs['time']
            carriers=[a for a in obs['agents'] if a['kind']=='ground' and 'carry' in a['capabilities']]
            self.low_initial=self._profile() and any((e:=self._usable_energy(a)) is not None and e<.25 for a in carriers)
            self.info_eligible=len(carriers)>=2 and self._profile() and all((e:=self._usable_energy(a)) is not None and e>=.25 for a in carriers)
        zones=[(z,_box(z)) for z in obs['zones']]
        land=[b for z,b in zones if b and z['type']=='land' and _allowed(z,'ground') and not z['constraints'] and not z['risk']]
        self.route_active=bool(self._profile() and self.capabilities.get('environment_id')=='metarescue_planar_reference' and len(land)==1 and land[0].minimum[2]==land[0].maximum[2]==0 and len(zones)<=16 and all(b and not z['constraints'] and not z['access'] for z,b in zones) and not obs['constraints'] and not obs['mission']['constraints'] and not obs['mission'].get('directives') and not obs['observation_quality']['contradiction_count'] and not self.program and all(not a['constraints'] for a in obs['agents']) and (self.route_mode!='route' or any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones)))
        if self.route_active and self.route_mode.startswith(('gate','eco')):
            self.route_active=self.low_initial or any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones)
        if self.route_active:
            key=tuple((z['type'],b.minimum,b.maximum,z['risk']) for z,b in zones)
            if key!=self.route_key:self.router=VisibilityRouter(zones);self.route_key=key
        return super().decide(obs)
    def _approach(self,a,b):
        path=self.router.path(a,b)
        if not path or not self.route_mode.startswith(('dock','gate','eco')):return path,tuple(b)
        a,b=tuple(a),tuple(b)
        if math.dist(a,b)<=1 and self.router.clear(a,b):return (0.,a),a
        reverse=self.router.path(b,a)
        if not reverse:return path,b
        origin=reverse[1];d=math.dist(origin,b)
        if d<=.8:return path,b
        goal=tuple(v+.8*(u-v)/d for u,v in zip(origin,b))
        candidate=self.router.path(a,goal)
        if candidate and candidate[0]<path[0]:return candidate,goal
        return path,b
    def _back(self,obs,goal):
        choices=[]
        for s in obs['infrastructure']:
            if s['kind']=='base_station' and s['status']=='active' and s.get('position') is not None:
                p,_=self._approach(goal,s['position'])
                if p:choices.append((p[0],s['id'],p[1],s))
        return min(choices,key=lambda p:p[:3]) if choices else None
    def _rates(self,a):
        empty=self.rate(a,False); loaded=self.rate(a,True)
        if self.route_mode.startswith(('custody','dock','gate','eco')) and self.low_initial:
            sig=self.resource_signature(a);samples=self.energy_samples.get((a['id'],sig,False),())
            if empty is None and samples:empty=median(samples)
            samples=self.energy_samples.get((a['id'],sig,True),())
            if loaded is None and samples:loaded=median(samples)
            factor=float(self.route_mode.split('-')[1])
        else:factor=1.35
        return empty or loaded, loaded or (empty*factor if empty else None)
    def _estimate(self,obs,a,t):
        goal=point(t['position']);dist=math.dist(a['position'],goal)
        near=dist<=1 and self.router.clear(a['position'],goal)
        path,arrival=((0.,goal),tuple(a['position'])) if near else self._approach(a['position'],goal)
        back=self._back(obs,arrival)
        if path is None or back is None:return None
        action,params=('pickup',{'target_id':t['id']}) if near else ('move',{'target':list(path[1])})
        if not self._supports(a,action,params) or self.cooldowns.get((a['id'],action,repr(params)),-1)>obs['time']:return None
        e=a['resources'].get('energy');empty,loaded=self._rates(a)
        cost=path[0]*(empty or 0.)+back[0]*(loaded or 0.)
        if e and empty is not None and loaded is not None and 1.10*cost>e['value']-e['minimum']:return None
        speed=self.speeds.get(a['id']); length=path[0]+back[0]
        h=horizon_of(obs['mission']);left=h-obs['time'] if h is not None else math.inf
        if speed and max(0.,length-2)/(1.05*speed)>left:return None
        # Unknown speed is ranking prior only, never a hard feasibility bound.
        eta=length/(speed or 1.3)+4
        return action,params,eta,cost,path[0],back[0]
    def _flow_wave(self,obs,zones,agents,claimed,orders):
        if not self.route_active or orders or self.program:return super()._flow_wave(obs,zones,agents,claimed,orders)
        crew=[a for a in agents if a['kind']=='ground' and 'carry' in a['capabilities'] and a['id'] not in self.pending and a['id'] not in self.cargo and not any(c['status']=='failed' for c in self._constraint_checks(obs,a)) and ((e:=a['resources'].get('energy')) is None or e['value']>e['minimum'])]
        targets=sorted((t for t in obs['targets'] if t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('position') is not None and t.get('evacuation_state','waiting')=='waiting' and ('casualty',t['id']) not in claimed and ('casualty',t['id']) not in self.completed),key=lambda t:t['id'])
        if not (1<=len(crew)<=32 and 1<=len(targets)<=64):return {}
        objectives={o.get('target_id'):o for o in obs['mission']['objectives'] if o.get('type')=='rescue'}
        edges={}
        for i,a in enumerate(crew):
            for j,t in enumerate(targets):
                est=self._estimate(obs,a,t)
                if est is None:continue
                _,_,eta,energy,_,_=est;o=objectives.get(t['id'],{});p=t.get('priority',o.get('priority',1.))
                deadline=o.get('deadline_s',o.get('deadline',horizon_of(obs['mission'])))
                weighted=round(1000*p)
                if self.route_mode!='route' and type(deadline) in (float,int) and math.isfinite(deadline):
                    # Reward an on-time opportunity, retain late rescue as a secondary goal.
                    slack=deadline-obs['time']-eta
                    weighted=round(1000*p*max(0.,min(1.,.5+slack/24)))
                if self.route_mode.startswith('eco') and self.low_initial:
                    resource=a['resources'].get('energy'); available=resource['value']-resource['minimum'] if resource else None
                    # Discount expensive trips early, when the same battery could complete more future work.
                    if available and energy>0:
                        h=horizon_of(obs['mission']);fraction=max(0.,min(1.,(h-obs['time'])/max(1.,h-self.started))) if h else .5
                        price=float(self.route_mode.split('-')[2])*fraction
                        weighted=round(weighted/(1+price*energy/available))
                edges[i,j]=(weighted,round(1000*eta))
        if not edges:return {a['id']:None for a in crew}
        n=len(crew);bound=n*max(d for p,d in edges.values())+1
        count=n*max(p for p,d in edges.values())*bound+bound
        costs=[]
        for i,a in enumerate(crew):
            row=[]
            for j,t in enumerate(targets):
                if (i,j) not in edges:row.append(None);continue
                p,d=edges[i,j]
                row.append(-count-p*bound+d if self.route_mode=='route' else -p*bound*(n+1)-bound+d)
            costs.append(row+[0]*n)
        try:chosen,_,_,_=minimum_assignment(costs)
        except AssignmentError:return super()._flow_wave(obs,zones,agents,claimed,orders)
        return {a['id']:targets[j]['id'] if j<len(targets) else None for a,j in zip(crew,chosen)}
    def _casualty(self,obs,zones,a,claimed,issue,wave=None):
        if not self.route_active or a['kind']!='ground' or 'carry' not in a['capabilities']:return super()._casualty(obs,zones,a,claimed,issue,wave)
        aid=a['id']
        if aid in self.cargo:
            back=self._back(obs,a['position']);intent=('casualty',self.cargo[aid])
            if back:
                d,sid,nextp,s=back
                if math.dist(a['position'],s['position'])<=1 and self.router.clear(a['position'],s['position']):issue(a,'handover',{'station_id':sid},intent)
                else:
                    e=a['resources'].get('energy');_,r=self._rates(a)
                    if e is None or r is None or math.dist(a['position'],nextp)*r*1.10<=e['value']-e['minimum']:
                        if self._motion_clear(obs,zones,a,nextp):issue(a,'move',{'target':list(nextp)},intent)
            return True
        if not wave or aid not in wave:return super()._casualty(obs,zones,a,claimed,issue,wave)
        tid=wave[aid]
        for t in obs['targets']:
            if t['id']!=tid or ('casualty',tid) in claimed or ('casualty',tid) in self.completed:continue
            est=self._estimate(obs,a,t)
            if est:
                action,params,*_=est
                if action=='move' and not self._motion_clear(obs,zones,a,params['target']):return False
                if issue(a,action,params,('casualty',tid)):
                    self.traces.append({'kind':'route_value','type':'route_value','episode_id':self.episode,'time':obs['time'],'agent_id':aid,'target_id':tid,'mode':self.route_mode,'eta':est[2],'out_distance':est[4],'return_distance':est[5]})
                    return True
        return False
