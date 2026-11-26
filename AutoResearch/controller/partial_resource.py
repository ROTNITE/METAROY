"""Experimental partial-information dispatch: known costs constrain only known edges."""
import math
from statistics import median
from controller.adaptive_survey import AdaptiveSurveyPolicy
from controller.portable import point,horizon_of,minimum_assignment,AssignmentError,_path_clear

class PartialResourcePolicy(AdaptiveSurveyPolicy):
    def _flow_wave(self, observation, zones, agents, claimed, orders):
        if (not self._profile() or self.program or orders or observation['observation_quality']['contradiction_count']):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        crew = [a for a in agents if a['id'] not in self.pending and a['id'] not in self.cargo and
                'carry' in a['capabilities'] and a['kind'] == 'ground' and
                (not a['resources'].get('energy') or a['resources']['energy']['value'] > a['resources']['energy']['minimum']) and
                not any(c['status'] == 'failed' for c in self._constraint_checks(observation, a))]
        if not crew or not any(self.rate(a, False) is not None for a in crew) or any(self.resource_signature(a) is None for a in crew) or len({self.resource_signature(a)[0] for a in crew}) != 1:
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        if any(self._usable_energy(a) is None for a in crew):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        if self.variant not in ('guard','adaptive','adaptive-service','guard-buffer','localguard') and any(self._usable_energy(a) < .25 for a in crew):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        reference = median(r for a in crew if (r:=self.rate(a,False)) is not None)
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
                loaded_rate = self.rate(a, True) or (1.35*empty_rate if empty_rate is not None else None)
                energy = dist * empty_rate + min(back) * loaded_rate if empty_rate is not None and loaded_rate is not None else None
                resource = a['resources']['energy']
                available = resource['value'] - resource['minimum']
                if self.variant in ('guard','balanced','service','efficient','hybrid','adaptive','adaptive-service','guard-buffer','localguard') and energy is not None and energy * 1.10 > available:
                    energy_filtered.append({'agent_id': a['id'], 'target_id': t['id'], 'estimated_energy': energy, 'available': available})
                    continue
                if self.variant in ('service','hybrid','adaptive-service') and measured and horizon is not None:
                    overhead = self.service_time(a['id'])
                    if overhead is not None and max(0., length - 2) / (1.05 * speed) + overhead > horizon - observation['time']:
                        excluded.append({'agent_id': a['id'], 'target_id': t['id'], 'estimated_service_s': overhead})
                        continue
                cost = length / speed if measured else length
                # Energy equivalent distance (normalized within each observed unit).
                if energy is not None and self.variant in ('balanced','efficient','hybrid','adaptive','adaptive-service'):
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
        self.traces.append({'kind': 'flow_wave', 'type': 'portable_flow_wave', 'method': 'partial_resource_wave', 'variant': self.variant, 'energy_filtered': energy_filtered,
                            'episode_id': self.episode, 'time': observation['time'], 'request_id': observation['request_id'],
                            'matches': result, 'relaxations': work, 'cost_unit': 'millisecond' if measured else 'millimetre',
                            'observed_speed_m_s': {a['id']: self.speeds[a['id']] for a in crew if a['id'] in self.speeds},
                            'deadline_filtered': excluded,
                            'candidate_edges': [{'agent_id': crew[i]['id'], 'target_id': targets[j]['id'],
                                                 'priority_milli': p, 'cost_milli': d} for (i, j), (p, d) in edges.items()]})
        return result
