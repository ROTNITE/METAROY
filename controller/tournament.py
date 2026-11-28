"""Observation-only Core 1.0 policy for an external tournament orchestrator.

The active mission client and its receipt-driven policy remain separate. This
policy never calls the environment or infers execution from a sent command.
"""
from collections import OrderedDict, deque
from copy import deepcopy
import hashlib
import json
import math
from threading import RLock

from contracts.protocol import OBSERVATION, DECISION, ProtocolError, validate
from controller.declared_scenarios import SCENARIO_SEMANTICS, choose_declared
from controller.decisions import decision
from controller.public_core import PublicCore
from controller.public_geometry import geometry, segments
from controller.public_route import RouteBudget, detour
from controller.navigation import point


MEDIA = {'aerial': 'air', 'ground': 'land', 'surface_water': 'water',
         'underwater': 'water', 'underground': 'underground'}
BLOCKING = {'obstacle', 'restricted', 'hazard'}
STOP = {'safe_stop', 'stop', 'pause', 'cancel', 'abort', 'halt'}
CARRIED = {'carried', 'loaded'}
ROUTE_RULES = {'position_bounds', 'zone_access', 'domain_access', 'restricted_zone',
               'minimum_separation', 'collision', 'risk_limit'}
RESOURCE_RULES = {'minimum_energy_reserve', 'resource_minimum', 'resource_reserve'}
REVISIT_SECONDS = 30.
SURVEY_ARRIVAL_M = 2.
MOVE_RETRY_SECONDS = 5.
TARGET_STATES = {'detected', 'suspected', 'confirmed'}


def _position(value):
    try:
        return point(value)
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


def _number(value, default=0.):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else default


def _action_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get('action') or value.get('id')
    return None


def public_observation(body):
    if not isinstance(body, dict):
        raise ProtocolError('INVALID_REQUEST', 'Expected an observation object')
    source = body.get('observation', body)
    if not isinstance(source, dict):
        raise ProtocolError('INVALID_REQUEST', 'Expected an observation object')
    if 'protocol_version' in source and source['protocol_version'] != '1.0':
        raise ProtocolError('UNSUPPORTED_PROTOCOL_VERSION', 'Expected Core 1.0')
    # The normative base profile, not a local extension or an environment ID.
    obs = {k: deepcopy(source[k]) for k in OBSERVATION['properties'] if k in source}
    for key in ('mission', 'network', 'observation_quality'):
        if isinstance(obs.get(key), dict):
            schema = OBSERVATION['properties'][key]['properties']
            obs[key] = {k: v for k, v in obs[key].items() if k in schema}
    for key in ('agents', 'infrastructure', 'zones', 'events'):
        if not isinstance(obs.get(key), list):
            continue
        schema = OBSERVATION['properties'][key]['items']['properties']
        obs[key] = [{k: v for k, v in row.items() if k in schema}
                    if isinstance(row, dict) else row for row in obs[key]]
    for agent in obs.get('agents', []) if isinstance(obs.get('agents'), list) else []:
        if not isinstance(agent, dict):
            continue
        if agent.get('kind') in ('digital', 'operator'):
            agent.setdefault('position', None)
        if isinstance(agent.get('available_actions'), list):
            actions = []
            for row in agent['available_actions']:
                name = _action_name(row)
                if not isinstance(name, str) or not name:
                    raise ProtocolError('INVALID_REQUEST', 'Invalid public action declaration')
                item = {'action': name}
                if isinstance(row, dict) and 'parameters_schema' in row:
                    item['parameters_schema'] = row['parameters_schema']
                actions.append(item)
            agent['available_actions'] = sorted(actions, key=lambda a: a['action'])
    # Structural validation deliberately accepts energy below its reserve: an
    # external disturbance can report depleted energy and must trigger recovery.
    validate(obs, OBSERVATION)
    for key in ('agents', 'infrastructure', 'zones', 'targets', 'events'):
        ids = [row.get('id') for row in obs[key]]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ProtocolError('INVALID_REQUEST', 'Missing or duplicate entity ID')
        obs[key].sort(key=lambda row: row['id'])
    for agent in obs['agents']:
        if agent['kind'] in MEDIA and _position(agent['position']) is None:
            raise ProtocolError('INVALID_REQUEST', 'Spatial agent requires XYZ position')
    for target in obs['targets']:
        for key in ('status', 'evacuation_state', 'carrier_id', 'supported_by_agent'):
            if key in target and target[key] is not None and not isinstance(target[key], str):
                raise ProtocolError('INVALID_REQUEST', 'Invalid target field', {'field': key})
        if target.get('position') is not None and _position(target['position']) is None:
            raise ProtocolError('INVALID_REQUEST', 'Invalid target position')
    groups = [obs['constraints'], obs['mission']['constraints']]
    groups.extend(a['constraints'] for a in (*obs['agents'], *obs['zones']))
    for group in groups:
        for rule in group:
            for key in ('id', 'type', 'severity'):
                if key in rule and not isinstance(rule[key], str):
                    raise ProtocolError('INVALID_REQUEST', 'Invalid constraint field', {'field': key})
    for key in ('directives', 'objectives', 'constraints'):
        obs['mission'][key].sort(key=lambda row: json.dumps(row, sort_keys=True))
    return obs


def _zone_shape(row):
    raw = row['geometry']
    try:
        if raw.get('type', raw.get('kind')) in ('axis_aligned_box', 'aabb', 'box'):
            raw = {**raw, 'type': 'box', 'min': raw.get('min', raw.get('lower')),
                   'max': raw.get('max', raw.get('upper'))}
        return geometry(raw)
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


def _access(zone, kind):
    access = zone['access']
    allowed = access.get('allowed_kinds', access.get('allowed_agent_kinds'))
    if isinstance(allowed, list):
        return kind in allowed or (not allowed and access.get('default') == 'allow')
    return access.get('default') != 'deny'


def _energy(agent):
    resource = agent['resources'].get('energy', {})
    return resource.get('value', 1.), resource.get('minimum', 0.)


def _hard_rules(obs, agent):
    for rule in (*obs['constraints'], *obs['mission']['constraints'], *agent['constraints']):
        if rule.get('severity', 'hard') != 'hard':
            continue
        scope = rule.get('scope', {})
        if isinstance(scope, dict) and scope.get('kind') == 'agent' and scope.get('agent_id', scope.get('id')) != agent['id']:
            continue
        yield rule


def _reserve(agent, rules):
    energy, reserve = _energy(agent)
    for rule in rules:
        if rule.get('type', rule.get('id')) in RESOURCE_RULES and rule.get('resource', rule.get('resource_id', 'energy')) == 'energy':
            reserve = max(reserve, _number(rule.get('minimum'), 1.))
    return energy, reserve


def _loaded_owner(target, known):
    if target.get('evacuation_state') not in CARRIED:
        return None
    for key in ('carrier_id', 'supported_by_agent'):
        owner = target.get(key)
        if isinstance(owner, str) and owner in known:
            return owner
    return None


def _directives(obs):
    items = list(obs['mission']['directives'])
    items += [e['parameters'] for e in obs['events']
              if e['type'] == 'operator_directive' and e['status'] in ('new', 'active')]
    return [d for d in items if d.get('status') not in ('resolved', 'expired', 'cancelled')]


def _directive_kind(row):
    return {str(row.get(k, '')).lower() for k in ('type', 'action', 'command')}


def _config(ids, params=None, synth=None):
    return {'scenario_ids': list(ids), 'parameters': params or {}, 'synthesized_scenario': synth}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class TournamentPolicy:
    def __init__(self, core_mode='strict'):
        self.core_mode = core_mode
        self.lock = RLock()
        self.records = deque(maxlen=4096)
        self.retired_episodes = OrderedDict()
        self.replies = OrderedDict()
        self._reset(None)

    def _reset(self, episode):
        self.episode = episode
        self.public = None
        self.capabilities = None
        self.scenarios = {}
        self.operations = set()
        self.unavailable_scenarios = set()
        self.previous = None
        self.cache = OrderedDict()
        self.scan_times = {}
        self.last_moves = {}
        self.pickup_attempts = {}
        self.inspection_attempts = {}
        self.cargo = {}
        self.time = -1.

    def _supports(self, agent, action, params):
        declared = self.public.actions.get(action) if self.public else {}
        if declared is None:
            return False
        required = declared.get('required_capabilities', [])
        if not isinstance(required, list) or any(not isinstance(cap, str) for cap in required) or not set(required) <= set(agent['capabilities']):
            return False
        declarations = [row for row in agent['available_actions'] if row['action'] == action]
        if not declarations:
            return False
        wire = self.public.command_parameters(action, params) if self.public else params
        for row in declarations:
            schemas = [declared[key] for key in ('parameters_schema',) if key in declared]
            if 'parameters_schema' in row:
                schemas.append(row['parameters_schema'])
            try:
                for schema in schemas:
                    if not isinstance(schema, dict):
                        raise ProtocolError('INVALID_COMMAND', 'Invalid public action schema')
                    validate(wire, schema, 'INVALID_COMMAND')
                return True
            except ProtocolError:
                continue
        return False

    def _clear(self, obs, zones, agent, start, end):
        kind = agent['kind']
        intervals = []
        for zone, shape in zones:
            # Media are overlapping permission volumes, not exclusions for
            # other kinds: an air zone above a land surface does not forbid a rover.
            same_medium = zone['type'] == MEDIA.get(kind)
            denied = not _access(zone, kind)
            explicit = zone['access'].get('allowed_kinds', zone['access'].get('allowed_agent_kinds', []))
            explicit = explicit if isinstance(explicit, list) else []
            restricted = zone['type'] in BLOCKING and (zone['type'] == 'obstacle' or kind not in explicit)
            hard = any(r.get('severity', 'hard') == 'hard' for r in zone['constraints'])
            blocked = restricted or (same_medium and (denied or zone['risk'] >= 1)) or hard
            if blocked and (shape is None or shape.interval(start, end) is not None):
                return False
            if shape is not None and not blocked and same_medium:
                # For surface vehicles the public pose may be the chassis centre,
                # above a thin land surface. Keep commanded Z unchanged; project
                # only the support test, and only for a nearby declared surface.
                a, b = start, end
                top = shape.maximum[2]
                if kind == 'ground' and 0 <= start[2] - top <= 1.5 and abs(start[2] - end[2]) < 1e-9:
                    a, b = (*start[:2], top), (*end[:2], top)
                intervals.extend(segments(shape, a, b))
        coverage = 0.
        for lo, hi in sorted(intervals):
            if lo > coverage + 1e-9:
                break
            coverage = max(coverage, hi)
        if coverage < 1. - 1e-9:
            return False
        for rule in _hard_rules(obs, agent):
            typ = rule.get('type', rule.get('id'))
            if typ == 'position_bounds':
                lo, hi = _position(rule.get('min')), _position(rule.get('max'))
                if lo is None or hi is None or not all(a <= b <= c for a, b, c in zip(lo, end, hi)):
                    return False
            elif typ == 'risk_limit':
                ceiling = _number(rule.get('maximum_risk'), -1.)
                if any(z['risk'] > ceiling and (s is None or s.interval(start, end) is not None) for z, s in zones):
                    return False
            elif typ in ('minimum_separation', 'collision'):
                margin = _number(rule.get('distance'), 1.5)
                delta = tuple(b-a for a,b in zip(start,end))
                norm = sum(d*d for d in delta)
                for peer in obs['agents']:
                    p = _position(peer['position'])
                    if p is None or peer['id'] == agent['id'] or peer['status'] in ('failed','offline'):
                        continue
                    t = min(1.,max(0.,sum(d*(v-a) for d,v,a in zip(delta,p,start))/norm)) if norm else 0.
                    if math.dist(p, tuple(a+t*d for a,d in zip(start,delta))) < margin:
                        return False
            elif typ not in ('minimum_energy_reserve', 'resource_minimum', 'resource_reserve', 'zone_access', 'domain_access', 'restricted_zone'):
                # An unknown hard movement rule cannot justify a route.
                return False
        return True

    def _move(self, obs, zones, budget, agent, goal):
        start = _position(agent['position'])
        if start is None or goal is None or agent['kind'] not in MEDIA:
            return None
        if agent['kind'] in ('ground', 'surface_water'):
            goal = (*goal[:2], start[2])
        if math.dist(start, goal) < .05:
            return None
        def clear(a, b):
            return self._clear(obs, zones, agent, a, b)
        waypoint = goal if budget.test(clear, start, goal) else detour(start, goal, zones, agent['kind'], clear, budget)
        params = {'target': list(waypoint)} if waypoint is not None else None
        return ('move', params) if params and self._supports(agent, 'move', params) else None

    def _command(self, obs, zones, budget, agent, claimed, inspect):
        aid = agent['id']
        p = _position(agent['position'])
        rules = list(_hard_rules(obs, agent))
        energy, reserve = _reserve(agent, rules)
        def action(name, params=None):
            params = params or {}
            return (name, params) if self._supports(agent, name, params) else None
        def go(goal):
            return self._move(obs, zones, budget, agent, goal)
        def observe():
            declarations = [row for row in agent['available_actions'] if row['action'] == 'observe']
            if self.public and 'observe' in self.public.actions:
                declarations.append(self.public.actions['observe'])
            for row in declarations:
                schema = row.get('parameters_schema')
                properties = schema.get('properties') if isinstance(schema, dict) else None
                if isinstance(properties, dict) and 'radius' in properties and (cmd := action('observe')):
                    return cmd
            return action('observe', {'radius': 10.}) or action('observe')
        if any(rule.get('type', rule.get('id')) not in ROUTE_RULES | RESOURCE_RULES for rule in rules):
            # Never execute an action under an unknown mandatory constraint.
            return action('hold')
        for directive in _directives(obs):
            if _directive_kind(directive) & {'hold'} and directive.get('target_id', directive.get('agent_id')) == aid:
                return action('hold')
        stations = [s for s in obs['infrastructure'] if s['status'] == 'active' and _position(s['position']) is not None]
        if energy <= reserve + .05:
            for s in sorted((s for s in stations if s['kind'] == 'charging_station'),
                            key=lambda s: math.dist(p, s['position']) if p else 0.):
                if p and math.dist(p, s['position']) <= 1.:
                    if cmd := action('charge', {'station_id': s['id']}):
                        return cmd
            return action('hold')
        if p is None:
            return action('relay', {'enabled': True}) or action('observe') or action('hold')
        if aid in self.cargo:
            if agent['status'] == 'busy' and not any(a['action'] in ('move', 'handover') for a in agent['available_actions']):
                return None
            for s in sorted((s for s in stations if s['kind'] == 'base_station'), key=lambda s: math.dist(p, s['position'])):
                station = _position(s['position'])
                if agent['kind'] == 'ground' and abs(station[2]-p[2]) <= 1.25:
                    station = (*station[:2], p[2])
                if math.dist(p, station) <= 1.:
                    if cmd := action('handover', {'station_id': s['id']}):
                        return cmd
                elif cmd := go(station):
                    return cmd
            return None if agent['status'] == 'busy' else action('hold')
        if agent['status'] == 'busy':
            # Busy without confirmed custody may be in the middle of service.
            return None
        if inspect is not None and agent['kind'] == 'aerial' and self._supports(agent, 'move', {'target': list(p)}):
            goal = (*inspect[:2], p[2])
            if math.dist(p, goal) > 2. and (cmd := go(goal)):
                return cmd
            if math.dist(p, goal) <= 2.:
                return observe() or action('hold')
        targets = sorted((t for t in obs['targets'] if t.get('status') in TARGET_STATES and
                          t.get('evacuation_state') not in CARRIED | {'evacuated'} and _position(t.get('position')) is not None),
                         key=lambda t: (-_number(t.get('priority')), math.dist(p, t['position']), t['id']))
        needs_confirmation = any(task.get('type') == 'confirm' and task.get('status') != 'completed'
                                 for task in obs['mission']['objectives'])
        if not obs['observation_quality']['contradiction_count']:
            for target in targets:
                tid = target['id']
                if tid in claimed:
                    continue
                if isinstance(target.get('accessible_kinds'), list) and agent['kind'] not in target['accessible_kinds']:
                    continue
                goal = _position(target['position'])
                params = {'target_id': tid}
                choices = []
                if target['status'] in ('suspected', 'detected'):
                    choices += [('confirm', params), ('inspect', params)]
                if target['status'] == 'confirmed':
                    choices += [('pickup', params)]
                if target['status'] == 'confirmed' or (target['status'] == 'detected' and not needs_confirmation):
                    choices += [('rescue', params), ('deliver', params)]
                for obj in obs['mission']['objectives']:
                    success = obj.get('success_conditions', {})
                    if (obj.get('target_id') == tid and isinstance(success, dict) and
                        isinstance(success.get('resource_id'), str) and type(success.get('amount_at_least')) is int):
                        choices.insert(0, ('deliver', {**params, 'resource_id': success['resource_id'], 'amount': success['amount_at_least']}))
                for name, args in choices:
                    if not self._supports(agent, name, args):
                        continue
                    radius = 1. if name == 'pickup' else 2.
                    inspection = name in ('confirm', 'inspect')
                    provenance = inspection and (
                        target.get('source_id') == aid or any(
                            e['type'] == 'target_detected' and e['source'] == aid and tid in e['targets']
                            for e in obs['events']))
                    attempt = self.inspection_attempts.get((aid, tid))
                    ready_to_retry = attempt is None or math.dist(attempt[1], p) >= 1. or obs['time']-attempt[0] >= 10.
                    approach = (*goal[:2], p[2]) if (name == 'pickup' and agent['kind'] == 'ground' and
                                                     abs(goal[2]-p[2]) <= 1.25) else goal
                    if (math.dist(p, approach) <= radius or provenance) and (not inspection or ready_to_retry):
                        if name == 'pickup':
                            self.pickup_attempts[tid] = aid
                        if inspection:
                            self.inspection_attempts[aid, tid] = (obs['time'], p)
                        claimed.add(tid)
                        return name, args
                    if agent['kind'] == 'aerial':
                        # Close the actual 3D sensing gap after a nearby attempt.
                        altitude = min(p[2], goal[2]+8.) if inspection and attempt else p[2]
                        goal = (*goal[:2], altitude)
                    if cmd := go(goal):
                        claimed.add(tid)
                        return cmd
        if (obs['network']['connectivity_score'] < .6 and not agent['communication'].get('relay_enabled') and
                (cmd := action('relay', {'enabled': True}))):
            return cmd
        if not any(a['action'] == 'observe' for a in agent['available_actions']):
            # A carrier with no search capability should preserve its energy
            # while scouts discover work, rather than roaming a survey grid.
            return action('hold')
        scan = aid, tuple(round(x, 2) for x in p)
        if scan not in self.scan_times and (cmd := observe()):
            if len(self.scan_times) >= 4096:
                self.scan_times.pop(next(iter(self.scan_times)))
            self.scan_times[scan] = obs['time']
            return cmd
        mission_points = {goal for task in obs['mission']['objectives'] if task.get('type') in ('search', 'search_and_evacuate')
                          and task.get('status') not in ('completed', 'failed', 'cancelled')
                          and (goal := _position(task.get('position'))) is not None}
        if agent['kind'] == 'aerial':
            mission_points = {(*goal[:2], max(p[2], goal[2])) for goal in mission_points}
        elif agent['kind'] in ('ground', 'surface_water'):
            mission_points = {(*goal[:2], p[2]) for goal in mission_points}
        candidates = set(mission_points)
        for zone, shape in zones:
            if shape is None or zone['type'] != MEDIA.get(agent['kind']) or not _access(zone, agent['kind']) or zone['risk'] >= 1:
                continue
            lo, hi = shape.minimum, shape.maximum
            z = min(hi[2], max(lo[2], p[2]))
            if agent['kind'] == 'aerial' and z-lo[2] < 2. and hi[2]-lo[2] > 4.:
                z = lo[2]+min(15., (hi[2]-lo[2])*.25)
            for x in (.2, .5, .8):
                for y in (.2, .5, .8):
                    candidates.add((lo[0]+x*(hi[0]-lo[0]), lo[1]+y*(hi[1]-lo[1]), z))
        def scan_key(q):
            if agent['kind'] in ('aerial', 'ground', 'surface_water'):
                q = q[:2]
            return aid, tuple(round(x*2) for x in q)
        # Complete novel coverage first, then revisit the oldest locations so
        # targets that appear later are not permanently skipped.
        for goal in sorted(candidates, key=lambda q: (self.scan_times.get(scan_key(q), -1.),
                                                     q not in mission_points, math.dist(p, q), q))[:48]:
            stamp = self.scan_times.get(scan_key(goal), -math.inf)
            if obs['time'] - stamp < REVISIT_SECONDS:
                continue
            distance = math.dist(p, goal)
            intent = self.last_moves.get(aid)
            # Some executors stop inside a tolerance. Relax arrival only
            # after repeated public poses show that the nearby move settled.
            settled = (distance <= SURVEY_ARRIVAL_M and intent is not None and
                       obs['time']-intent[0] >= MOVE_RETRY_SECONDS and math.dist(intent[1], goal) <= .25 and
                       math.dist(intent[2], p) <= .05)
            if distance < .05 or settled:
                if cmd := observe():
                    if len(self.scan_times) >= 4096:
                        self.scan_times.pop(next(iter(self.scan_times)))
                    self.scan_times[scan_key(goal)] = obs['time']
                    return cmd
            elif cmd := go(goal):
                return cmd
        return observe() or action('hold')

    def decide(self, body):
        wrapped = isinstance(body, dict) and 'observation' in body
        source = body['observation'] if wrapped else body
        declared = body.get('capabilities') if wrapped else None
        raw_episode = source.get('episode_id') if isinstance(source, dict) else None
        raw_request = source.get('request_id') if isinstance(source, dict) else None
        with self.lock:
            belongs = self.episode == raw_episode
            if isinstance(raw_episode, str) and raw_episode in self.retired_episodes:
                cached = self.replies.get((raw_episode, raw_request)) if isinstance(raw_request, str) else None
                if cached and cached[0] == _fingerprint(source) and (declared is None or cached[1] == _fingerprint(declared)):
                    return deepcopy(cached[2])
                raise ProtocolError('UNKNOWN_EPISODE', 'Observation belongs to a retired episode')
            if belongs and declared is not None and declared != self.capabilities:
                raise ProtocolError('INVALID_REQUEST', 'Public capability declaration changed within the episode')
            public = self.public if belongs else None
            if not belongs and declared is not None:
                public = PublicCore(declared, mode=self.core_mode)
                if not public.identity and (self.core_mode == 'strict' or not public.compatibility):
                    raise ProtocolError('INVALID_REQUEST', 'Tournament coordinates must use XYZ meters')
            raw = public.wire_observation(source) if public else source
            obs = public_observation(raw)
            if public:
                obs = public.view(obs)
            if not belongs:
                if self.episode is not None:
                    self.retired_episodes[self.episode] = None
                    if len(self.retired_episodes) > 4096:
                        self.retired_episodes.popitem(last=False)
                self._reset(obs['episode_id'])
                if public:
                    self.public, self.capabilities = public, deepcopy(declared)
                    self.scenarios = declared.get('supported_scenarios', {})
                    self.operations = set(declared.get('supported_operations', ()))
            rid = obs['request_id']
            fingerprint = _fingerprint(obs)
            if rid in self.cache:
                old, result = self.cache[rid]
                if old != fingerprint:
                    raise ProtocolError('INVALID_REQUEST', 'Changed observation for a known request ID')
                return deepcopy(result)
            if obs['time'] < self.time:
                raise ProtocolError('INVALID_REQUEST', 'Observation time moved backwards')
            result = self._decide(obs)
            validate(result, DECISION)
            self.time = obs['time']
            self.previous = deepcopy(result)
            self.cache[rid] = (fingerprint, deepcopy(result))
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            self.replies[(obs['episode_id'], rid)] = (_fingerprint(source), _fingerprint(self.capabilities), deepcopy(result))
            if len(self.replies) > 512:
                self.replies.popitem(last=False)
            self.records.append(deepcopy(result))
            return result

    def _decide(self, obs):
        agents = obs['agents']
        known = {a['id'] for a in agents}
        active = sorted((a for a in agents if a['status'] in ('active', 'degraded', 'busy') and
                         a['communication'].get('reachable', a['communication'].get('connected', True)) is not False),
                        key=lambda a: (a['kind'], _position(a['position']) or (), a['id']))
        reported = sorted({s for a in agents for s in a['scenario_ids']})
        reported_roles = {a['id']: a['role'] for a in agents if a['role'] is not None}
        previous = deepcopy(self.previous)
        if previous is None:
            previous = {'selected_configuration': _config(reported), 'role_assignments': reported_roles}
        # The orchestrator calls again after a successful step; the prior
        # submitted plan is our configuration hypothesis, never an action receipt.
        # Per-agent scenario memberships can lag behind the global plan.
        previous['role_assignments'] = {k: v for k,v in previous['role_assignments'].items() if k in known}
        old = previous['selected_configuration']
        ids, params, synth = old['scenario_ids'], old['parameters'], old['synthesized_scenario']
        roles = previous['role_assignments']
        directives = _directives(obs)
        stop = any(_directive_kind(d) & STOP for d in directives)
        inspect = None
        for d in directives:
            if _directive_kind(d) & {'inspect_point', 'inspect', 'standoff'}:
                loc = d.get('point', d.get('target'))
                if isinstance(loc, list) and len(loc) == 2:
                    loc = [*loc, 0.]
                inspect = _position(loc)
                if inspect is not None:
                    break
        reasons = ['public_observation_only']
        evaluations = None
        events = {e['type'] for e in obs['events'] if e['status'] in ('new','active')}
        low = any(_energy(a)[0] <= _energy(a)[1] + .05 for a in active)
        if stop:
            operation = 'safe_stop'
            reasons.append('operator_stop')
        elif self.public and SCENARIO_SEMANTICS in self.public.extensions and inspect is None:
            self.previous = previous
            operation, selected, assigned, evaluations = choose_declared(self, obs, active, ids)
            if operation == 'modify' and assigned != roles:
                operation, selected, assigned = 'keep', None, None
            if selected is not None:
                ids, params, synth = (selected[key] for key in ('scenario_ids', 'parameters', 'synthesized_scenario'))
                roles = assigned if assigned is not None else roles
            reasons.append('published_scenario_semantics')
        elif inspect is not None:
            sid = 'public_inspection_' + hashlib.sha256(repr(inspect).encode()).hexdigest()[:12]
            if ids == [sid] and synth:
                operation = 'keep'
            else:
                operation, ids, params = 'synthesize', [sid], {}
                inspection_actions = ('move', 'observe', 'inspect', 'hold')
                synth = {'id': sid, 'version': '1.0', 'title': 'Public inspection',
                         'game_model': {'class': 'coalition', 'players': ['scout'],
                                        'information_structure': 'partial', 'stability_criterion': 'utility_non_decreasing'},
                         'applicability': {'entry': [], 'continuation': [], 'exit': [], 'emergency_exit': []},
                         'roles': ['scout'], 'objectives': ['inspect'], 'constraints': [],
                         'allowed_actions': [self.public.wire_action(action) if self.public else action
                                             for action in inspection_actions
                                             if not self.public or action in self.public.actions],
                         'transition_targets': [],
                         'metrics': ['mission_completion', 'safety_violations']}
                roles = {a['id']: 'scout' for a in active if a['kind'] == 'aerial' and 'sense' in a['capabilities']}
            reasons.append('operator_inspection')
        elif synth is not None and synth['title'] == 'Public inspection':
            operation, ids, params, synth = 'switch', ['coverage_search'], {}, None
            roles = {a['id']: 'scout' for a in active if {'sense', 'move'} <= set(a['capabilities'])}
            reasons.append('operator_inspection_finished')
        elif len(reported) > 1 and self.previous is not None and set(ids) != set(reported):
            operation, ids, params, synth = 'compose', reported, {'by_scenario': {s: {} for s in reported}}, None
            reasons.append('complementary_public_scenarios')
        elif not ids and not any(_energy(a)[0] > _energy(a)[1] + .05 for a in active):
            operation = 'keep'
            reasons.append('waiting_for_viable_public_actor')
        elif not ids:
            operation, ids, params, synth = 'select', ['coverage_search'], {}, None
            roles = {a['id']: 'scout' for a in active if 'sense' in a['capabilities'] and 'move' in a['capabilities']}
            reasons.append('initial_public_search')
        elif 'connectivity_loss' in events and ids != ['coordination_reconfiguration']:
            operation, ids, params, synth = 'switch', ['coordination_reconfiguration'], {}, None
            roles = {a['id']: 'scout' for a in active if 'sense' in a['capabilities'] and 'move' in a['capabilities']}
            reasons.append('connectivity_reconfiguration')
        elif (low or 'resource_shortage' in events) and params.get('priority_bias') != 1.2:
            operation, params = 'modify', {**params, 'priority_bias': 1.2}
            reasons.append('resource_reserve_recovery')
        else:
            operation = 'keep'
            reasons.append('continue_public_plan')
            if obs['network']['connectivity_score'] < .6:
                # A connectivity metric alone does not declare a new scenario.
                # Keep the accepted plan and route only reachable actors.
                reasons.append('connectivity_degraded')
        commands = []
        self.last_moves = {aid: intent for aid, intent in self.last_moves.items() if aid in known}
        if not stop:
            targets = {t['id']: t for t in obs['targets']}
            # A sent pickup is not an acknowledgement, and a bare 'carried'
            # status does not identify which carrier owns the target.
            for aid, tid in list(self.cargo.items()):
                if aid not in known or (tid in targets and (targets[tid].get('evacuation_state') not in CARRIED or
                        (_loaded_owner(targets[tid], known) is not None and _loaded_owner(targets[tid], known) != aid))):
                    self.cargo.pop(aid, None)
            if not obs['observation_quality']['contradiction_count']:
                for tid, target in targets.items():
                    owner = _loaded_owner(target, known)
                    # Legacy public observations can report a loaded target at
                    # the carrier's pose without publishing a carrier ID. A
                    # matching pickup intent plus a unique actor within public pickup range
                    # is a planning hypothesis, never a completed goal. Do
                    # not resolve ambiguous custody by the order of agent IDs.
                    if owner is None and target.get('evacuation_state') in CARRIED:
                        attempted = self.pickup_attempts.get(tid)
                        pose = _position(target.get('position'))
                        possible = [a['id'] for a in agents if pose is not None and 'carry' in a['capabilities']
                                    and _position(a['position']) is not None and math.dist(a['position'], pose) <= 1. + 1e-9]
                        if possible == [attempted]:
                            owner = attempted
                    if owner is not None:
                        self.cargo[owner] = tid
            for tid, aid in list(self.pickup_attempts.items()):
                if aid not in known or tid in self.cargo.values() or targets.get(tid, {}).get('evacuation_state') == 'evacuated':
                    self.pickup_attempts.pop(tid, None)
            zones = [(z, _zone_shape(z)) for z in obs['zones']]
            budget = RouteBudget(len(zones))
            claimed = set(self.cargo.values())
            for agent in active[:256]:
                if inspect is not None and agent['id'] not in roles:
                    continue
                if cmd := self._command(obs, zones, budget, agent, claimed, inspect):
                    name, args = cmd
                    aid = agent['id']
                    if name == 'move':
                        last = self.last_moves.get(aid)
                        if (last and obs['time']-last[0] < MOVE_RETRY_SECONDS and
                                math.dist(last[1], args['target']) <= .25):
                            continue
                        self.last_moves[aid] = (obs['time'], args['target'], _position(agent['position']))
                    elif name not in ('observe', 'relay'):
                        self.last_moves.pop(aid, None)
                    if self.public:
                        args = self.public.command_parameters(name, args)
                        name = self.public.wire_action(name)
                    if inspect is None or name in synth['allowed_actions']:
                        commands.append({'agent_id': aid, 'action': name, 'parameters': args})
        result = decision(obs, commands=commands, operation=operation, selected=_config(ids, params, synth),
                          roles=roles, previous=previous)
        quality = obs['observation_quality']['global_completeness']
        risk = max((z['risk'] for z in obs['zones'] if z['type'] not in BLOCKING), default=0.)
        result['candidate_evaluations'] = evaluations if evaluations is not None else [
            {'scenario_id': sid, 'applicable': True, 'utility': quality, 'risk': risk, 'selected': True}
            for sid in ids]
        result['constraint_checks'] = [{'constraint_id': 'public_route', 'status': 'unknown'},
                                       {'constraint_id': 'minimum_energy_reserve', 'status': 'unknown' if low else 'passed'}]
        result['explanation'] = {'observation_refs': [obs['request_id']], 'reason_codes': reasons,
                                 'expected_effects': [c['action'] for c in commands] or ['no_new_commands'],
                                 'confidence': quality}
        return result
