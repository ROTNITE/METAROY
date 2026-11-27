"""Frozen contextual wave exploration candidate. Context uses public geometry,
fleet resources, and initial observed goals; no scenario-family labels or seed.
"""
from controller.wave_survey_trial import WaveSurveyPolicy
from controller.rescue_aware import RescueAwarePolicy
from controller.portable import horizon_of
class AdaptiveWavePolicy(WaveSurveyPolicy):
    def __init__(self,*args,**kw):
        super().__init__(*args,wave_mode='wave-0.25-0.12-20',**kw)
    def decide(self,obs):
        fresh=self.episode!=obs['episode_id']
        if fresh:
            walls=[z for z in obs['zones'] if z['type'] in ('obstacle','restricted','hazard')]
            scouts=sum(r['kind']=='aerial' and 'sense' in r['capabilities'] for r in obs['agents'])
            low=any(r['kind']=='ground' and 'carry' in r['capabilities'] and (e:=self._usable_energy(r)) is not None and e<.25 for r in obs['agents'])
            known=sum(t.get('kind')=='person' for t in obs['targets'])
            urgent=any(t.get('kind')=='person' and t.get('priority',1)!=1 for t in obs['targets']) or any(type(o.get('deadline_s')) in (int,float) and horizon_of(obs['mission']) is not None and o['deadline_s']<horizon_of(obs['mission']) for o in obs['mission']['objectives'])
            self.original_high=bool(len(walls)>=3 and not any(z['type']=='restricted' for z in walls) and not low)
            economical=low or bool(walls and scouts==1) or (not walls and not urgent and known>=4)
            self.wave_fraction=.30 if economical else .25;self.budget_cap=.10 if economical else .12
            if self.original_high:self.budget_cap=.12
        out=super().decide(obs)
        if fresh:self.traces.append({'kind':'adaptive_wave_context','type':'adaptive_wave_context','episode_id':obs['episode_id'],'time':obs['time'],'request_id':obs['request_id'],'decision_id':out['decision_id'],'original_high':self.original_high,'phase_fraction':self.wave_fraction,'budget':self.budget_cap,'basis':'public_initial_geometry_resources_known_goals'})
        return out
    def _target_service(self,obs,zones,a,claimed,issue):
        if self.original_high:return RescueAwarePolicy._target_service(self,obs,zones,a,claimed,issue)
        return super()._target_service(obs,zones,a,claimed,issue)
