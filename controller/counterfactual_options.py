"""Bounded online choice of scout survey options, learned from simulator forks."""
from controller.counterfactual_value import CounterfactualPolicy,predict
from controller.adaptive_wave import AdaptiveWavePolicy
from controller.wave_survey_trial import WaveSurveyPolicy
from controller.rescue_aware import RescueAwarePolicy
from controller.portable import horizon_of

OPTION_PHASES=(.15,.40,.65)
DURATION=40.
OPTIONS=('high','economy','wave','pause')

class OptionPolicy(CounterfactualPolicy):
    def __init__(self,*args,option_models=None,option_threshold=.002,force_option=None,**kw):
        self.option_models=option_models;self.option_threshold=option_threshold;self.force_option=force_option
        super().__init__(*args,**kw)
    def _reset(self,episode):
        super()._reset(episode);self.option_active={}
    def _target_service(self,obs,zones,a,claimed,issue):
        baseline=lambda:AdaptiveWavePolicy._target_service(self,obs,zones,a,claimed,issue)
        aid=a['id'];now=obs['time'];h=horizon_of(obs['mission'])
        # Allow previously started route to finish. Only intervene if the same
        # narrow public profile still applies and no external directives apply.
        valid=self._profile() and self.capabilities.get('environment_id')=='metarescue_planar_reference' and 'metarescue.patrol_estimates.v1' in self.public.extensions and self.dock_mode!='dock-only' and not self.program and not obs['constraints'] and not obs['mission']['constraints'] and not obs['mission'].get('directives') and not obs['observation_quality']['contradiction_count'] and not any(r['constraints'] for r in obs['agents'])
        if not valid or h is None or h<=self.started:
            self.option_active.pop(aid,None);return baseline()
        if self._supported_cf(obs,a):
            frac=(now-self.started)/(h-self.started)
            phase=next((i for i,f in enumerate(OPTION_PHASES) if f<=frac<f+.1 and i not in self.cf_seen),None)
            if phase is not None:
                self.cf_seen.add(phase);x=self._features(obs,a,h);self.cf_probe={'phase':phase,'x':x,'time':now,'agent':aid}
                option=None;prediction=None;estimates={}
                if self.force_option and self.force_option[0]==phase:option=self.force_option[1]
                elif self.option_models:
                    price=self.option_models.get('energy_price',.3);min_m1=self.option_models.get('min_m1',-.001)
                    candidates=[]
                    for name,model in self.option_models['models'].items():
                        m,e=predict(model,x);score=m-price*e;estimates[name]=[m,e,score]
                        if m>=min_m1 and score>self.option_threshold:candidates.append((score,name,[m,e]))
                    if candidates:_,option,prediction=max(candidates)
                if option is not None:
                    self.option_active[aid]=(min(h,now+DURATION),option)
                    self.cf_audit.append({'agent_id':aid,'phase':phase,'option':option,'until':min(h,now+DURATION),'prediction':prediction,'alternatives':estimates,'basis':'public_features_bounded_scout_option'})
        active=self.option_active.get(aid)
        if not active or active[0]<=now or a['kind']!='aerial':return baseline()
        option=active[1]
        if option=='pause':return True
        prev=(self.wave_fraction,self.survey_budget,self.budget_cap)
        try:
            if option=='high':
                self.survey_budget=self.budget_cap=.12
                return RescueAwarePolicy._target_service(self,obs,zones,a,claimed,issue)
            self.wave_fraction=.30 if option=='economy' else .25
            self.survey_budget=self.budget_cap=.10 if option=='economy' else .12
            return WaveSurveyPolicy._target_service(self,obs,zones,a,claimed,issue)
        finally:self.wave_fraction,self.survey_budget,self.budget_cap=prev
