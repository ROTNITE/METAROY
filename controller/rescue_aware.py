"""Frozen context-adaptive research candidate. Public planar profile only.
Default balanced profile keeps ordinary multi-scout and low-energy/no-wall
coverage, uses repair for multi-scout obstacles, and energy-priced patrol
for a single scout. High-value profile is an explicit alternative, not a
hidden-seed selector. All rescue logic is inherited from Arrival Value.
"""
from controller.dock_trial import DockPolicy

class RescueAwarePolicy(DockPolicy):
    def __init__(self,*args,profile='balanced',**kw):
        if profile not in ('balanced','high-value'):raise ValueError(profile)
        self.rescue_aware_profile=profile
        super().__init__(*args,dock_mode='dock-only',**kw)
    def decide(self,obs):
        if self.episode!=obs['episode_id']:
            wall=any(z['type'] in ('obstacle','restricted','hazard') for z in obs['zones'])
            scouts=sum(r['kind']=='aerial' and 'sense' in r['capabilities'] for r in obs['agents'])
            low=any(r['kind']=='ground' and 'carry' in r['capabilities'] and (e:=self._usable_energy(r)) is not None and e<.25 for r in obs['agents'])
            # Select using only public initial conditions, never the seed,
            # family label, future arrivals, or offline ceiling.
            if not wall and (low or scouts!=1):
                self.dock_mode='dock-only';self.scheme='full'
            elif self.rescue_aware_profile=='high-value':
                self.dock_mode='dock-adaptive';self.scheme='phasecheap';self.min_age=55.;self.rescue_weight=1.;self.survey_budget=.12
            elif wall and scouts>1:
                self.dock_mode='dock-adaptive';self.scheme='repairgap';self.min_age=45.;self.rescue_weight=2.;self.survey_budget=.08
            else:
                self.dock_mode='dock-adaptive';self.scheme='efficient';self.min_age=45.;self.rescue_weight=1.;self.survey_budget=.10 if wall else .09
        return super().decide(obs)
