"""Experimental online resource estimator. Uses only public telemetry and matching action ACKs."""
from collections import deque
from statistics import median
import math
from controller.bottleneck import BottleneckPolicy
from controller.portable import point, horizon_of, minimum_assignment, AssignmentError, _path_clear

class ResourcePolicy(BottleneckPolicy):
    def __init__(self, capabilities, requested_policy, *, scout_patrol='net65', public=None, variant='localguard'):
        self.variant = variant
        super().__init__(capabilities, requested_policy, scout_patrol=scout_patrol, public=public, staged_patrol=True)

    def _reset(self, episode):
        super()._reset(episode)
        self.energy_samples = {}
        self.previous_reading = {}
        self.service_samples = {}
        self.sent_at = {}

    @staticmethod
    def resource_signature(agent):
        e = agent.get('resources', {}).get('energy')
        if not e or not isinstance(e.get('unit'), str):
            return None
        return (e.get('unit'), e.get('minimum'), e.get('maximum'))

    def rate(self, agent, loaded):
        sig = self.resource_signature(agent)
        samples = self.energy_samples.get((agent['id'], sig, loaded), ())
        return median(samples) if len(samples) >= 3 else None

    def service_time(self, aid):
        p = self.service_samples.get((aid,'pickup'), ())
        h = self.service_samples.get((aid,'handover'), ())
        return median(p) + median(h) if p and h else None

    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self._reset(observation['episode_id'])
            self.started = observation['time']
        if self._profile():
            now = observation['time']
            for agent in observation['agents']:
                aid = agent['id']; sig = self.resource_signature(agent)
                pos = agent.get('position'); connected = agent['communication'].get('reachable', agent['communication'].get('connected', True)) is not False
                if agent['kind'] != 'ground' or not connected or sig is None or pos is None:
                    self.previous_reading.pop(aid, None)
                    continue
                energy = agent['resources']['energy']['value']; loaded = aid in self.cargo
                prev = self.previous_reading.get(aid); pending = self.pending.get(aid)
                if prev and pending and pending[3][1] == 'move' and 0 < now-prev[0] <= 2 and prev[3:]==(sig,loaded):
                    length = math.dist(pos, prev[1]); spent = prev[2] - energy
                    if length > .1 and spent > 0 and math.isfinite(spent/length):
                        self.energy_samples.setdefault((aid,sig,loaded),deque(maxlen=12)).append(spent/length)
                self.previous_reading[aid] = (now, tuple(pos), energy, sig, loaded)
        return super().decide(observation)

    def observe_response(self, response):
        request, _ = self.proposed
        for i, command in enumerate(request['commands']):
            if command['action'] in ('pickup','handover'):
                self.sent_at[(request['decision_id'],i)] = (command['agent_id'],command['action'],request['time'])
        for item in response['command_results']:
            key = (item.get('decision_id'), item.get('command_index'))
            sample = self.sent_at.get(key)
            if sample and item.get('agent_id') == sample[0] and item.get('action') == sample[1] and item.get('execution_status') in ('completed','failed','cancelled'):
                self.sent_at.pop(key)
                dt = item.get('updated_at',response['time']) - sample[2]
                if item['execution_status']=='completed' and 0 <= dt <= 120:
                    self.service_samples.setdefault(sample[:2],deque(maxlen=8)).append(dt)
        # Bound memory when receipts are lost.
        self.sent_at = {k:v for k,v in self.sent_at.items() if response['time']-v[2] <= 120}
        return super().observe_response(response)

    def _casualty(self, observation, zones, agent, claimed, issue, wave=None):
        if self.variant != 'localguard' or not self._profile() or agent['kind'] != 'ground':
            return super()._casualty(observation,zones,agent,claimed,issue,wave)
        energy = agent['resources'].get('energy')
        empty = self.rate(agent,False); loaded = self.rate(agent,True)
        if energy is None or empty is None and loaded is None:
            return super()._casualty(observation,zones,agent,claimed,issue,wave)
        # If only loaded rate is available, use it for the empty leg as a
        # conservative heuristic. No inferred guarantee of future feasibility.
        empty = empty or loaded
        loaded = loaded or 1.35*empty
        available = energy['value']-energy['minimum']
        aid = agent['id']
        def checked(a, action, params, intent):
            if action not in ('move','pickup'):
                return issue(a,action,params,intent)
            if aid in self.cargo:
                required = math.dist(a['position'],params['target'])*loaded if action=='move' else 0.
            else:
                t = next((t for t in observation['targets'] if t['id']==intent[1]),None)
                if t is None or t.get('position') is None:
                    return issue(a,action,params,intent)
                goal = point(t['position'])
                station = [math.dist(goal,s['position']) for s in observation['infrastructure'] if
                           s['kind']=='base_station' and s['status']=='active' and _path_clear(zones,{**a,'position':goal},s['position'])]
                if not station:
                    return issue(a,action,params,intent)
                required = (math.dist(a['position'],goal)*empty if action=='move' else 0.)+min(station)*loaded
            if required*1.10 > available:
                self.traces.append({'kind':'resource_guard','type':'resource_guard','episode_id':self.episode,
                                    'time':observation['time'],'request_id':observation['request_id'],
                                    'agent_id':aid,'action':action,'intent':intent,'estimated_energy':required,'available':available})
                return False
            return issue(a,action,params,intent)
        return super()._casualty(observation,zones,agent,claimed,checked,wave)

    def _flow_wave(self, observation, zones, agents, claimed, orders):
        if (not self._profile() or self.program or orders or observation['observation_quality']['contradiction_count']):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        crew = [a for a in agents if a['id'] not in self.pending and a['id'] not in self.cargo and
                'carry' in a['capabilities'] and a['kind'] == 'ground' and
                (not a['resources'].get('energy') or a['resources']['energy']['value'] > a['resources']['energy']['minimum']) and
                not any(c['status'] == 'failed' for c in self._constraint_checks(observation, a))]
        if not crew or not all(self.rate(a, False) is not None for a in crew) or len({self.resource_signature(a)[0] for a in crew}) != 1:
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        if any(self._usable_energy(a) is None for a in crew):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        if self.variant not in ('guard','adaptive','adaptive-service','guard-buffer','localguard') and any(self._usable_energy(a) < .25 for a in crew):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        reference = median(self.rate(a, False) for a in crew)
        targets = sorted((t for t in observation['targets'] if t.get('kind') == 'person' and
                          t.get('status') == 'confirmed' and t.get('position') is not None and
                          t.get('evacuation_state', 'waiting') == 'waiting' and
                          ('casualty', t['id']) not in claimed and ('casualty', t['id']) not in self.completed),
                         key=lambda t: t['id'])
        stations = [s for s in observation['infrastructure'] if s['kind'] == 'base_station' and s['status'] == 'active']
        if not (1 <= len(crew) <= 32 and 1 <= len(targets) <= 64 and stations):
            return {}
        edges, excluded, energy_filtered = {}, [], []
        horizon = horizon_of(observation['mission'])
        priorities = {o.get('target_id'): o.get('priority', 0) for o in observation['mission']['objectives'] if o.get('type') == 'rescue'}
        measured = all(a['id'] in self.speeds for a in crew)
        for i, a in enumerate(crew):
            for j, t in enumerate(targets):
                goal = point(t['position'])
                dist = math.dist(a['position'], goal)
                action, params = ('pickup', {'target_id': t['id']}) if dist <= 1 else ('move', {'target': list(goal)})
                if not self._supports(a, action, params) or self.cooldowns.get((a['id'], action, repr(params)), -1) > observation['time']:
                    continue
                if dist > 1 and not _path_clear(zones, a, goal):
                    continue
                at = {**a, 'position': goal}
                back = [math.dist(goal, s['position']) for s in stations if _path_clear(zones, at, s['position'])]
                if not back:
                    continue
                priority = t.get('priority', priorities.get(t['id'], 0))
                length = dist + min(back)
                speed = self.speeds.get(a['id'])
                # Heuristic optimistic travel time (service overhead omitted).
                # 2 m is the two 1 m tolerances already used by PortablePolicy.
                if speed and horizon is not None and max(0., length - 2) / (1.05 * speed) > horizon - observation['time']:
                    excluded.append({'agent_id': a['id'], 'target_id': t['id'], 'distance_m': length,
                                     'speed_m_s': speed, 'remaining_s': horizon - observation['time']})
                    continue
                empty_rate = self.rate(a, False)
                loaded_rate = self.rate(a, True) or empty_rate * (1.35 if self.variant in ("guard-buffer","localguard") else 1.)
                energy = dist * empty_rate + min(back) * loaded_rate
                resource = a['resources']['energy']
                available = resource['value'] - resource['minimum']
                if self.variant in ('guard','balanced','service','efficient','hybrid','adaptive','adaptive-service','guard-buffer','localguard') and energy * 1.10 > available:
                    energy_filtered.append({'agent_id': a['id'], 'target_id': t['id'], 'estimated_energy': energy, 'available': available})
                    continue
                if self.variant in ('service','hybrid','adaptive-service') and measured and horizon is not None:
                    overhead = self.service_time(a['id'])
                    if overhead is not None and max(0., length - 2) / (1.05 * speed) + overhead > horizon - observation['time']:
                        excluded.append({'agent_id': a['id'], 'target_id': t['id'], 'estimated_service_s': overhead})
                        continue
                cost = length / speed if measured else length
                # Energy equivalent distance (normalized within each observed unit).
                if self.variant in ('balanced','efficient','hybrid') or self.variant in ('adaptive','adaptive-service') and any(self._usable_energy(x) < .25 for x in crew):
                    equivalent = energy / reference
                    if measured:
                        equivalent /= median(self.speeds[x['id']] for x in crew)
                    alpha = .5 if self.variant in ('balanced','hybrid','adaptive','adaptive-service') else 2.
                    cost += alpha * equivalent
                edges[i, j] = (max(0, round(priority * 1000)), round(cost * 1000))
        if not edges:
            if not energy_filtered and (not excluded or not measured):
                return {}
            result, work = {a['id']: None for a in crew}, 0
        else:
            n = len(crew)
            cost_bound = n * max(d for p, d in edges.values()) + 1
            count_weight = n * max(p for p, d in edges.values()) * cost_bound + cost_bound
            costs = [[(-count_weight - p * cost_bound + d if (i, j) in edges else None)
                      for j in range(len(targets)) for p, d in [edges.get((i, j), (0, 0))]] + [0] * n for i in range(n)]
            try:
                chosen, _, _, work = minimum_assignment(costs)
            except AssignmentError:
                return {}
            result = {a['id']: targets[j]['id'] if j < len(targets) else None for a, j in zip(crew, chosen)}
        self.traces.append({'kind': 'flow_wave', 'type': 'portable_flow_wave', 'method': 'resource_predictor_v1', 'variant': self.variant, 'energy_filtered': energy_filtered,
                            'episode_id': self.episode, 'time': observation['time'], 'request_id': observation['request_id'],
                            'matches': result, 'relaxations': work, 'cost_unit': 'millisecond' if measured else 'millimetre',
                            'observed_speed_m_s': {a['id']: self.speeds[a['id']] for a in crew if a['id'] in self.speeds},
                            'deadline_filtered': excluded,
                            'candidate_edges': [{'agent_id': crew[i]['id'], 'target_id': targets[j]['id'],
                                                 'priority_milli': p, 'cost_milli': d} for (i, j), (p, d) in edges.items()]})
        return result
