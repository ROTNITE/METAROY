"""Research-only counterfactual scout pacing. Public features, no world import.
The alternative is a bounded pause of an idle scout, not fabricated completion.
"""
import math
from statistics import mean
from controller.adaptive_wave import AdaptiveWavePolicy
from controller.portable import horizon_of

FEATURES=('fraction','remaining','scouts','carriers','free','busy','cargo','known','waiting','done','urgent','scout_energy','ground_energy','ground_min','speed','walls','restricted','scans','age_mean','age_min','age_max','base_dist','queue_per_carrier','nearest_waiting','reachable','max_delay','original_high','budget','min_trip','newest_priority')
PHASES=(.35,.575,.775)
PAUSE=25.

class CounterfactualPolicy(AdaptiveWavePolicy):
    def __init__(self,*args,model=None,threshold=0.,force_phase=None,**kw):
        self.cf_model=model;self.cf_threshold=threshold;self.cf_force=force_phase
        super().__init__(*args,**kw)
    def _reset(self,episode):
        super()._reset(episode);self.cf_seen=set();self.cf_pauses={};self.cf_probe=None;self.cf_audit=[]
    def decide(self,obs):
        self.cf_probe=None;self.cf_audit=[]
        out=super().decide(obs)
        for entry in self.cf_audit:
            self.traces.append({'kind':'counterfactual_value','type':'counterfactual_value','episode_id':obs['episode_id'],'time':obs['time'],'request_id':obs['request_id'],'decision_id':out['decision_id'],**entry})
        return out
    def _supported_cf(self,obs,a):
        return (a['kind']=='aerial' and 'sense' in a['capabilities'] and self.scout_patrol=='net65' and self._profile() and self.capabilities.get('environment_id')=='metarescue_planar_reference' and 'metarescue.patrol_estimates.v1' in self.public.extensions and self.dock_mode!='dock-only' and not self.program and self._research_constraints_supported(obs) and not obs['mission'].get('directives') and not obs['observation_quality']['contradiction_count'] and a['status']=='active' and a['communication'].get('reachable',False) and a.get('position') is not None and len(self.scanned_at)>0 and a['id'] not in self.explore_goals and a['id'] not in self.repeat_goals)
    def _features(self,obs,a,h):
        now=obs['time'];remaining=h-now
        crew=[r for r in obs['agents'] if r['kind']=='ground' and 'carry' in r['capabilities'] and r['position'] is not None]
        active=[r for r in crew if r['status'] in ('active','busy','degraded') and r['communication'].get('reachable',False)]
        targets=[t for t in obs['targets'] if t.get('kind')=='person']
        waiting=[t for t in targets if t.get('evacuation_state')=='waiting' and t.get('position') is not None]
        bases=[s['position'] for s in obs['infrastructure'] if s['kind']=='base_station' and s['status']=='active' and s.get('position') is not None]
        e=[max(0.,r['resources']['energy']['value']-r['resources']['energy']['minimum']) for r in active if 'energy' in r['resources']]
        ages=[now-t for t in self.scanned_at.values()]
        speed=[self.speeds.get(r['id'],1.3) for r in active]
        walls=[z for z in obs['zones'] if z['type'] in ('obstacle','restricted','hazard')]
        trips=[(math.dist(r['position'],t['position'])+min(math.dist(t['position'],b) for b in bases))/max(.5,self.speeds.get(r['id'],1.3))+4 for r in active for t in waiting] if bases else []
        return [float(v) for v in ((now-self.started)/max(1.,h-self.started),remaining,sum(r['kind']=='aerial' and 'sense' in r['capabilities'] for r in obs['agents']),len(crew),sum(r['status']=='active' and r['id'] not in self.pending and r['id'] not in self.cargo for r in active),sum(r['status']=='busy' or r['id'] in self.pending for r in active),len(self.cargo),len(targets),len(waiting),sum(t.get('evacuation_state')=='evacuated' for t in targets),sum(t.get('priority',1)!=1 for t in waiting),a['resources']['energy']['value'],mean(e) if e else 0,min(e,default=0),mean(speed) if speed else 0,len(walls),sum(z['type']=='restricted' for z in walls),len(ages),mean(ages),min(ages),max(ages),min((math.dist(a['position'],b) for b in bases),default=100),len(waiting)/max(1,len(active)),min((math.dist(a['position'],t['position']) for t in waiting),default=100),len(active),obs['observation_quality'].get('maximum_delay',0),self.original_high,self.budget_cap,min(trips,default=200),max((t.get('priority',1) for t in waiting),default=0))]
    def _target_service(self,obs,zones,a,claimed,issue):
        if not self._supported_cf(obs,a):return super()._target_service(obs,zones,a,claimed,issue)
        now=obs['time'];h=horizon_of(obs['mission']);aid=a['id']
        if h is None or h<=self.started:return super()._target_service(obs,zones,a,claimed,issue)
        if now<self.cf_pauses.get(aid,-1):return True
        fraction=(now-self.started)/(h-self.started)
        phase=next((i for i,f in enumerate(PHASES) if f<=fraction<f+.095 and i not in self.cf_seen),None)
        if phase is None:return super()._target_service(obs,zones,a,claimed,issue)
        self.cf_seen.add(phase)
        x=self._features(obs,a,h);self.cf_probe={'phase':phase,'x':x,'time':now,'agent':aid}
        prediction=None;pause=self.cf_force==phase
        if self.cf_model is not None:
            prediction=predict(self.cf_model,x)
            price=self.cf_model.get('energy_price',.3)
            pause=prediction[0]>=self.cf_model.get('min_m1',-.001) and prediction[0]-price*prediction[1]>self.cf_threshold
        if pause:
            self.cf_pauses[aid]=min(h,now+PAUSE)
            self.cf_audit.append({'agent_id':aid,'phase':phase,'pause_until':self.cf_pauses[aid],'prediction':prediction,'basis':'public_features_bounded_scout_pause'})
            return True
        return super()._target_service(obs,zones,a,claimed,issue)

def predict(model,x):
    # JSON numerical trees, no pickle or ML dependency at runtime.
    values=[]
    for nodes in model['trees']:
        i=0
        while nodes[i][0]>=0:
            f,t,l,r=nodes[i];i=l if x[f]<=t else r
        values.append(nodes[i][1])
    return [mean(v[k] for v in values) for k in range(2)]
