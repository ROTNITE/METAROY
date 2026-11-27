"""Opt-in Mission Flow refinement based exclusively on public Core observations.

The observed speed is an empirical heuristic, not a certified physical bound.
The deadline gate can therefore decline an otherwise valid future task. Native
EvolutionPolicy is not changed. The staged patrol is separately opt-in.
"""
from __future__ import annotations

import math
from controller.portable import PortablePolicy, point, horizon_of, minimum_assignment, AssignmentError, _path_clear
from controller.net65 import due
from controller.public_core import PATROL_ESTIMATES


class BottleneckPolicy(PortablePolicy):
    def __init__(self, capabilities, requested_policy, *, scout_patrol='core', public=None, staged_patrol=False):
        self.staged_patrol = staged_patrol
        super().__init__(capabilities, requested_policy, scout_patrol=scout_patrol, public=public, flow_dispatch=True)

    def _reset(self, episode):
        super()._reset(episode)
        self.speeds = {}
        self.last_motion = {}
        self.visits = {}
        self.scout_enabled = False

    def _profile(self):
        return (self.public.identity and not self.public.short_actions and
                {'pickup', 'handover', 'move'} <= self.actions.keys())

    @staticmethod
    def _usable_energy(agent):
        e = agent['resources'].get('energy')
        if not e:
            return None
        span = e.get('maximum', e['minimum']) - e['minimum']
        return (e['value'] - e['minimum']) / span if span > 0 else None

    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self._reset(observation['episode_id'])
            self.started = observation['time']
        if self._profile():
            now = observation['time']
            for agent in observation['agents']:
                aid, pos = agent['id'], agent.get('position')
                connected = agent['communication'].get('reachable', agent['communication'].get('connected', True)) is not False
                previous, pending = self.last_motion.get(aid), self.pending.get(aid)
                if pos is not None and connected and agent['kind'] == 'ground':
                    if previous and pending and pending[3][1] == 'move' and 0 < now - previous[0] <= 2:
                        speed = math.dist(pos, previous[1]) / (now - previous[0])
                        if math.isfinite(speed) and speed > 1e-6:
                            self.speeds[aid] = max(speed, self.speeds.get(aid, 0))
                    self.last_motion[aid] = (now, tuple(pos))
                else:
                    self.last_motion.pop(aid, None)
        carriers = [a for a in observation['agents'] if a['kind'] == 'ground' and 'carry' in a['capabilities']]
        self.scout_enabled = (self.staged_patrol and self.scout_patrol == 'net65' and self._profile() and
                              PATROL_ESTIMATES in self.public.extensions and len(observation['zones']) <= 64 and
                              not observation['observation_quality']['contradiction_count'] and bool(carriers) and
                              all((e := self._usable_energy(a)) is not None and e >= .25 for a in carriers))
        return super().decide(observation)

    def _patrol_due(self, now, start, horizon, coverage_complete):
        if not self.scout_enabled:
            return due(now, start, horizon, coverage_complete)
        return coverage_complete and horizon is not None and start < horizon and now >= start + .40 * (horizon - start)

    def _scout_points(self, zones, agent):
        points = list(super()._scout_points(zones, agent))
        if not self.scout_enabled or agent['kind'] != 'aerial' or len(points) > 64:
            return points
        # Connected component of the public, straight-line feasible candidate
        # scan locations. Does not claim the rest of the map was covered.
        reached, frontier, remaining = [], [tuple(agent['position'])], set(points)
        while frontier:
            origin = frontier.pop()
            accessible = sorted(p for p in remaining if _path_clear(zones, {**agent, 'position': origin}, p))
            reached.extend(accessible)
            frontier.extend(accessible)
            remaining.difference_update(accessible)
        return reached

    def observe_response(self, response):
        before = self.scanned_at.copy()
        super().observe_response(response)
        for p, stamp in self.scanned_at.items():
            if p in before and stamp > before[p]:
                self.visits[p] = self.visits.get(p, 0) + 1

    def _repeat_sector(self, observation, zones, agent, points, claimed, scouts):
        if self.scout_enabled:
            horizon = horizon_of(observation['mission'])
            fraction = (observation['time'] - self.started) / (horizon - self.started) if horizon and horizon > self.started else 1.
            cap = 1 if fraction < .55 else 2 if fraction < .70 else 3
            if fraction >= .70:
                free = sum(a['kind'] == 'ground' and 'carry' in a['capabilities'] and a['status'] == 'active' and
                           a['id'] not in self.pending and a['id'] not in self.cargo and
                           a['communication'].get('reachable', True) for a in observation['agents'])
                waiting = sum(t.get('kind') == 'person' and t.get('status') == 'confirmed' and
                              t.get('evacuation_state', 'waiting') == 'waiting' and
                              ('casualty', t['id']) not in claimed for t in observation['targets'])
                if free > waiting:
                    cap += 2
            if sum(self.visits.values()) >= cap:
                return None, 'repeat_budget_used'
            points = [p for p in points if self.visits.get(p, 0) == 0]
            if not points:
                return None, 'repeat_pass_complete'
        return super()._repeat_sector(observation, zones, agent, points, claimed, scouts)

    def _flow_wave(self, observation, zones, agents, claimed, orders):
        if (not self._profile() or self.program or orders or observation['observation_quality']['contradiction_count']):
            return super()._flow_wave(observation, zones, agents, claimed, orders)
        crew = [a for a in agents if a['id'] not in self.pending and a['id'] not in self.cargo and
                'carry' in a['capabilities'] and a['kind'] == 'ground' and
                (not a['resources'].get('energy') or a['resources']['energy']['value'] > a['resources']['energy']['minimum']) and
                not any(c['status'] == 'failed' for c in self._constraint_checks(observation, a))]
        for a in crew:
            if a['resources'].get('energy') and ((e := self._usable_energy(a)) is None or e < .25):
                return {}
        targets = sorted((t for t in observation['targets'] if t.get('kind') == 'person' and
                          t.get('status') == 'confirmed' and t.get('position') is not None and
                          t.get('evacuation_state', 'waiting') == 'waiting' and
                          ('casualty', t['id']) not in claimed and ('casualty', t['id']) not in self.completed),
                         key=lambda t: t['id'])
        stations = [s for s in observation['infrastructure'] if s['kind'] == 'base_station' and s['status'] == 'active']
        if not (1 <= len(crew) <= 32 and 1 <= len(targets) <= 64 and stations):
            return {}
        edges, excluded = {}, []
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
                cost = length / speed if measured else length
                edges[i, j] = (max(0, round(priority * 1000)), round(cost * 1000))
        if not edges:
            if not excluded or not measured:
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
        self.traces.append({'kind': 'flow_wave', 'type': 'portable_flow_wave', 'method': 'bottleneck_v1',
                            'episode_id': self.episode, 'time': observation['time'], 'request_id': observation['request_id'],
                            'matches': result, 'relaxations': work, 'cost_unit': 'millisecond' if measured else 'millimetre',
                            'observed_speed_m_s': {a['id']: self.speeds[a['id']] for a in crew if a['id'] in self.speeds},
                            'deadline_filtered': excluded,
                            'candidate_edges': [{'agent_id': crew[i]['id'], 'target_id': targets[j]['id'],
                                                 'priority_milli': p, 'cost_milli': d} for (i, j), (p, d) in edges.items()]})
        return result
