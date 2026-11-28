"""Small planar Core reference world for reproducible controller trials."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from random import Random
import uuid

from contracts.protocol import ID, POINT, OPERATIONS, ProtocolError, object_schema, validate, validate_decision
from environment.geometry import segment_clear


CASES = ('normal', 'late_target', 'link_loss', 'blocked_route')
HORIZON = 200
BASE = (10., 10., 0.)
SPEED = 9.
SCAN_S = 2
SCAN_COST = .001
MOVE_COST = .00035
MINIMUM = .08
TRANSFER_RADIUS = 1.
ACTIONS = {
    'move': ('move', object_schema({'target': {'type':'array','minItems':3,'maxItems':3,
                         'prefixItems':[{'type':'number','minimum':0,'maximum':50},
                                        {'type':'number','minimum':0,'maximum':50},{'const':0}],
                         'items':False}})),
    # An empty observe already has a defined radius in _advance_agent.
    # Publish that fact instead of requiring a redundant argument.
    'observe': ('sense', object_schema({'radius': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 15,
                                                   'default': 15.}}, required=[])),
    'inspect': ('sense', object_schema({'target_id': ID})),
    'pickup': ('carry', object_schema({'target_id': ID})),
    'handover': ('carry', object_schema({'station_id': ID})),
    'hold': ('move', object_schema({})),
}
SCENARIOS = {
    name: {'roles': [{'id': 'scout', 'required_capabilities': ['sense']},
                     {'id': 'executor', 'required_capabilities': ['carry']},
                     {'id': 'rescuer', 'required_capabilities': ['carry']}],
           'parameters_schema': object_schema({})}
    for name in ('coverage_search', 'sector_recheck', 'coordination_reconfiguration')
}


def capabilities(*, estimates=True):
    extensions = ['metaevolution.step_deduplication.v1', 'metaroy.action_semantics.v1']
    result = {'protocol_version': '1.0', 'environment_id': 'metarescue_planar_reference',
              'mission_ids': ['search_and_rescue'], 'agent_kinds': ['aerial', 'ground'],
              'coordinates': {'unit': 'meter', 'axes': ['x', 'y', 'z']},
              'time_model': {'external_step': 1, 'wall_pacing': 'accelerated'},
              'supported_operations': list(OPERATIONS),
              'supported_scenarios': SCENARIOS,
              'supported_actions': {action: {'required_capabilities': [required], 'parameters_schema': schema}
                                    for action, (required, schema) in ACTIONS.items()},
              'extensions': extensions,
              'action_semantics': {'handover': {'semantic_id':'handover','effect':'evacuated'}}}
    if estimates:
        extensions.append('metarescue.patrol_estimates.v1')
        result['patrol_estimates'] = {'speed_m_s': SPEED, 'scan_s': SCAN_S, 'move_fraction_per_m': MOVE_COST,
                                      'scan_fraction': SCAN_COST, 'reference_distance_m': 15.}
    return result


@dataclass
class Action:
    result: dict
    parameters: dict
    remaining: int


@dataclass
class Agent:
    kind: str
    position: tuple[float, float, float]
    speed: float
    energy: float = 1.
    cargo: str | None = None
    action: Action | None = None


@dataclass
class Person:
    position: tuple[float, float, float]
    appears_at: int
    custody: str | None = None


class World:
    def __init__(self, seed, case='normal', *, opaque=False):
        if case not in CASES or type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
            raise ValueError('Expected a uint32 seed and a known 2D case')
        self.case = case
        self.opaque = opaque
        self.id = uuid.uuid4().hex
        self.time = 0
        self.status = 'running'
        self.closed = False
        self.configuration = {'scenario_ids': [], 'role_assignments': {}}
        self.synthesized = {}
        self.scenario_parameters = {}
        self.journal = []
        self.replays = {}
        self.ack_queue = []
        self.records = []
        self.events = []
        self.reports = {}
        self.visible = {}
        identifiers = ('42/λ', 'q:7/β', 'b&3/仮', 't?δ', '19/κ') if opaque else (
            'scout', 'carrier', 'base', 'person-1', 'person-2')
        self.scout, carrier, self.base, early, late = identifiers
        self.agents = {self.scout: Agent('aerial', BASE, SPEED), carrier: Agent('ground', BASE, 5.)}
        self.pose = {key: agent.position for key, agent in self.agents.items()}
        self.energy = {key: agent.energy for key, agent in self.agents.items()}
        random = Random(seed)
        self.people = {early: Person((12. + random.uniform(-1, 1), 13. + random.uniform(-1, 1), 0.), 0)}
        if case == 'late_target':
            self.people[late] = Person((10. + random.uniform(-1, 1), 10. + random.uniform(-1, 1), 0.), 131)
        if case == 'blocked_route':
            self.people[early].position = (40., 40., 0.)
        self.wall = ((29., 0., -1.), (31., 50., 1.)) if case == 'blocked_route' else None

    def reachable(self, identifier):
        return identifier != self.scout or self.case != 'link_loss' or not 4 <= self.time < 16

    def _actions(self, agent):
        if agent.action:
            return ('hold',)
        return ('move', 'hold', 'observe', 'inspect') if agent.kind == 'aerial' else ('move', 'hold', 'pickup', 'handover')

    def observation(self, request_id):
        agents = []
        for identifier, agent in self.agents.items():
            connected = self.reachable(identifier)
            actions = self._actions(agent) if connected else ()
            capabilities = ['move', 'sense'] if agent.kind == 'aerial' else ['move', 'carry']
            agents.append({'id': identifier, 'kind': agent.kind, 'position': list(self.pose[identifier]),
                           'status': 'busy' if connected and agent.action else 'active', 'capabilities': capabilities,
                           'resources': {'energy': {'id': 'energy', 'value': self.energy[identifier],
                                                    'minimum': MINIMUM, 'maximum': 1., 'unit': 'normalized'}},
                           'communication': {'reachable': connected}, 'role': self.configuration['role_assignments'].get(identifier),
                           'scenario_ids': self.configuration['scenario_ids'], 'constraints': [
                               {'id':'planar_domain/'+identifier,'type':'position_bounds','severity':'hard',
                                'scope':{'kind':'agent','agent_id':identifier},'min':[0,0,0],'max':[50,50,0],
                                'predicate':{'op':'and','args':[
                                    {'op':op,'left':{'path':'position.'+str(axis)},'right':{'value':bound}}
                                    for axis in range(3) for op,bound in (('ge',0),('le',0 if axis==2 else 50))]}}],
                           'available_actions': [{'action': action} for action in actions]})
        zones = [{'id': f'zone-{i}' if not self.opaque else f'ζ/{3-i}', 'type': kind,
                  'geometry': {'type': 'box', 'min': list(bounds[0]), 'max': list(bounds[1])},
                  'access': {}, 'risk': float(kind == 'obstacle'), 'cost': {}, 'communication': {}, 'constraints': []}
                 for i, (kind, bounds) in enumerate([('land', ((0, 0, 0), (50, 50, 0))),
                                                    ('air', ((0, 0, 0), (50, 50, 0)))] +
                                                   ([('obstacle', self.wall)] if self.wall else []))]
        targets = [{'id': identifier, 'kind': 'person', 'status': 'confirmed', 'confidence': 1.,
                    'position': list(person.position), 'observed_at': self.visible[identifier],
                    'evacuation_state': 'evacuated' if person.custody == self.base else 'carried' if person.custody else 'waiting'}
                   for identifier, person in self.people.items() if identifier in self.visible]
        if self.opaque:
            agents.reverse()
            zones.reverse()
            targets.reverse()
        connected = self.reachable(self.scout)
        return {'protocol_version': '1.0', 'episode_id': self.id, 'request_id': request_id, 'time': self.time,
                'agents': agents, 'zones': zones, 'targets': targets,
                'infrastructure': [{'id': self.base, 'kind': 'base_station', 'position': list(BASE),
                                    'status': 'active', 'capabilities': ['transfer'], 'constraints': [], 'resources': {},
                                    'communication': {'reachable': True}}],
                'mission': {'id': 'search_and_rescue', 'objectives': [
                    {'id': row['id'], 'type': 'rescue', 'target_id': row['id'], 'priority': 1.,
                     'status': 'completed' if row['evacuation_state'] == 'evacuated' else 'active'} for row in targets],
                    'priorities': ['search', 'rescue'], 'directives': [], 'constraints': [],
                    'status': {'running': 'active', 'success': 'completed', 'time_limit': 'failed',
                               'safe_stopped': 'safe_stopped'}[self.status], 'termination': {'time_limit': HORIZON}},
                'network': {'connectivity_score': 1. if connected else .5, 'partition_count': 0 if connected else 1,
                            'reachable_agent_ratio': 1. if connected else .5, 'average_link_quality': 1.,
                            'mesh_available': False, 'known_links': []},
                'observation_quality': {'global_completeness': 1. if connected else .5, 'maximum_delay': 1.,
                                        'stale_data_ratio': 0. if connected else .5, 'contradiction_count': 0,
                                        'unknown_state_ratio': 0. if connected else .5},
                'constraints': [], 'events': list(self.events),
                'metrics': {'mission_completion': sum(p.custody == self.base for p in self.people.values()) / len(self.people),
                            'safety_violations': 0,
                            'confirmed_evacuations': sum(t['evacuation_state'] == 'evacuated' for t in targets),
                            'known_people': len(targets), 'energy_fraction_spent': sum(1. - e for e in self.energy.values())}}

    def response(self, request_id, *, step=False):
        observation = self.observation(request_id)
        response = {k: observation[k] for k in ('protocol_version', 'episode_id', 'request_id', 'time')}
        response.update(status=self.status, observation=observation)
        if step:
            response.update(command_results=list(self.records), events=list(self.events), metrics=observation['metrics'],
                            done=self.status != 'running')
        return response

    def _clear(self, start, end):
        if end[2] != 0 or any(not 0 <= value <= 50 for value in end[:2]):
            return False
        return not self.wall or segment_clear(start, end, [{'min': self.wall[0], 'max': self.wall[1]}], radius=0.)

    def _validate_action(self, command):
        identifier, action, parameters = (command[key] for key in ('agent_id', 'action', 'parameters'))
        agent = self.agents[identifier]
        if action == 'move':
            goal = parameters['target']
            if not self._clear(agent.position, goal):
                raise ProtocolError('CONSTRAINT_VIOLATION', 'Route leaves the plane or crosses an obstacle',
                                    {'agent_id':identifier,'action':action,'rule':'motion_domain',
                                     'start':list(agent.position),'target':goal})
            if agent.energy - math.dist(agent.position, goal) * MOVE_COST < MINIMUM:
                raise ProtocolError('RESOURCE_EXHAUSTED', 'Route would spend the energy reserve')
        elif action in ('pickup', 'inspect'):
            person = self.people.get(parameters['target_id'])
            if person is None or parameters['target_id'] not in self.visible or person.custody is not None:
                raise ProtocolError('INVALID_COMMAND', 'Target is unavailable')
            radius = TRANSFER_RADIUS if action == 'pickup' else 15.
            if math.dist(agent.position, person.position) > radius or agent.cargo:
                raise ProtocolError('CONSTRAINT_VIOLATION', 'Agent cannot access the target from its current position')
        elif action == 'handover' and (parameters['station_id'] != self.base or agent.cargo is None or
                                       math.dist(agent.position, BASE) > TRANSFER_RADIUS):
            raise ProtocolError('INVALID_COMMAND', 'Handover requires cargo at the base')
        elif action == 'observe' and agent.energy - SCAN_COST < MINIMUM:
            raise ProtocolError('RESOURCE_EXHAUSTED', 'Scan would spend the energy reserve')

    @staticmethod
    def _synthesis(selected):
        # Listing 3 base declaration: no project-specific graph is required.
        strings = {'type': 'array', 'items': ID}
        array = {'type': 'array'}
        schema = object_schema({'id': ID, 'version': ID, 'title': ID,
            'game_model': object_schema({'class': ID, 'players': array,
                'information_structure': ID, 'stability_criterion': ID}),
            'applicability': object_schema({name: array for name in ('entry', 'continuation', 'exit', 'emergency_exit')}),
            'roles': {'type': 'array', 'items': {'anyOf': [ID, object_schema({'id': ID})]}},
            'objectives': array, 'constraints': array, 'allowed_actions': strings,
            'transition_targets': strings, 'metrics': array})
        scenario = selected['synthesized_scenario']
        validate(scenario, schema, 'INVALID_SCENARIO')
        if selected['scenario_ids'] != [scenario['id']]:
            raise ProtocolError('INVALID_SCENARIO', 'Synthesis ID does not match selection')
        return scenario

    def step(self, decision, body_sha, declarations):
        identifier = decision.get('decision_id')
        if isinstance(identifier, str) and identifier in self.replays:
            fingerprint, response = self.replays[identifier]
            if fingerprint != body_sha:
                raise ProtocolError('INVALID_REQUEST', 'Different bytes for a known decision ID')
            return response
        if self.closed or self.status != 'running':
            raise ProtocolError('INVALID_REQUEST', 'Episode is terminal')
        validate_decision(decision, self.observation(decision.get('request_id')), declarations)
        operation = decision['operation']
        selected = decision['selected_configuration']
        ids = selected['scenario_ids']
        roles = decision['role_assignments']
        previous = decision['previous_configuration']
        old_ids = self.configuration['scenario_ids']
        old_roles = self.configuration['role_assignments']
        if (operation not in declarations['supported_operations'] or
                set(previous['scenario_ids']) != set(old_ids) or previous['role_assignments'] != old_roles):
            raise ProtocolError('INVALID_SCENARIO', 'Decision does not match the active 2D scenario')
        if len(ids) != len(set(ids)):
            raise ProtocolError('INVALID_SCENARIO', 'Duplicate scenario membership')
        catalog = {**SCENARIOS, **self.synthesized}
        new_scenario = None
        if operation == 'synthesize':
            new_scenario = self._synthesis(selected)
            if new_scenario['id'] in SCENARIOS:
                raise ProtocolError('INVALID_SCENARIO', 'Synthesis cannot replace a declared scenario')
            if new_scenario['id'] in self.synthesized and self.synthesized[new_scenario['id']] != new_scenario:
                raise ProtocolError('INVALID_SCENARIO', 'Synthesis cannot redefine an existing scenario ID')
            catalog[new_scenario['id']] = new_scenario
        if operation != 'safe_stop':
            if any(sid not in catalog for sid in ids):
                raise ProtocolError('INVALID_SCENARIO', 'Unknown 2D scenario')
            if operation in ('select', 'switch') and len(ids) != 1 or operation == 'compose' and len(ids) < 2:
                raise ProtocolError('INVALID_SCENARIO', 'Invalid scenario cardinality')
            if operation in ('keep', 'modify') and (set(ids) != set(old_ids) or roles != old_roles):
                raise ProtocolError('INVALID_SCENARIO', 'Operation must retain scenario membership and roles')
            role_specs = {}
            for sid in ids:
                for spec in catalog[sid]['roles']:
                    spec = {'id': spec} if isinstance(spec, str) else spec
                    role_specs.setdefault(spec['id'], []).append(set(spec.get('required_capabilities', [])))
            for owner, role in roles.items():
                caps = {'move', 'sense' if self.agents[owner].kind == 'aerial' else 'carry'}
                if not any(required <= caps for required in role_specs.get(role, [])):
                    raise ProtocolError('INVALID_SCENARIO', 'Invalid 2D scenario role')
            for sid in ids:
                params = selected['parameters']
                if 'by_scenario' in params:
                    groups = params['by_scenario']
                    if not isinstance(groups, dict) or set(groups) != set(ids):
                        raise ProtocolError('INVALID_SCENARIO', 'Invalid composite parameters')
                    params = groups[sid]
                validate(params, catalog[sid].get('parameters_schema', object_schema({})), 'INVALID_SCENARIO')
        owners = [row['agent_id'] for row in decision['commands']]
        if len(owners) != len(set(owners)) or operation == 'safe_stop' and owners:
            raise ProtocolError('INVALID_COMMAND', 'Expected one action per agent and a command-free safe stop')
        for command in decision['commands']:
            self._validate_action(command)
        if new_scenario is not None:
            self.synthesized[new_scenario['id']] = deepcopy(new_scenario)
        if operation not in ('keep', 'safe_stop'):
            self.configuration = {'scenario_ids': list(ids), 'role_assignments': dict(roles)}
            self.scenario_parameters = deepcopy(selected['parameters'])

        self.events, self.records = [], []
        for index, command in enumerate(decision['commands']):
            owner, action = command['agent_id'], command['action']
            if action == 'hold' and self.agents[owner].action:
                self._finish(self.agents[owner], 'cancelled')
            receipt = {'agent_id': owner, 'decision_id': identifier, 'command_index': index,
                       'action': action, 'updated_at': self.time, 'validation_status': 'accepted',
                       'delivery_status': 'delivered', 'execution_status': 'running', 'reason_code': None}
            duration = SCAN_S if action in ('observe', 'inspect', 'pickup', 'handover') else 1
            self.agents[owner].action = Action(receipt, command['parameters'], duration)
            self.records.append(receipt)
        if operation == 'safe_stop':
            for agent in self.agents.values():
                if agent.action:
                    self._finish(agent, 'cancelled')
            self.status = 'safe_stopped'
        else:
            self._advance()
        response = self.response(decision['request_id'], step=True)
        self.replays[identifier] = (body_sha, response)
        self.journal.append({'type': 'step', 'decision': deepcopy(decision), 'response': response})
        return response

    def _finish(self, agent, status='completed'):
        receipt = dict(agent.action.result, updated_at=self.time, execution_status=status)
        self.ack_queue.append((self.time + 1, receipt))
        agent.action = None

    def _advance(self):
        self.time += 1
        for identifier, agent in self.agents.items():
            self._advance_agent(identifier, agent)
            if self.reachable(identifier):
                self.pose[identifier], self.energy[identifier] = agent.position, agent.energy
        for ready, receipt in self.ack_queue[:]:
            if ready <= self.time and self.reachable(receipt['agent_id']):
                self.records.append(receipt)
                self.ack_queue.remove((ready, receipt))
        for identifier, (seen, owner) in tuple(self.reports.items()):
            if seen < self.time and self.reachable(owner):
                self.visible[identifier] = seen
                self.events.append(self._event('target_detected', owner, identifier, seen))
                del self.reports[identifier]
        if all(person.custody == self.base for person in self.people.values()):
            self.status = 'success'
        elif self.time >= HORIZON:
            self.status = 'time_limit'

    def _advance_agent(self, identifier, agent):
        action = agent.action
        if action is None:
            return
        name, params = action.result['action'], action.parameters
        if name == 'move':
            target = tuple(params['target'])
            distance = math.dist(agent.position, target)
            if distance > agent.speed:
                agent.position = tuple(a + (b - a) * agent.speed / distance for a, b in zip(agent.position, target))
                agent.energy -= agent.speed * MOVE_COST
                return
            agent.position = target
            agent.energy -= distance * MOVE_COST
        else:
            action.remaining -= 1
            if action.remaining:
                return
            if name in ('observe', 'inspect'):
                agent.energy = max(MINIMUM, agent.energy - SCAN_COST)
                radius = params.get('radius', 15.)
                for key, person in self.people.items():
                    if (key not in self.visible and key not in self.reports and person.appears_at <= self.time and
                            math.dist(agent.position, person.position) <= radius and self._clear(agent.position, person.position)):
                        self.reports[key] = self.time, identifier
            elif name == 'pickup':
                key = params['target_id']
                person = self.people[key]
                if person.custody is not None:
                    self._finish(agent, 'failed')
                    return
                person.custody, agent.cargo = identifier, key
            elif name == 'handover':
                key = agent.cargo
                self.people[key].custody, agent.cargo = self.base, None
                self.events.append(self._event('target_state_changed', identifier, key, self.time,
                                               {'evacuation_state': 'evacuated', 'decision_id': action.result['decision_id'],
                                                'command_index': action.result['command_index']}))
        self._finish(agent)
        if name == 'handover':
            # The local wire link for the carrier delivers the handover at the
            # terminal boundary before the controller observes success.
            _, receipt = self.ack_queue.pop()
            self.records.append(receipt)

    def _event(self, kind, source, target, when, parameters=None):
        return {'id': f'{kind}/{target}/{when}', 'type': kind, 'source': source, 'targets': [target],
                'time': when, 'parameters': parameters or {}, 'confidence': 1., 'status': 'new'}
