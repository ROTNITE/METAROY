"""Preserve assignment and only shorten a direct approach within declared planar service tolerance."""
import math
from controller.arrival_trial import ArrivalPolicy
from controller.rescue_exploration import ExplorationPolicy

class DockPolicy(ExplorationPolicy):
    def __init__(self,*args,dock_mode='dock-only',**kw):
        self.dock_mode=dock_mode
        mode='explore-'+dock_mode[5:] if dock_mode.startswith('dock-') and dock_mode[5:6].isdigit() else 'explore-45-2-0.09-repairwalls' if dock_mode=='dock-walls' else 'explore-45-2-0.08-repairgap' if dock_mode=='dock-gap' else 'explore-45-2-0.08-repairgaplate'
        super().__init__(*args,explore_mode=mode,**kw)
    def _target_service(self,obs,zones,a,claimed,issue):
        if self.dock_mode=='dock-only':return ArrivalPolicy._target_service(self,obs,zones,a,claimed,issue)
        return super()._target_service(obs,zones,a,claimed,issue)
    def _casualty(self,obs,zones,a,claimed,issue,wave=None):
        if (self.route_active or not self._profile() or self.capabilities.get('environment_id')!='metarescue_planar_reference' or a['kind']!='ground' or
            obs['constraints'] or obs['mission']['constraints'] or obs['mission'].get('directives') or obs['observation_quality']['contradiction_count'] or self.program or a['constraints'] or
            any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones)):
            return super()._casualty(obs,zones,a,claimed,issue,wave)
        def short(agent,action,params,intent):
            if action=='move' and intent and intent[0]=='casualty' and 'target' in params:
                origin=agent['position'];target=params['target'];d=math.dist(origin,target)
                if d>1.:
                    goal=[v+(u-v)*.8/d for u,v in zip(origin,target)]
                    if self._motion_clear(obs,zones,agent,goal):return issue(agent,action,{'target':goal},intent)
            return issue(agent,action,params,intent)
        return super()._casualty(obs,zones,a,claimed,short,wave)
