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
from controller.decisions import decision
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
TARGET_STATES = {'detected', 'suspected', 'confirmed', 'delivered'}


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


def _supports(agent, action, params):
    for row in agent['available_actions']:
        if row['action'] != action:
            continue
        schema = row.get('parameters_schema')
        if schema is None:
            return True
        if not isinstance(schema, dict):
            continue
        try:
            validate(params, schema, 'INVALID_COMMAND')
            return True
        except ProtocolError:
            pass
    return False


def _directives(obs):
    items = list(obs['mission']['directives'])
    items += [e['parameters'] for e in obs['events']
              if e['type'] == 'operator_directive' and e['status'] in ('new', 'active')]
    return [d for d in items if d.get('status') not in ('resolved', 'expired', 'cancelled')]


def _directive_kind(row):
    return {str(row.get(k, '')).lower() for k in ('type', 'action', 'command')}


def _config(ids, params=None, synth=None):
    return {'scenario_ids': list(ids), 'parameters': params or {}, 'synthesized_scenario': synth}


class TournamentPolicy:
    def __init__(self):
        self.lock = RLock()
        self.records = deque(maxlen=4096)
        self._reset(None)

    def _reset(self, episode):
        self.episode = episode
        self.previous = None
        self.cache = OrderedDict()
        self.scan_attempts = set()
        self.scan_times = {}
        self.pickup_attempts = {}
        self.cargo = {}
        self.time = -1.

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
        return ('move', params) if params and _supports(agent, 'move', params) else None

    def _command(self, obs, zones, budget, agent, claimed, inspect):
        aid = agent['id']
        p = _position(agent['position'])
        rules = list(_hard_rules(obs, agent))
        energy, reserve = _reserve(agent, rules)
        def action(name, params=None):
            params = params or {}
            return (name, params) if _supports(agent, name, params) else None
        def go(goal):
            return self._move(obs, zones, budget, agent, goal)
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
                if math.dist(p, s['position']) <= 1.:
                    if cmd := action('handover', {'station_id': s['id']}):
                        return cmd
                elif cmd := go(_position(s['position'])):
                    return cmd
            return None if agent['status'] == 'busy' else action('hold')
        if agent['status'] == 'busy':
            # Busy without confirmed custody may be in the middle of service.
            return None
        if inspect is not None and agent['kind'] == 'aerial' and _supports(agent, 'move', {'target': list(p)}):
            goal = (*inspect[:2], p[2])
            if math.dist(p, goal) > 2. and (cmd := go(goal)):
                return cmd
            if math.dist(p, goal) <= 2.:
                return action('observe', {'radius': 10.}) or action('observe') or action('hold')
        targets = sorted((t for t in obs['targets'] if t.get('status') in TARGET_STATES and
                          t.get('evacuation_state') not in CARRIED | {'evacuated'} and _position(t.get('position')) is not None),
                         key=lambda t: (-_number(t.get('priority')), math.dist(p, t['position']), t['id']))
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
                if target['status'] in ('detected', 'confirmed', 'delivered'):
                    choices += [('rescue', params), ('deliver', params)]
                for obj in obs['mission']['objectives']:
                    success = obj.get('success_conditions', {})
                    if (obj.get('target_id') == tid and isinstance(success, dict) and
                        isinstance(success.get('resource_id'), str) and type(success.get('amount_at_least')) is int):
                        choices.insert(0, ('deliver', {**params, 'resource_id': success['resource_id'], 'amount': success['amount_at_least']}))
                for name, args in choices:
                    if not _supports(agent, name, args):
                        continue
                    radius = 1. if name == 'pickup' else 2.
                    if math.dist(p, goal) <= radius:
                        if name == 'pickup':
                            self.pickup_attempts[tid] = aid
                        claimed.add(tid)
                        return name, args
                    if cmd := go(goal if agent['kind'] != 'aerial' else (*goal[:2], p[2])):
                        claimed.add(tid)
                        return cmd
        if obs['network']['connectivity_score'] < .6 and (cmd := action('relay', {'enabled': True})):
            return cmd
        if not any(a['action'] == 'observe' for a in agent['available_actions']):
            # A carrier with no search capability should preserve its energy
            # while scouts discover work, rather than roaming a survey grid.
            return action('hold')
        scan = (aid, tuple(round(x, 2) for x in p))
        if scan not in self.scan_attempts and (cmd := action('observe', {'radius': 10.}) or action('observe')):
            if len(self.scan_attempts) >= 4096:
                self.scan_attempts.clear()
                self.scan_times.clear()
            self.scan_attempts.add(scan)
            self.scan_times[scan] = obs['time']
            return cmd
        candidates = set()
        for zone, shape in zones:
            if shape is None or zone['type'] != MEDIA.get(agent['kind']) or not _access(zone, agent['kind']) or zone['risk'] >= 1:
                continue
            lo, hi = shape.minimum, shape.maximum
            z = min(hi[2], max(lo[2], p[2]))
            for x in (.2, .5, .8):
                for y in (.2, .5, .8):
                    candidates.add((lo[0]+x*(hi[0]-lo[0]), lo[1]+y*(hi[1]-lo[1]), z))
        def scan_key(q):
            return aid, tuple(round(x, 2) for x in q)
        # Complete novel coverage first, then revisit the oldest locations so
        # targets that appear later are not permanently skipped.
        for goal in sorted(candidates, key=lambda q: (self.scan_times.get(scan_key(q), -1.), math.dist(p, q), q))[:48]:
            stamp = self.scan_times.get(scan_key(goal), -math.inf)
            if obs['time'] - stamp < REVISIT_SECONDS:
                continue
            if math.dist(p, goal) < .05:
                if cmd := action('observe', {'radius': 10.}) or action('observe'):
                    self.scan_attempts.add(scan_key(goal))
                    self.scan_times[scan_key(goal)] = obs['time']
                    return cmd
            elif cmd := go(goal):
                return cmd
        return action('observe', {'radius': 10.}) or action('observe') or action('hold')

    def decide(self, body):
        obs = public_observation(body)
        with self.lock:
            if self.episode != obs['episode_id']:
                self._reset(obs['episode_id'])
            rid = obs['request_id']
            fingerprint = hashlib.sha256(json.dumps(obs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
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
            self.records.append(deepcopy(result))
            return result

    def _decide(self, obs):
        agents = obs['agents']
        known = {a['id'] for a in agents}
        active = sorted((a for a in agents if a['status'] in ('active', 'degraded', 'busy') and
                         a['communication'].get('reachable', a['communication'].get('connected', True)) is not False),
                        key=lambda a: (a['kind'], _position(a['position']) or (), a['id']))
        reported = list(dict.fromkeys(s for a in agents for s in a['scenario_ids']))
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
        events = {e['type'] for e in obs['events'] if e['status'] in ('new','active')}
        low = any(_energy(a)[0] <= _energy(a)[1] + .05 for a in active)
        if stop:
            operation = 'safe_stop'
            reasons.append('operator_stop')
        elif inspect is not None:
            sid = 'public_inspection_' + hashlib.sha256(repr(inspect).encode()).hexdigest()[:12]
            if ids == [sid] and synth:
                operation = 'keep'
            else:
                operation, ids, params = 'synthesize', [sid], {}
                synth = {'id': sid, 'version': '1.0', 'title': 'Public inspection',
                         'game_model': {'class': 'coalition', 'players': ['scout'],
                                        'information_structure': 'partial', 'stability_criterion': 'utility_non_decreasing'},
                         'applicability': {'entry': [], 'continuation': [], 'exit': [], 'emergency_exit': []},
                         'roles': ['scout'], 'objectives': ['inspect'], 'constraints': [],
                         'allowed_actions': ['move', 'observe', 'inspect'], 'transition_targets': [],
                         'metrics': ['mission_completion', 'safety_violations']}
                roles = {a['id']: 'scout' for a in active if a['kind'] == 'aerial' and 'sense' in a['capabilities']}
            reasons.append('operator_inspection')
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
                if cmd := self._command(obs, zones, budget, agent, claimed, inspect):
                    commands.append({'agent_id': agent['id'], 'action': cmd[0], 'parameters': cmd[1]})
        result = decision(obs, commands=commands, operation=operation, selected=_config(ids, params, synth),
                          roles=roles, previous=previous)
        quality = obs['observation_quality']['global_completeness']
        risk = max((z['risk'] for z in obs['zones'] if z['type'] not in BLOCKING), default=0.)
        result['candidate_evaluations'] = [{'scenario_id': sid, 'applicable': True, 'utility': quality,
                                             'risk': risk, 'selected': True} for sid in ids]
        result['constraint_checks'] = [{'constraint_id': 'public_route', 'status': 'unknown'},
                                       {'constraint_id': 'minimum_energy_reserve', 'status': 'unknown' if low else 'passed'}]
        result['explanation'] = {'observation_refs': [obs['request_id']], 'reason_codes': reasons,
                                 'expected_effects': [c['action'] for c in commands] or ['no_new_commands'],
                                 'confidence': quality}
        return result
