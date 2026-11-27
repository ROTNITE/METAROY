"""Execute the public Core profile using declared actions and geometry."""
from __future__ import annotations

import math
from copy import deepcopy
import hashlib

from contracts.expressions import Expression, ExpressionError
from contracts.protocol import OPERATIONS, ProtocolError, validate
from controller.baselines import FixedWorkflow
from controller.assignment import minimum_assignment, AssignmentError
from controller.decisions import decision
from controller.declared_scenarios import SCENARIO_SEMANTICS, choose_declared
from controller.navigation import Box, point
from controller.public_geometry import geometry as parse_geometry, segments
from controller.public_patrol import PublicPatrolEstimates
from controller.net65 import Candidate, MAX_CANDIDATES, choose_sector, due, horizon_of, scan_ages
from controller.public_core import PATROL_ESTIMATES, PublicCore
from controller.scenarios import COVERAGE, PRIORITY, SAFETY, configuration, risk_of
from controller.scenario_program import DONE, GRAMMAR, ProgramError, ScenarioProgram, phase


_MEDIA = {
    'ground': 'land', 'aerial': 'air', 'underground': 'underground',
    'surface_water': 'water', 'underwater': 'water',
}
_TERMINAL = {'completed', 'failed', 'cancelled'}
_ROUTE_RULES = {'position_bounds', 'zone_access', 'domain_access', 'restricted_zone',
                'minimum_separation', 'collision', 'risk_limit'}
_RESOURCE_RULES = {'resource_minimum', 'minimum_energy_reserve', 'resource_reserve'}


def _box(zone):
    geometry = zone['geometry']
    try:
        return parse_geometry(geometry)
    except (KeyError, ValueError):
        return None


def _allowed(zone, kind):
    access = zone['access']
    kinds = access.get('allowed_kinds', access.get('allowed_agent_kinds'))
    if kinds == [] and access.get('default') == 'allow':
        return True
    return kind in kinds if isinstance(kinds, list) else access.get('default') != 'deny'


def _path_clear(zones, agent, goal, *, public_media=False):
    kind = agent['kind']
    start = agent['position']
    intervals = []
    for zone, box in zones:
        if zone['type'] in ('obstacle', 'restricted', 'hazard'):
            explicit = zone['access'].get('allowed_kinds', zone['access'].get('allowed_agent_kinds'))
            if zone['type'] != 'obstacle' and isinstance(explicit, list) and kind in explicit:
                continue
            if box is None or box.interval(start, goal) is not None:
                return False
        if box and zone['type'] == _MEDIA.get(kind) and _allowed(zone, kind) and (
                zone['risk'] == 0 or public_media and zone['risk'] < 1.) and not zone['constraints']:
            support_start, support_goal = start, goal
            if kind == 'ground':
                support_start = (*start[:2], box.maximum[2])
                support_goal = (*goal[:2], box.maximum[2])
            intervals.extend(segments(box,support_start,support_goal))
    covered = 0.
    for lower, upper in sorted(intervals):
        if lower > covered + 1e-9:
            break
        covered = max(upper, covered)
    return covered >= 1. - 1e-9


def _path_margin(start, end, position):
    delta = tuple(b-a for a,b in zip(start,end))
    length_sq = sum(d*d for d in delta)
    if not length_sq:
        return math.dist(start, position)
    along = sum(d*(p-a) for d,p,a in zip(delta,position,start)) / length_sq
    return math.dist(position, tuple(a+min(1.,max(0.,along))*d for a,d in zip(start,delta)))


class PortablePolicy:
    """Use the mandatory wire profile when the scene-specific adapter is absent."""

    def __init__(self, capabilities, requested_policy, *, scout_patrol='core', public=None, flow_dispatch=False):
        self.public = public or PublicCore(capabilities)
        self.actions = self.public.actions
        self.scenarios = capabilities.get('supported_scenarios', {})
        self.requested_policy = requested_policy
        self.operations = set(capabilities.get('supported_operations', capabilities.get('operations', OPERATIONS))) & set(OPERATIONS)
        self.capabilities = capabilities
        self.scout_patrol = scout_patrol
        self.flow_dispatch = flow_dispatch
        self.patrol_estimates = PublicPatrolEstimates(capabilities,self.public)
        self._reset(None)

    def _reset(self, episode):
        self.episode = episode
        self.started = None
        self.previous = None
        self.proposed = None
        self.pending = {}
        self.completed = set()
        self.cooldowns = {}
        self.unavailable_scenarios = set()
        self.program = None
        self.program_declaration = None
        self.program_parameters = None
        self.program_objective = None
        self.program_announced = False
        self.failed_programs = set()
        self.stopping = False
        self.scanned_at = {}
        self.repeat_goals = {}
        self.cargo = {}
        self.net65_repeats = 0
        self.patrol_estimates.reset()
        self.traces = []

    def drain_traces(self):
        traces, self.traces = self.traces, []
        return traces

    def patrol_summary(self, now):
        return {'mode': self.scout_patrol, 'net65_repeats': self.net65_repeats, **scan_ages(self.scanned_at, now)}

    def _supports(self, agent, action, parameters):
        declared = self.actions.get(action)
        if (not declared or not FixedWorkflow.available(agent, action) or
                not set(declared.get('required_capabilities', [])) <= set(agent['capabilities'])):
            return False
        schema = declared.get('parameters_schema')
        if not isinstance(schema, dict):
            return False
        try:
            validate(self.public.command_parameters(action, parameters), schema, 'INVALID_COMMAND')
        except (ProtocolError, ValueError):
            return False
        return True

    def _constraint_checks(self, observation, agent):
        # Public predicates describe the peer's wire coordinate system.
        if not self.public.identity:
            observation = self.raw_observation
            agent = self.raw_agents[agent['id']]
        checks = []
        constraints = [*observation['constraints'], *observation['mission']['constraints'], *agent['constraints']]
        for item in constraints:
            if item.get('severity', 'hard') != 'hard':
                continue
            scope = item.get('scope', {})
            kind = scope.get('kind', 'global')
            if kind == 'agent' and scope.get('agent_id', scope.get('id', agent['id'])) != agent['id']:
                continue
            rule = item.get('type')
            if rule is None and item.get('id') == 'minimum_energy_reserve':
                rule = 'minimum_energy_reserve'
            if self.public.short_actions and rule in _RESOURCE_RULES:
                resource = agent['resources'].get(item.get('resource', 'energy'))
                minimum = item.get('minimum')
                passed = resource is not None and type(minimum) in (int,float) and resource['value'] > minimum
                checks.append({'constraint_id': item.get('id', rule), 'agent_id': agent['id'],
                               'status': 'passed' if passed else 'failed'})
                continue
            if self.public.short_actions and rule in _ROUTE_RULES and 'predicate' not in item:
                # Movement-specific restrictions are enforced against the
                # proposed segment when binding the command.
                continue
            public_scope = 'observation' if kind == 'global' else kind
            context = {'observation': observation, 'mission': observation['mission'], 'agent': agent}.get(public_scope)
            passed = False
            if context is not None:
                try:
                    passed = Expression(item.get('predicate')).evaluate(context, scope=public_scope) is True
                except (ExpressionError, TypeError):
                    pass
            checks.append({'constraint_id': item.get('id', 'undeclared_constraint'), 'agent_id': agent['id'],
                           'status': 'passed' if passed else 'failed'})
        return checks

    def _motion_clear(self, observation, zones, agent, goal):
        if not _path_clear(zones, agent, goal, public_media=self.public.short_actions):
            return False
        if not self.public.short_actions:
            return True
        rules = (*observation['constraints'], *observation['mission']['constraints'], *agent['constraints'])
        if any(not _allowed(zone, agent['kind']) and box and box.interval(agent['position'], goal) is not None
               for zone, box in zones):
            return False
        for rule in rules:
            kind = rule.get('type')
            if kind == 'position_bounds':
                try:
                    bounds = Box.parse({**rule, 'kind': 'box'})
                except (ValueError, KeyError):
                    return False
                if bounds.interval(goal, goal) is None:
                    return False
            if kind == 'risk_limit':
                ceiling = rule.get('maximum_risk')
                if type(ceiling) not in (int, float) or any(zone['risk'] > ceiling and (
                        not box or box.interval(agent['position'], goal) is not None) for zone, box in zones):
                    return False
            if kind in ('minimum_separation', 'collision'):
                margin = rule.get('distance', 1.5)
                if type(margin) not in (int, float) or any(
                        peer['id'] != agent['id'] and peer['status'] not in ('failed','offline') and peer['position'] is not None
                        and _path_margin(agent['position'], goal, peer['position']) < margin
                        for peer in observation['agents']):
                    return False
        return True

    def _candidate(self, observation, agents, identifier, target_id, preferred=None):
        declaration = self.scenarios.get(identifier)
        if declaration is None or identifier in self.unavailable_scenarios:
            return None
        schema = declaration.get('parameter_schema', declaration.get('parameters_schema', {}))
        parameters = {'target_id': target_id} if identifier == PRIORITY and target_id and (
            'target_id' in schema.get('properties', {})) else {}
        roles = {}
        feasible = True
        preferred = preferred if preferred is not None else self.previous['role_assignments'] if self.previous else {}
        for role in declaration.get('roles', ()):
            required = set(role.get('required_capabilities', ()))
            compatible = [a for a in agents if a['id'] not in roles and required <= set(a['capabilities'])]
            compatible.sort(key=lambda a: preferred.get(a['id']) != role['id'])
            minimum = role.get('minimum', 0)
            maximum = role.get('maximum', len(agents))
            if (type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum or
                    len(compatible) < minimum):
                feasible = False
                continue
            for owner in compatible[:min(maximum, max(1, minimum))]:
                roles[owner['id']] = role['id']
        try:
            validate(parameters, schema, 'INVALID_SCENARIO')
            predicates = declaration.get('applicability', {}).get('entry', ())
            applicable = feasible and all(Expression(node).evaluate(observation) is True for node in predicates)
        except (ProtocolError, ExpressionError, TypeError, ValueError):
            applicable = False
        if declaration.get('roles') and not roles:
            applicable = False
        report = {'scenario_id': identifier, 'applicable': applicable, 'utility': float(applicable),
                  'risk': risk_of(observation), 'selected': False,
                  'rejected_reason_codes': [] if applicable else ['core_preconditions_unknown_or_false']}
        return declaration, parameters, roles, report

    def _scenario(self, observation, agents, desired, target_id):
        old = self.previous['selected_configuration']['scenario_ids'] if self.previous else []
        if self.requested_policy == 'local_reactive' or self.requested_policy == 'fixed_workflow' and old:
            return 'keep', None, None, []
        if SCENARIO_SEMANTICS in self.public.extensions and self.requested_policy != 'fixed_workflow':
            return choose_declared(self,observation,agents,old)
        if self.requested_policy == 'fixed_workflow':
            desired = COVERAGE
        evaluated = {identifier: candidate for identifier in dict.fromkeys((desired, COVERAGE))
                     if (candidate := self._candidate(observation, agents, identifier, target_id)) is not None}
        candidates = [value[3] for value in evaluated.values()]
        if desired == PRIORITY and 'compose' in self.operations and set(evaluated) == {COVERAGE, PRIORITY}:
            coverage, priority = evaluated[COVERAGE], evaluated[PRIORITY]
            # A multi-capability scout must not steal the shared relay role
            # from an existing compatible owner when both scenarios run.
            priority = self._candidate(observation, agents, PRIORITY, target_id, coverage[2])
            evaluated[PRIORITY] = priority
            candidates = [value[3] for value in evaluated.values()]
            compatible = all(isinstance(source[0].get('compatible_with'), list) and
                             other in source[0]['compatible_with'] for source, other in
                             ((coverage, PRIORITY), (priority, COVERAGE)))
            roles = {**coverage[2], **priority[2]}
            no_conflict = all(priority[2].get(aid, role) == role for aid, role in coverage[2].items())
            # Composition must retain an independent scouting participant.
            independent = set(coverage[2]) - set(priority[2])
            if compatible and no_conflict and independent and coverage[3]['applicable'] and priority[3]['applicable']:
                parameters = {'by_scenario': {COVERAGE: coverage[1], PRIORITY: priority[1]}}
                for candidate in candidates: candidate['selected'] = True
                ids = [COVERAGE, PRIORITY]
                if old == ids:
                    changed = parameters != self.previous['selected_configuration']['parameters']
                    return ('modify', configuration(ids, parameters), roles, candidates) if changed else ('keep', None, None, candidates)
                return 'compose', configuration(ids, parameters), roles, candidates
        for identifier, (_, parameters, roles, report) in evaluated.items():
            if report['applicable']:
                report['selected'] = True
                if old == [identifier]:
                    if parameters != self.previous['selected_configuration']['parameters']:
                        return 'modify', configuration([identifier], parameters), None, candidates
                    return 'keep', None, None, candidates
                return ('switch' if old else 'select'), configuration([identifier], parameters), roles, candidates
        return 'keep', None, None, candidates

    def _start_public_program(self, observation, agents, tasks):
        """Synthesize a fresh verification dependency for uncertain priority aid."""
        if (not tasks or self.program or 'synthesize' not in self.operations or
                self.requested_policy in ('fixed_workflow', 'local_reactive') or risk_of(observation) < .2):
            return
        termination = observation['mission']['termination']
        horizon = termination.get('horizon', termination.get('time_limit'))
        if horizon is not None and (type(horizon) not in (int, float) or not math.isfinite(horizon) or
                                    horizon - observation['time'] < 60):
            return
        # Core leaves termination's vocabulary open. An absent global horizon
        # does not remove the program's own 60-second execution deadline.
        for objective, goal, aid in tasks:
            if objective['id'] in self.failed_programs or ('deliver', objective['id']) in self.completed:
                continue
            if objective.get('priority', 0) < .9:
                continue
            candidates = [a for a in agents if a['id'] not in self.pending and a['position'] is not None and
                          not any(c['status'] == 'failed' for c in self._constraint_checks(observation, a)) and
                          (not a['resources'].get('energy') or
                           a['resources']['energy']['value'] > a['resources']['energy']['minimum'])]
            inspectors = [a for a in candidates if math.dist(a['position'], goal) <= 10 and
                          self._supports(a, 'inspect', {'target_id': aid['target_id']})]
            carriers = [a for a in candidates if self._supports(a, 'deliver', aid) and
                        set(objective.get('required_capabilities', ())) <= set(a['capabilities']) and
                        (not a['resources'].get(aid['resource_id']) or
                         a['resources'][aid['resource_id']]['value'] > a['resources'][aid['resource_id']]['minimum']) and
                        self._supports(a, 'move', {'target': list(goal)}) and
                        _path_clear([(z, _box(z)) for z in observation['zones']], a, goal)]
            pair = next(((i, c) for i in inspectors for c in carriers if i['id'] != c['id']), None)
            if pair is None:
                continue
            inspector, carrier = pair
            parameters = {**aid, 'target_position': list(goal)}
            roles = {inspector['id']: 'verifier', carrier['id']: 'executor'}
            def command(role, action, **params):
                return {'role': role, 'action': action, 'parameters': params}
            def binding(name): return {'parameter': name}
            confirmed = {'op': 'any', 'items': {'path': 'observation.targets'}, 'var': 'target', 'where':
                {'op': 'and', 'args': [
                    {'op': 'eq', 'left': {'path': '$target.id'}, 'right': {'path': 'parameters.target_id'}},
                    {'op': 'eq', 'left': {'path': '$target.status'}, 'right': {'value': 'confirmed'}},
                    {'op': 'ge', 'left': {'path': '$target.confidence'}, 'right': {'value': .5}}]}}
            rules = {'grammar': GRAMMAR, 'initial_state': 'verify', 'deadline_seconds': 60, 'states': [
                phase('verify', [command('verifier', 'inspect', target_id=binding('target_id'))], 'approach', 15,
                      condition={'op': 'and', 'args': [deepcopy(DONE), confirmed]}),
                phase('approach', [command('executor', 'move', target=binding('target_position'))], 'deliver', 30),
                phase('deliver', [command('executor', 'deliver', target_id=binding('target_id'),
                      resource_id=binding('resource_id'), amount=binding('amount'))], 'done', 15),
                {'id': 'done', 'outcome': 'success'}, {'id': 'fallback', 'outcome': 'safe_degradation'}]}
            identity = hashlib.sha256((objective['id'] + '\0' + aid['target_id']).encode()).hexdigest()[:16]
            declaration = {'id': 'public_verified_delivery_' + identity, 'version': '1.0',
                'title': 'Fresh inspection before priority aid delivery',
                'game_model': {'class': 'team', 'players': ['verifier', 'executor'],
                    'information_structure': 'partial', 'strategies': {'verifier': ['inspect'], 'executor': ['approach', 'deliver']},
                    'information_sources': ['public_observation', 'correlated_command_results'],
                    'utilities': {'verifier': 'confirmed_target', 'executor': 'confirmed_priority_delivery'},
                    'uncertainty': ['stale_data', 'missing_ack'], 'stability_criterion': 'feasible_delivery_after_fresh_verification'},
                'applicability': {'entry': [], 'continuation': [], 'exit': [], 'emergency_exit': []},
                'roles': [{'id': role, 'minimum': 1, 'maximum': 1, 'required_capabilities': sorted(required)}
                    for role, required in [('verifier', set(self.actions['inspect'].get('required_capabilities', []))),
                                           ('executor', set(self.actions['deliver'].get('required_capabilities', [])) |
                                            set(self.actions['move'].get('required_capabilities', [])))]],
                'objectives': [deepcopy(objective)], 'constraints': deepcopy(observation['mission']['constraints']),
                'allowed_actions': ['inspect', 'move', 'deliver'], 'transition_targets': [COVERAGE, SAFETY],
                'metrics': ['confirmed_delivery', 'verification_delay'],
                'parameter_schema': {'type': 'object', 'properties': {
                    'target_id': {'type': 'string'}, 'resource_id': {'type': 'string'}, 'amount': {'type': 'integer', 'minimum': 1},
                    'target_position': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'number'}}},
                    'required': ['target_id', 'resource_id', 'amount', 'target_position'], 'additionalProperties': False},
                'execution_rules': rules}
            if declaration['id'] in self.scenarios:
                continue
            known = [s['execution_rules'] for s in self.scenarios.values()
                     if isinstance(s.get('execution_rules'), dict) and s['execution_rules'].get('grammar') == GRAMMAR]
            try:
                self.program = ScenarioProgram(declaration, parameters, roles, self.capabilities, known_rules=known)
            except (ProgramError, ProtocolError):
                continue
            self.program.started = self.program.entered = observation['time']
            self.program_declaration, self.program_parameters = declaration, parameters
            self.program_objective, self.program_announced = objective['id'], False
            return

    def _scout_points(self, zones, agent):
        for zone, box in zones:
            if (box is None or zone['type'] != _MEDIA.get(agent['kind']) or not _allowed(zone, agent['kind']) or
                    (zone['risk'] and not self.public.short_actions) or zone.get('constraints')):
                continue
            altitude = agent['position'][2] if agent['kind'] in ('ground','surface_water') and self.public.short_actions else (
                box.maximum[2] if agent['kind'] == 'ground' else (box.minimum[2] + box.maximum[2]) / 2)
            for fx in (.2, .5, .8):
                for fy in (.2, .5, .8):
                    yield (box.minimum[0] + fx * (box.maximum[0] - box.minimum[0]),
                           box.minimum[1] + fy * (box.maximum[1] - box.minimum[1]), altitude)

    def _patrol_due(self, now, start, horizon, coverage_complete):
        return due(now, start, horizon, coverage_complete)

    def _repeat_sector(self, observation, zones, agent, points, claimed, scouts):
        values,source = self.patrol_estimates.values(agent,points)
        if values is None:
            return None, 'net65_missing_public_estimates'
        energy = agent['resources'].get('energy')
        stations = [point(row['position']) for row in observation['infrastructure']
                    if row['kind'] == 'base_station' and row['status'] == 'active']
        if (not energy or not stations or not self._supports(agent, 'observe', self._observe_parameters()) or
                any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in values)):
            return None, 'net65_missing_public_estimates'

        speed, scan_s, move_energy, scan_energy, side = values
        now = observation['time']
        horizon = horizon_of(observation['mission'])
        cycle = len(points) * (scan_s + side / speed) / max(1, len(scouts))
        start = point(agent['position'])
        available = energy['value'] - energy['minimum']

        def estimate(origin, goal):
            if not _path_clear(zones, {**agent, 'position': origin}, goal):
                return None
            distance = math.dist(origin, goal)
            return distance / speed, distance * move_energy

        def return_energy(goal):
            trips = (estimate(goal, station) for station in stations)
            return min((trip[1] for trip in trips if trip is not None), default=math.inf)

        candidates = []
        for goal in points:
            stamp = self.scanned_at[goal]
            if ('repeat', goal) in claimed or now - stamp < max(6., .35 * cycle):
                continue
            distance = math.dist(start, goal)
            earliest_peer = min((math.dist(peer['position'], goal) for peer in scouts if peer['id'] != agent['id']),
                                default=math.inf)
            ownership = 1. / (1. + (distance - earliest_peer) / side) if earliest_peer + 2 * speed < distance else 1.
            candidates.append(Candidate(goal, goal, stamp, ownership=ownership))
        candidates.sort(key=lambda c: (c.scanned_at, math.dist(start, c.position), c.sector))
        goal = choose_sector(candidates[:MAX_CANDIDATES], start=start, now=now, horizon=horizon, cycle=cycle,
                             scan_s=scan_s, scan_energy=scan_energy, energy_scale=side * move_energy,
                             estimate=estimate, available_energy=available, return_energy=return_energy)
        if goal is not None and source != 'declared_patrol_estimates':
            self.traces.append({'type':'net65_public_estimate_used','provider':source,'episode_id':self.episode,
                                'observation_id':observation['request_id'],'agent_id':agent['id'],'goal':list(goal)})
        return goal, 'net65_repeat_patrol' if goal is not None else 'net65_wait'


    def _continue_repeat(self, observation, zones, agent, goal, points, issue):
        if goal not in points or not self._motion_clear(observation,zones,agent,goal):
            return False
        values,source=self.patrol_estimates.values(agent,points)
        if not values or source=='declared_patrol_estimates':
            return False
        speed,scan_seconds,per_meter,scan_energy,_=values
        start=agent['position']
        distance=math.dist(start,goal)
        energy=agent['resources'].get('energy')
        remaining=horizon_of(observation['mission'])-observation['time']
        if not energy or distance/speed+scan_seconds>=remaining:
            return False
        returns=[math.dist(goal,station['position']) for station in observation['infrastructure']
                 if station['status']=='active' and station['kind']=='base_station'
                 and _path_clear(zones,{**agent,'position':goal},station['position'])]
        if not returns or (distance+min(returns))*per_meter+scan_energy>energy['value']-energy['minimum']:
            return False
        return issue(agent,'move',{'target':list(goal)},('repeat',goal))

    def _observe_parameters(self):
        schema = self.actions.get('observe', {}).get('parameters_schema', {})
        radius = schema.get('properties', {}).get('radius', {})
        if self.public.short_actions and not radius:
            return {}
        limit = radius.get('maximum')
        return {'radius': min(10., limit * self.public.scale) if type(limit) in (int, float) else 10.}

    def _target_service(self, observation, zones, agent, claimed, issue):
        if not self.public.short_actions or agent['kind'] not in _MEDIA or observation['observation_quality']['contradiction_count']:
            return False
        targets = sorted((target for target in observation['targets'] if target.get('position') is not None and
                          target['status'] in ('detected','suspected','confirmed','delivered') and
                          agent['kind'] in target.get('accessible_kinds', [agent['kind']])),
                         key=lambda target: (-target.get('priority', 0), math.dist(agent['position'], target['position']), target['id']))
        for target in targets:
            params = {'target_id': target['id']}
            choices = ('confirm',) if target['status'] in ('detected','suspected') else (
                ('rescue',) if target['status'] == 'delivered' else ('rescue','deliver'))
            action = next((name for name in choices if self._supports(agent,name,params)),None)
            if action is None:
                continue
            intent = (action, target['id'])
            if intent in claimed or intent in self.completed:
                continue
            goal = point(target['position'])
            if math.dist(agent['position'], goal) <= (8. if action == 'confirm' else 4.):
                if issue(agent, action, params, intent):
                    return True
            else:
                if agent['kind'] == 'aerial':
                    goal = (*goal[:2],max(2.,goal[2]))
                if self._motion_clear(observation, zones, agent, goal) and issue(agent,'move',{'target':list(goal)},intent):
                    return True
        return False

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
        if not (2<=len(crew)<=32 and 1<=len(targets)<=64 and stations):return {}
        edges={}
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
                edges[i,j]=(max(0,round(p*1000)),round((dist+min(back))*1000))
        if not edges:return {}
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
                                                'priority_milli':p,'round_trip_mm':d}
                                               for (i,j),(p,d) in edges.items()]})
        return result

    def _casualty(self, observation, zones, agent, claimed, issue, wave=None):
        identifier = agent['id']
        if identifier in self.cargo:
            intent = ('casualty', self.cargo[identifier])
            stations = sorted((row for row in observation['infrastructure'] if row['kind'] == 'base_station'
                               and row['status'] == 'active'),
                              key=lambda row: (math.dist(agent['position'], row['position']), row['id']))
            for station in stations:
                goal = station['position']
                if math.dist(agent['position'], goal) <= 1.:
                    if issue(agent, 'handover', {'station_id': station['id']}, intent):
                        break
                elif _path_clear(zones, agent, goal) and issue(agent, 'move', {'target': goal}, intent):
                    break
            return True

        if 'pickup' not in self.actions or 'handover' not in self.actions or 'carry' not in agent['capabilities']:
            return False
        if observation['observation_quality']['contradiction_count']:
            return False
        targets = sorted((row for row in observation['targets'] if row.get('kind') == 'person'
                          and row.get('status') == 'confirmed' and row.get('position') is not None
                          and row.get('evacuation_state', 'waiting') == 'waiting'),
                         key=lambda row: (-row.get('priority', 0), math.dist(agent['position'], row['position']), row['id']))
        if wave and identifier in wave:
            targets = [t for t in targets if t['id']==wave[identifier]]
        for target in targets:
            intent = ('casualty', target['id'])
            goal = point(target['position'])
            if intent in claimed or intent in self.completed:
                continue
            if math.dist(agent['position'], goal) <= 1.:
                if issue(agent, 'pickup', {'target_id': target['id']}, intent):
                    return True
            elif _path_clear(zones, agent, goal) and issue(agent, 'move', {'target': list(goal)}, intent):
                return True
        return False

    def _delivery_tasks(self, observation):
        targets = {t['id']: t for t in observation['targets'] if t.get('status') == 'confirmed' and
                   t.get('position') and t.get('confidence', 0.) >= .5}
        if observation['observation_quality']['contradiction_count']:
            return []
        tasks = []
        for obj in observation['mission']['objectives']:
            target = targets.get(obj.get('target_id'))
            success = obj.get('success_conditions', {})
            resource, amount = success.get('resource_id'), success.get('amount_at_least')
            if (obj.get('type') != 'deliver' or obj.get('status') not in ('active', 'pending') or target is None or
                    not isinstance(resource, str) or type(amount) is not int or amount <= 0):
                continue
            try:
                position = point(target['position'])
            except ValueError:
                continue
            tasks.append((obj, position, {'target_id': target['id'], 'resource_id': resource, 'amount': amount}))
        return sorted(tasks, key=lambda item: (-item[0].get('priority', 0), item[1], item[0]['id']))

    def _clear_program(self):
        self.program = self.program_declaration = self.program_parameters = self.program_objective = None
        self.program_announced = False

    def _program_commands(self, observation, orders):
        if not self.program_announced or self.program.reserved_agents & orders.keys():
            return []
        # Check every phase again against current public constraints, resources
        # and action declarations, including while waiting for its acknowledgement.
        actors = {a['id']: a for a in observation['agents']}
        for aid in self.program.reserved_agents:
            agent = actors.get(aid)
            if agent is None or agent['status'] in ('failed', 'offline'):
                self.program.outcome = 'safe_degradation'
                return []
            energy = agent['resources'].get('energy')
            if (energy and energy['value'] <= energy['minimum'] or
                    any(c['status'] == 'failed' for c in self._constraint_checks(observation, agent))):
                self.program.outcome = 'safe_degradation'
                return []

        def route(command, agent):
            return command if (self._supports(agent, command['action'], command['parameters']) and
                _path_clear([(z, _box(z)) for z in observation['zones']], agent, command['parameters']['target'])) else None

        def authorize(command, agent):
            parameters = command['parameters']
            if not self._supports(agent, command['action'], parameters):
                return False
            if command['action'] == 'deliver':
                resource = agent['resources'].get(parameters['resource_id'])
                if resource and resource['value'] <= resource['minimum']:
                    return False
            signature = (agent['id'], command['action'], repr(parameters))
            return self.cooldowns.get(signature, -1.) <= observation['time']

        # Configuration revision is an optional extension. Public role delivery
        # and decision/index-correlated command ACKs remain mandatory here.
        return self.program.propose(observation, None, route=route, authorize=authorize)

    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self._reset(observation['episode_id'])
            self.started = observation['time']
        if not self.public.identity:
            self.raw_observation = observation
            self.raw_agents = {row['id']: row for row in observation['agents']}
            observation = self.public.view(observation)
        elif self.public.semantic_ids:
            observation = self.public.view(observation)
        if self.stopping:
            core_view = {key: observation[key] for key in ('episode_id', 'time', 'request_id')}
            request = decision(core_view, previous=self.previous)
            request['explanation']['reason_codes'] = ['core_public_profile', 'waiting_for_safe_stop']
            self.proposed = (request, [])
            return request
        participants = sorted((a for a in observation['agents'] if a['status'] in ('active', 'degraded') and
                               a['communication'].get('reachable', a['communication'].get('connected', True)) is not False),
                              key=lambda a: (a['kind'], tuple(a['position'] or ()), a['id']))
        agents = [a for a in participants if a['position'] is not None]
        zones = [(z, _box(z)) for z in observation['zones']]
        tasks = self._delivery_tasks(observation)
        orders = FixedWorkflow.hold_orders(observation)
        suspects = [t for t in observation['targets'] if t.get('status') == 'suspected' and t.get('position')]
        commands, intents, checks = [], [], []
        patrol_reasons = set()
        now = observation['time']
        moving_ground = set()
        claimed = {item[2] for item in self.pending.values()}
        claimed.update(('casualty', target) for target in self.cargo.values())
        claimed.update(('repeat', goal) for goal in self.repeat_goals.values())
        scouts = [agent for agent in agents if agent['kind'] == 'aerial' and 'sense' in agent['capabilities']]
        if not orders:
            self._start_public_program(observation, agents, tasks)
        program_commands = self._program_commands(observation, orders) if self.program else []
        program_failed = False
        if self.program and self.program.outcome:
            program_failed = self.program.outcome != 'success'
            if program_failed:
                self.failed_programs.add(self.program_objective)
            else:
                self.completed.add(('deliver', self.program_objective))
            self._clear_program()

        def issue(agent, action, params, intent):
            signature = (agent['id'], action, repr(params))
            if self.cooldowns.get(signature, -1.) > now or not self._supports(agent, action, params):
                return False
            params = self.public.command_parameters(action, params)
            if self.public.short_actions and action == 'move' and agent['kind'] in ('ground','surface_water'):
                # Older synchronous peers do not publish the swept paths of
                # actors within their tick. Serialize same-medium movement.
                if agent['kind'] in moving_ground:
                    return False
                moving_ground.add(agent['kind'])
            commands.append({'agent_id': agent['id'], 'action': self.public.wire_action(action), 'parameters': params})
            intents.append((intent, signature))
            if intent is not None:
                claimed.add(intent)
            return True

        wave = self._flow_wave(observation,zones,agents,claimed,orders) if not program_failed else {}
        for agent in agents:
            identifier = agent['id']
            if identifier in self.pending:
                continue
            if identifier in orders:
                intent = ('hold', orders[identifier]['id'])
                if intent not in self.completed:
                    issue(agent, 'hold', {}, intent)
                continue
            if self.program or program_failed:
                continue
            energy = agent['resources'].get('energy')
            checks_for_agent = self._constraint_checks(observation, agent)
            checks.extend(checks_for_agent)
            if (energy and energy['value'] <= energy['minimum'] or
                    any(c['status'] == 'failed' for c in checks_for_agent)):
                continue
            if (self._casualty(observation, zones, agent, claimed, issue, wave) or
                    self._target_service(observation, zones, agent, claimed, issue)):
                continue
            for obj, goal, params in tasks:
                intent = ('deliver', obj['id'])
                resource = agent['resources'].get(params['resource_id'])
                if (intent in self.completed or intent in claimed or not self._supports(agent, 'deliver', params) or
                        not set(obj.get('required_capabilities', ())) <= set(agent['capabilities']) or
                        obj.get('deadline') is not None and obj['deadline'] < now or
                        resource and resource['value'] <= resource['minimum']):
                    continue
                if math.dist(agent['position'], goal) <= 1.:
                    if issue(agent, 'deliver', params, intent):
                        break
                elif _path_clear(zones, agent, goal) and issue(agent, 'move', {'target': list(goal)}, intent):
                    break
            else:
                if 'sense' not in agent['capabilities']:
                    continue
                nearby = []
                for target in suspects:
                    try:
                        distance = math.dist(agent['position'], point(target['position']))
                    except ValueError:
                        continue
                    if distance <= 10.:
                        nearby.append((distance, target['id'], target.get('observed_at', now)))
                if any(issue(agent, 'inspect', {'target_id': target_id}, ('inspect', target_id, at))
                       for _, target_id, at in sorted(nearby) if ('inspect', target_id, at) not in self.completed):
                    continue
                points = sorted(set(self._scout_points(zones, agent)),
                                key=lambda p: (math.dist(agent['position'], p), p))
                for goal in points:
                    intent = ('coverage', goal)
                    if intent in self.completed or intent in claimed:
                        continue
                    if math.dist(agent['position'], goal) <= 1.:
                        if issue(agent, 'observe', self._observe_parameters(), intent):
                            break
                    elif self._motion_clear(observation, zones, agent, goal) and issue(agent, 'move', {'target': list(goal)}, intent):
                        break
                else:
                    if (self.scout_patrol == 'net65' and agent['kind'] == 'aerial' and points and
                            self._patrol_due(now, self.started, horizon_of(observation['mission']),
                                all(goal in self.scanned_at for goal in points))):
                        goal = self.repeat_goals.get(identifier)
                        if goal is not None and math.dist(agent['position'], goal) <= 1.:
                            issue(agent, 'observe', self._observe_parameters(), ('repeat', goal))
                            patrol_reasons.add('net65_confirm_repeat')
                        elif goal is not None and self._continue_repeat(observation,zones,agent,goal,points,issue):
                            patrol_reasons.add('net65_committed_repeat')
                        else:
                            self.repeat_goals.pop(identifier, None)
                            goal, reason = self._repeat_sector(observation, zones, agent, points, claimed, scouts)
                            patrol_reasons.add(reason)
                            if goal is not None:
                                if math.dist(agent['position'], goal) <= 1.:
                                    issue(agent, 'observe', self._observe_parameters(), ('repeat', goal))
                                else:
                                    issue(agent, 'move', {'target': list(goal)}, ('repeat', goal))
        desired = PRIORITY if tasks else SAFETY if orders or observation['network']['partition_count'] else COVERAGE
        target_id = tasks[0][2]['target_id'] if tasks else None
        operation, selected, roles, candidates = self._scenario(observation, participants, desired, target_id)
        if self.program:
            operation, selected, roles = ('keep', None, None) if self.program_announced else (
                'synthesize', configuration([self.program_declaration['id']], self.program_parameters,
                                           self.program_declaration), self.program.assignments)
            for command in program_commands:
                agent = next(a for a in observation['agents'] if a['id'] == command['agent_id'])
                issue(agent, command['action'], command['parameters'], ('program', self.program_objective))
        actionable = [a for a in agents if a['available_actions']]
        viable = [a for a in actionable if not (
            a['resources'].get('energy') and a['resources']['energy']['value'] <= a['resources']['energy']['minimum']) and
            not any(c['status'] == 'failed' for c in self._constraint_checks(observation, a))]
        waiting_for_actor = not viable and any(a['status'] in ('busy', 'charging') for a in observation['agents'])
        if waiting_for_actor and not program_failed and not self.program:
            operation, selected, roles, commands, intents = 'keep', None, None, [], []
        if 'safe_stop' in self.operations and (program_failed or not viable and not waiting_for_actor):
            operation, selected, roles, commands, intents = 'safe_stop', None, None, [], []
        core_view = {key: observation[key] for key in ('episode_id', 'time', 'request_id')}
        request = decision(core_view, commands=commands, operation=operation, selected=selected, roles=roles, previous=self.previous)
        if program_commands:
            wire_program = [{**c,'action':self.public.wire_action(c['action']),
                             'parameters':self.public.command_parameters(c['action'],c['parameters'])} for c in program_commands]
            indices = [i for i, c in enumerate(commands) if c in wire_program]
            if len(indices) != len(program_commands):
                raise ProgramError('A proposed public program phase changed before dispatch')
            self.program.bind(request, indices)
        request['candidate_evaluations'] = candidates
        request['constraint_checks'] = checks
        request['explanation']['reason_codes'] = ['core_public_profile', 'declared_actions' if commands else 'no_admissible_action']
        request['explanation']['reason_codes'].extend(sorted(patrol_reasons))
        if wave:
            request['explanation']['reason_codes'].append('portable_flow_wave')
            for trace in reversed(self.traces):
                if trace.get('kind')=='flow_wave' and trace.get('request_id')==observation['request_id']:
                    trace['decision_id']=request['decision_id']
                    break
        if self.program:
            request['explanation']['reason_codes'].append('public_program_' + self.program.current)
        if operation == 'safe_stop':
            request['explanation']['reason_codes'].append('program_failed' if program_failed else 'no_viable_public_actor')
        elif waiting_for_actor and not self.program:
            request['explanation']['reason_codes'].append('waiting_for_public_actor')
        request['explanation']['expected_effects'] = [command['action'] for command in commands]
        self.proposed = (request, intents)
        self.patrol_estimates.begin(request,observation)
        return request

    def observe_response(self, response):
        self.patrol_estimates.finish(response)
        request, intents = self.proposed
        self.previous = request
        self.stopping = self.stopping or request['operation'] == 'safe_stop'
        if self.program:
            if request['operation'] == 'synthesize':
                self.program_announced = True
            self.program.observe(response)
        for index, (command, (intent, signature)) in enumerate(zip(request['commands'], intents)):
            self.pending[command['agent_id']] = (request['decision_id'], index, intent, signature)
        for item in response['command_results']:
            pending = self.pending.get(item.get('agent_id'))
            if (self.public.short_actions and pending is not None and not item.get('decision_id') and
                    pending[0] == request['decision_id'] and item.get('action') == pending[3][1]):
                # A short synchronous response can acknowledge only the
                # matching command from this step. Move may have covered just
                # part of the requested segment.
                outcome = {'applied':'completed','accepted':'completed','rejected':'failed'}.get(item.get('status'))
                if outcome is not None:
                    item = {**item,'decision_id':pending[0],'command_index':pending[1],'execution_status':outcome}
            if pending is None or pending[:2] != (item.get('decision_id'), item.get('command_index')):
                continue
            state = item.get('execution_status')
            if (state in _TERMINAL or item.get('validation_status') == 'rejected' or
                    item.get('delivery_status') in ('expired', 'lost')):
                _, _, intent, signature = self.pending.pop(item['agent_id'])
                aid, action, _ = signature
                if state == 'completed':
                    if action in ('observe', 'inspect', 'deliver', 'confirm', 'rescue', 'hold') and intent and intent[0] != 'repeat':
                        self.completed.add(intent)
                    if intent and intent[0] in ('coverage', 'repeat') and action == 'observe':
                        stamp = item.get('updated_at', response['time'])
                        if type(stamp) not in (int, float) or not 0 <= stamp <= response['time']:
                            stamp = response['time']
                        self.scanned_at[intent[1]] = stamp
                        if intent[0] == 'repeat':
                            self.repeat_goals.pop(aid, None)
                            self.net65_repeats += 1
                            self.traces.append({'type': 'net65_repeat_completed', 'episode_id': self.episode,
                                                'time': response['time'], 'position': list(intent[1]), 'agent_id': aid,
                                                'observed_at': stamp, 'decision_id': item['decision_id'],
                                                'command_index': item['command_index']})
                    if intent and intent[0] == 'repeat' and action == 'move':
                        self.repeat_goals[aid] = intent[1]
                    if intent and intent[0] == 'casualty':
                        if action == 'pickup':
                            self.cargo[aid] = intent[1]
                        if action == 'handover':
                            self.cargo.pop(aid, None)
                            self.completed.add(intent)
                else:
                    if intent and intent[0] == 'repeat':
                        self.repeat_goals.pop(aid, None)
                    self.cooldowns[signature] = response['time'] + 10.

    def reject(self, now, code):
        request, intents = self.proposed
        if self.program:
            self.failed_programs.add(self.program_objective)
            self._clear_program()
        if code == 'INVALID_SCENARIO' and request['operation'] != 'keep':
            self.unavailable_scenarios.update(request['selected_configuration']['scenario_ids'])
        for _, signature in intents:
            self.cooldowns[signature] = now + 10.
