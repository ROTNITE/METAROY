"""Phase-synchronized marginal coverage, preserving public safety checks."""
import math
from controller.marginal_exploration import MarginalPolicy
from controller.arrival_trial import ArrivalPolicy
from controller.portable import _path_clear,horizon_of
class WaveSurveyPolicy(MarginalPolicy):
    def __init__(self,*args,wave_mode='wave-0.30-0.10-20',**kw):
        _,fraction,budget,gap=wave_mode.split('-');self.wave_fraction=float(fraction);self.minimum_gap=float(gap)
        super().__init__(*args,marginal_mode=f'marginal-{budget}-0-0-0',**kw)
    def _target_service(self,obs,zones,a,claimed,issue):
        if self.dock_mode=='dock-only':return ArrivalPolicy._target_service(self,obs,zones,a,claimed,issue)
        if ArrivalPolicy._target_service(self,obs,zones,a,claimed,issue):return True
        if (a['kind']!='aerial' or 'sense' not in a['capabilities'] or self.scout_patrol!='net65' or not self._profile() or
            self.capabilities.get('environment_id')!='metarescue_planar_reference' or 'metarescue.patrol_estimates.v1' not in self.public.extensions or
            self.program or not self._research_constraints_supported(obs) or obs['mission'].get('directives') or obs['observation_quality']['contradiction_count']):return False
        data=self._geometry(zones,a)
        if data is None:return False
        router,samples,cover=data; now=obs['time'];h=horizon_of(obs['mission']);energy=a['resources'].get('energy');est=self.capabilities.get('patrol_estimates',{})
        speed,scan,rate,scan_e=(est.get(k) for k in ('speed_m_s','scan_s','move_fraction_per_m','scan_fraction'))
        if h is None or energy is None or not all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in (speed,scan,rate,scan_e)):return False
        bases=[tuple(s['position']) for s in obs['infrastructure'] if s['kind']=='base_station' and s['status']=='active' and s.get('position') is not None]
        if not bases:return False
        crew=[r for r in obs['agents'] if r['kind']=='ground' and 'carry' in r['capabilities'] and r['position'] is not None and r['status'] in ('active','busy','degraded') and r['communication'].get('reachable',True) and (e:=r['resources'].get('energy')) and e['value']>e['minimum']]
        if not crew:return True
        if self.scheme in ('hybrid','shared','repair','repairwalls','repairdemand','repairgap','repairgaplate','phase','phasekde','phasewalls','phasecheap','efficient','efficientwalls','efficientkde') and self.low_initial and not any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones):return False
        aid=a['id'];position=tuple(a['position']); remaining=h-now
        start_energy=self.survey_initial.setdefault(aid,energy['value'])
        budget=min(energy['value']-energy['minimum'],self.survey_budget-(start_energy-energy['value']))
        if self.scheme.startswith(('phase','efficient')):
            if self.scheme in ('phasewalls','efficientwalls') and not any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones):return False
            fraction=(now-self.started)/max(1,h-self.started)
            # Keep part of the survey allowance for later arrivals, instead of
            # exhausting it early. Fractions are public clock only.
            release=.55 if fraction<.32 else .80 if fraction<.56 else 1.
            if self.scheme in ('phasecheap','efficient','efficientwalls','efficientkde'):release=.65 if fraction<.32 else .85 if fraction<.56 else 1.
            budget=min(budget,release*self.survey_budget-(start_energy-energy['value']))
            if len([r for r in obs['agents'] if r['kind']=='aerial' and 'sense' in r['capabilities']])>1:
                spent=sum(max(0.,v-r['resources']['energy']['value']) for r in obs['agents'] if (v:=self.survey_initial.get(r['id'])) is not None and r['resources'].get('energy'))
                budget=min(budget,(1.0 if self.scheme.startswith('efficient') else 1.35)*release*self.survey_budget-spent)
        if self.scheme=='shared':
            spent=sum(max(0.,v-r['resources']['energy']['value']) for r in obs['agents'] if (v:=self.survey_initial.get(r['id'])) is not None and r['resources'].get('energy'))
            budget=min(budget,1.3*self.survey_budget-spent)
        if self.scheme.startswith('repair'):
            walls=any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones)
            if self.scheme in ('repairwalls','repairgap','repairgaplate') and not walls:return False
            # Let the original primary coverage proceed while a direct admissible
            # candidate remains. Switch only after no new baseline point can be reached directly.
            if aid not in self.explore_goals and any(('coverage',p) not in self.completed and ('coverage',p) not in claimed and _path_clear(zones,a,p) for p in super()._scout_points(zones,a)):
                return False
            if self.scheme=='repairdemand' and now>self.started+50:
                waiting=sum(t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('evacuation_state')=='waiting' and ('casualty',t['id']) not in claimed for t in obs['targets'])
                available=sum(r['status']=='active' and r['id'] not in self.cargo and r['id'] not in self.pending for r in crew)
                if waiting>=available+len(crew):return True
        if budget<scan_e:return False if self.scheme.startswith('repairgap') else True
        stamps=[None]*len(samples)
        for p,t in self.scanned_at.items():
            for i in cover.get(p,()):
                if stamps[i] is None or t>stamps[i]:stamps[i]=t
        reserved=set()
        for p_id,p in self.explore_goals.items():
            if p_id!=aid and p_id in self.pending:reserved.update(cover.get(p,()))
        for intent in claimed:
            if intent and intent[0] in ('coverage','repeat') and len(intent)==2:reserved.update(cover.get(intent[1],()))
        known=[tuple(t['position']) for t in obs['targets'] if t.get('kind')=='person' and t.get('position') is not None]
        density=[1.]*len(samples)
        if self.scheme in ('phasekde','efficientkde') and len(known)>=3:
            density=[sum(math.exp(-math.dist(q,p)**2/200) for p in known)/len(known) for q in samples]
            avg=sum(density)/max(1,len(density));density=[.45+.55*v/max(avg,1e-8) for v in density]
        queue=self._opportunity_slots(obs,crew,bases,claimed,remaining) if self.queue_weight else None
        cycle=max(1.,self.wave_fraction*(h-self.started));phase=self.started+math.floor((now-self.started)/cycle)*cycle
        weight=[]
        for i,q in enumerate(samples):
            age=now-stamps[i] if stamps[i] is not None else now-self.started+40
            if i in reserved or (stamps[i] is not None and (stamps[i]>=phase or age<self.minimum_gap)):weight.append(0.);continue
            back=min(math.dist(q,b) for b in bases)
            trip=min((math.dist(r['position'],q)+back)/max(.5,self.speeds.get(r['id'],1.3))+4+(10 if r['status']=='busy' or r['id'] in self.cargo else 0) for r in crew)
            rescue=max(.05,min(1.,(remaining-trip)/max(10.,remaining*.5)))
            if queue is not None:
                extra=min((delay+(math.dist(origin,q)+back)/v+4 for delay,origin,v in queue),default=math.inf)
                marginal=max(0.,min(1.,(remaining-extra)/max(10.,remaining*.45)))
                rescue*=1-self.queue_weight+self.queue_weight*marginal
            weight.append((100. if stamps[i] is None else 60.)*rescue**self.rescue_weight*density[i])
        goal=self.explore_goals.pop(aid,None)
        if goal in cover and math.dist(position,goal)<1e-8:
            if budget>=scan_e and scan_e+rate*min((v[0] for b in bases if (v:=router.path(position,b)) is not None),default=math.inf)<=energy['value']-energy['minimum'] and issue(a,'observe',self._observe_parameters(),('repeat',goal)):
                self._survey_audit.append({'agent_id':aid,'action':'observe','goal':list(goal),'basis':'arrived_scan_goal','budget_estimate':budget});self.repeat_goals.pop(aid,None);return True
        if remaining<8:return False if self.scheme.startswith('repairgap') else True
        choices=[]
        for p,indices in cover.items():
            gain=sum(weight[i] for i in indices)
            if gain<=0:continue
            if self.scheme.startswith('repairgap') and gain<40:continue
            d=math.dist(position,p);score=gain/(2+scan+d/speed)
            score/=(1+d*rate/scan_e)**self.price
            choices.append((score,gain,p))
        valid=[]
        for _,gain,p in sorted(choices,reverse=True)[:12]:
            path=router.path(position,p)
            if path is None:continue
            distance,nextp=path; duration=distance/speed+scan
            if duration+4>=remaining:continue
            back=min((v[0] for b in bases if (v:=router.path(p,b)) is not None),default=math.inf)
            if (distance+back)*rate+scan_e>energy['value']-energy['minimum'] or distance*rate+scan_e>budget:continue
            valid.append((gain/(2+duration)/(1+distance*rate/scan_e)**self.price,-distance,p,nextp))
        for value,_,p,nextp in sorted(valid,reverse=True):
            if self.min_score and value<self.min_score:break
            if math.dist(position,p)<1e-8:
                if issue(a,'observe',self._observe_parameters(),('repeat',p)):
                    self._survey_audit.append({'agent_id':aid,'action':'observe','goal':list(p),'basis':'freshness_rescue_weight','budget_estimate':budget});self.repeat_goals.pop(aid,None);return True
            elif self._motion_clear(obs,zones,a,nextp) and issue(a,'move',{'target':list(nextp)},('coverage',p)):
                self._survey_audit.append({'agent_id':aid,'action':'move','goal':list(p),'waypoint':list(nextp),'basis':'freshness_rescue_weight','budget_estimate':budget});self.explore_goals[aid]=p;self.repeat_goals.pop(aid,None);return True
        return False if self.scheme.startswith('repairgap') else True
