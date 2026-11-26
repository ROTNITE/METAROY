"""Bounded receding-horizon dispatcher based on public observations."""
import math
from controller.resource_predictor import ResourcePolicy
from controller.portable import _path_clear, horizon_of


def schedule(first, future, budgets, priorities, baseline, width=12, depth=2, fixed_count=None, delays=None):
    n=len(first); m=len(priorities); fixed_count=n if fixed_count is None else fixed_count
    delays=delays or [0.]*n
    empty=tuple(() for _ in range(n)); init=(0,empty,(0.,)*n,(0.,)*n,0.,0.)
    def score(s):return (s[0].bit_count(),s[4],-s[5],-sum(s[3]))
    def search(fixed):
        start=init
        if fixed is not None:
            mask=0;seq=list(empty);t=[0.]*n;e=[0.]*n;weight=completion=0.
            for i,j in enumerate(fixed[:fixed_count]):
                if j is None:continue
                if j not in first[i] or mask&(1<<j):return None
                dt,de=first[i][j]
                if dt>budgets[i][0] or de>budgets[i][1]:return None
                mask|=1<<j;seq[i]=(j,);t[i]=dt;e[i]=de;weight+=priorities[j];completion+=dt+delays[i]
            start=(mask,tuple(seq),tuple(t),tuple(e),weight,completion)
        states=[start];best=start
        for _ in range(min(m,n*depth)):
            out={}
            for mask,seq,t,e,weighted,completion in states:
                for i in range(n):
                    k=len(seq[i]);
                    if k>=depth or (k==0 and fixed is not None and i<fixed_count):continue
                    for j,(dt,de) in (first[i] if k==0 else future[i]).items():
                        if mask&(1<<j):continue
                        nt=t[i]+dt;ne=e[i]+de
                        if nt>budgets[i][0]+1e-9 or ne>budgets[i][1]+1e-12:continue
                        ns=seq[:i]+(seq[i]+(j,),)+seq[i+1:]
                        out[ns]=(mask|(1<<j),ns,t[:i]+(nt,)+t[i+1:],e[:i]+(ne,)+e[i+1:],weighted+priorities[j],completion+nt+delays[i])
            if not out:break
            states=sorted(out.values(),key=score,reverse=True)[:width]
            if score(states[0])>score(best):best=states[0]
        return best
    reference=search(baseline)
    if reference is None:return baseline,None,None
    candidate=search(None)
    wave=tuple(s[0] if s else None for s in candidate[1][:fixed_count])
    if sum(j is not None for j in wave)<sum(j is not None for j in baseline[:fixed_count]):wave=baseline[:fixed_count]
    return wave,score(reference),score(candidate)


class LogisticsPolicy(ResourcePolicy):
    def __init__(self,*args,logistics='mpc',**kwargs):
        self.logistics=logistics
        super().__init__(*args,**kwargs)

    def _flow_wave(self, observation,zones,agents,claimed,orders):
        baseline=super()._flow_wave(observation,zones,agents,claimed,orders)
        if not baseline or not self._profile() or self.program or orders:return baseline
        trace=next((x for x in reversed(self.traces) if x.get('kind')=='flow_wave' and x.get('request_id')==observation['request_id']),None)
        if not trace or trace.get('method')!='resource_predictor_v1':return baseline
        eligible={(x['agent_id'],x['target_id']) for x in trace['candidate_edges']}
        if self.logistics=='commit':
            targets={t['id']:t for t in observation['targets'] if t.get('position') is not None}
            out=dict(baseline)
            for a in agents:
                aid=a['id']
                if aid not in out:continue
                near=[j for j,t in targets.items() if (aid,j) in eligible and math.dist(a['position'],t['position'])<=1.]
                if not near:continue
                j=min(near,key=lambda j:(math.dist(a['position'],targets[j]['position']),j))
                if out[aid]==j:continue
                old=out[aid];other=next((k for k,v in out.items() if v==j),None)
                if other is None:
                    if old is not None:out[aid]=j
                elif old is not None and (other,old) in eligible:out[aid]=j;out[other]=old
            if out!=baseline:self.traces.append({'kind':'logistics_mpc','type':'logistics_commit','request_id':observation['request_id'],'time':observation['time'],'matches':out})
            return out
        crew=[a for a in agents if a['id'] in baseline]
        targets=[t for t in observation['targets'] if any((a['id'],t['id']) in eligible for a in crew)]
        stations=[s for s in observation['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        end=horizon_of(observation['mission']); now=observation['time']
        if not (1<=len(crew)<=4 and len(crew)<len(targets)<=12 and len(stations)==1 and end is not None and end>now):return baseline
        if not all(self.speeds.get(a['id']) and self.rate(a,False) and self.rate(a,True) and self.service_time(a['id']) is not None for a in crew):return baseline
        base=stations[0]['position'];first=[];future=[];budgets=[];delays=[]
        free_count=len(crew)
        busy_mode=self.logistics in ('busy','busy-time','busy-flex')
        if busy_mode:
            known={t['id']:t for t in observation['targets'] if t.get('position') is not None}
            # Estimate the remaining current trip, but never issue commands
            # to a busy carrier or unclaim its current casualty.
            for a in observation['agents']:
                aid=a['id'];pending=self.pending.get(aid);cargo=self.cargo.get(aid)
                if a['kind']!='ground' or a['position'] is None or not a['communication'].get('reachable',True):continue
                if not (cargo or pending and pending[2] and pending[2][0]=='casualty'):continue
                if not (self.speeds.get(aid) and self.rate(a,False) and self.rate(a,True) and self.service_time(aid) is not None):continue
                if not a['resources'].get('energy'):continue
                t=known.get(cargo or pending[2][1]);p=t['position'] if t else None
                if p is None:continue
                empty_leg=0. if cargo else math.dist(a['position'],p)
                loaded_leg=math.dist(a['position'] if cargo else p,base)
                delay=(empty_leg+loaded_leg)/self.speeds[aid]+self.service_time(aid)
                energy=empty_leg*self.rate(a,False)+loaded_leg*self.rate(a,True)
                if delay>=end-now:continue
                crew.append({**a,'position':base,'_delay':delay,'_spent':energy})
            if len(crew)>4 or len(crew)==free_count:return baseline
        for a in crew:
            speed=self.speeds[a['id']];empty=self.rate(a,False);loaded=self.rate(a,True);service=self.service_time(a['id'])
            resource=a['resources']['energy'];budgets.append((end-now-a.get('_delay',0),(resource['value']-resource['minimum'])/1.10-a.get('_spent',0)));delays.append(a.get('_delay',0))
            f={};g={}
            for j,t in enumerate(targets):
                p=t['position'];back=math.dist(p,base);d=math.dist(a['position'],p)
                if (a['id'],t['id']) in eligible:f[j]=(math.ceil(d/speed)+math.ceil(back/speed)+service,d*empty+back*loaded)
                if _path_clear(zones,{**a,'position':base},p) and _path_clear(zones,{**a,'position':p},base):g[j]=(2*math.ceil(back/speed)+service,back*(empty+loaded))
            first.append(g if '_delay' in a else f);future.append(g)
        ids=[t['id'] for t in targets]; prior={o.get('target_id'):o.get('priority',0) for o in observation['mission']['objectives']}
        priorities=[t.get('priority',prior.get(t['id'],0)) for t in targets]
        initial=tuple(ids.index(baseline.get(a['id'])) if baseline.get(a['id']) in ids else None for a in crew[:free_count])
        wave,ref,trial=schedule(first,future,budgets,priorities,initial,fixed_count=free_count,delays=delays)
        if ref is None or wave==initial or trial[1]<ref[1]:return baseline
        improve=trial[0]>ref[0] or self.logistics in ('mpc-time','busy-time','busy-flex') and trial[0]==ref[0] and trial[2]>ref[2]+2
        if not improve:return baseline
        result={a['id']:ids[j] if j is not None else None for a,j in zip(crew[:free_count],wave)}
        self.traces.append({'kind':'logistics_mpc','type':'logistics_mpc','request_id':observation['request_id'],'time':now,'baseline':baseline,'matches':result,'baseline_score':ref,'candidate_score':trial})
        return result

class SurveyPolicy(ResourcePolicy):
    """Use a declared larger sensing radius instead of the fixed 10 m cap.
    Opt-in experiment: a larger radius is not assumed to be free in other peers.
    """
    def __init__(self,*args,survey='range',**kwargs):
        self.survey=survey
        super().__init__(*args,**kwargs)

    def _observe_parameters(self):
        params=super()._observe_parameters()
        if not self._profile() or 'radius' not in params:return params
        schema=self.actions.get('observe',{}).get('parameters_schema',{}).get('properties',{}).get('radius',{})
        upper=schema.get('maximum')
        if type(upper) in (int,float) and math.isfinite(upper) and upper>params['radius'] and upper<=100:
            params['radius']=upper*self.public.scale
        return params

    def _repeat_sector(self, observation, zones, agent, points, claimed, scouts):
        if self.survey in ('range-budget','range-demand','range-late') and self.scout_enabled:
            h=horizon_of(observation['mission']);f=(observation['time']-self.started)/(h-self.started) if h and h>self.started else 1.
            if self.survey=='range-budget':
                cap=0 if f<.55 else 1 if f<.70 else 2
            else:
                free=sum(a['kind']=='ground' and 'carry' in a['capabilities'] and a['status']=='active' and a['id'] not in self.pending and a['id'] not in self.cargo and a['communication'].get('reachable',True) for a in observation['agents'])
                waiting=sum(t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('evacuation_state','waiting')=='waiting' and ('casualty',t['id']) not in claimed for t in observation['targets'])
                cap=(0 if f<.55 else 1 if f<.70 else 2) if self.survey=='range-late' else (1 if f<.55 else 2 if f<.70 else 3)
                if free>waiting:cap+=1 if f<.7 else 2
                if free<=waiting and f<.70:return None,'survey_backlog'
            if sum(self.visits.values())>=cap:return None,'survey_budget'
        return super()._repeat_sector(observation,zones,agent,points,claimed,scouts)
