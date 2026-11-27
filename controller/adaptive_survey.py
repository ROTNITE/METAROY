"""Opt-in Resource Predictor with wider, demand-aware public sensing.

Uses the advertised inclusive upper bound for observe.radius, not private
sensor parameters. A larger radius may cost more in other environments;
this is an experimental mode, not a universal safety or performance claim.
"""
from __future__ import annotations
import math
from contracts.protocol import validate, ProtocolError
from controller.resource_predictor import ResourcePolicy
from controller.portable import horizon_of


class AdaptiveSurveyPolicy(ResourcePolicy):
    def _wide_radius(self, params):
        if not self._profile() or 'radius' not in params:
            return None
        schema = self.actions.get('observe', {}).get('parameters_schema', {}).get('properties', {}).get('radius', {})
        upper = schema.get('maximum')
        if type(upper) in (int, float) and math.isfinite(upper) and params['radius'] < upper <= 100:
            candidate = upper * self.public.scale
            try:
                validate(self.public.parameters('observe', {**params, 'radius': candidate}),
                         self.actions['observe']['parameters_schema'], 'INVALID_COMMAND')
            except (ProtocolError, ValueError):
                return None
            return candidate
        return None

    def _observe_parameters(self):
        params = super()._observe_parameters()
        radius = self._wide_radius(params)
        if radius is not None:
            params['radius'] = radius
        return params

    def _repeat_sector(self, observation, zones, agent, points, claimed, scouts):
        # Do not change patrol on peers without the wider-radius declaration.
        carriers = sum(a['kind']=='ground' and 'carry' in a['capabilities'] for a in observation.get('agents', ()))
        if carriers >= 2 and self.scout_enabled and self._wide_radius(super()._observe_parameters()) is not None:
            h = horizon_of(observation['mission'])
            fraction = (observation['time']-self.started)/(h-self.started) if h and h>self.started else 1.
            free = sum(a['kind']=='ground' and 'carry' in a['capabilities'] and a['status']=='active'
                       and a['id'] not in self.pending and a['id'] not in self.cargo
                       and a['communication'].get('reachable',True) for a in observation['agents'])
            waiting = sum(t.get('kind')=='person' and t.get('status')=='confirmed'
                          and t.get('evacuation_state','waiting')=='waiting'
                          and ('casualty',t['id']) not in claimed for t in observation['targets'])
            cap = 0 if fraction < .55 else 1 if fraction < .70 else 2
            if free > waiting:
                cap += 1 if fraction < .70 else 2
            if free <= waiting and fraction < .70:
                return None, 'survey_backlog'
            if sum(self.visits.values()) >= cap:
                return None, 'survey_budget'
        return super()._repeat_sector(observation,zones,agent,points,claimed,scouts)

    def decide(self, observation):
        decision = super().decide(observation)
        # Existing decision IDs and observations allow audit of actual commands.
        # Log the selected parameter, not a fabricated confirmed scan.
        radius = self._wide_radius(super()._observe_parameters())
        if radius is not None:
            for i, command in enumerate(decision['commands']):
                if command['action'] == 'observe' and command['parameters'].get('radius') == radius:
                    self.traces.append({'kind':'adaptive_survey','type':'adaptive_survey',
                        'episode_id':self.episode,'time':observation['time'],
                        'request_id':observation['request_id'],'decision_id':decision['decision_id'],
                        'command_index':i,'agent_id':command['agent_id'],'radius':radius,
                        'basis':'public_inclusive_schema_maximum'})
        return decision
