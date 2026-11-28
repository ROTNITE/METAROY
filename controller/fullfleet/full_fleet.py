"""Bounded public-state rollout with already committed carriers' future capacity.

Only initially free carriers receive a different first assignment. A busy
carrier's accepted waypoint and rescue remain fixed. Its conditional release is
included identically in the incumbent and challenger forecasts, after reserving
the remaining trip's energy. This is an approximate forecast, not a certificate
of future service completion or a native 3D adapter.

No simulator, private specification, scenario identifier or evaluator is read.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from statistics import median

from controller.portable import horizon_of
from controller.fullfleet.dispatch import EfficiencyDispatchPolicy
from controller.fullfleet.policies import Projection


@dataclass(frozen=True)
class BusyRelease:
    agent: dict
    target_id: str
    ready: float
    position: tuple
    energy: float
    committed_energy: float
    phase: str


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class FullFleetPolicy(EfficiencyDispatchPolicy):
    """Extend the frozen first-wave neighborhood with bounded busy releases.

    The same continuation heuristic, trip estimators and admission rules are
    used as in the free-only successor. No eligible busy release means exactly
    the existing free-only projection. A release requires measured empty and
    loaded rates, measured pickup and handover delays, a matching own accepted
    command or acknowledged cargo, and previously observed idle capabilities.

    Previously observed idle actions are used only in a hypothetical post-trip
    agent. Current observations, pending commands, cargo and commands sent to the
    environment retain their original states. The committed trip's remaining
    cost reduces future usable energy but its fixed reward/cost is not added to
    discretionary continuation scores; this constant is common to both plans.
    """

    def __init__(self, *args, all_routes=False, **kwargs):
        self.all_routes = bool(all_routes)
        super().__init__(*args, **kwargs)
        if self.all_routes:
            self.route_mode = 'dock-1.8'

    def _reset(self, episode):
        super()._reset(episode)
        self.fleet_counts = Counter()
        self._fleet_idle = {}
        self._fleet_commands = {}
        self._fleet_context = None

    def decide(self, observation):
        before = (self.fleet_counts.copy() if self.episode == observation['episode_id']
                  else Counter())
        result = super().decide(observation)
        delta = self.fleet_counts - before
        if delta:
            releases = self._fleet_context or ()
            self.traces.append({
                'kind': 'successor_full_fleet', 'type': 'successor_full_fleet',
                'episode_id': observation['episode_id'], 'request_id': observation['request_id'],
                'decision_id': result['decision_id'], 'time': observation['time'],
                'counts': dict(delta), 'all_routes': self.all_routes,
                'releases': [{'agent_id': r.agent['id'], 'target_id': r.target_id,
                              'ready_at': r.ready, 'ready_position': list(r.position),
                              'remaining_energy': r.energy,
                              'committed_energy': r.committed_energy, 'phase': r.phase}
                             for r in releases],
                'basis': 'public_telemetry_own_accepted_commitments_conditional_release',
            })
        return result

    def observe_response(self, response):
        request, intents = self.proposed
        for index, (command, (intent, _)) in enumerate(zip(request['commands'], intents)):
            if (command['action'] in ('move', 'pickup', 'handover') and intent and
                    intent[0] == 'casualty'):
                key = (request['decision_id'], index)
                self._fleet_commands[key] = {
                    'agent_id': command['agent_id'], 'action': command['action'],
                    'parameters': dict(command['parameters']), 'target_id': intent[1],
                    'sent_at': request['time'], 'accepted': False,
                }
        for receipt in response['command_results']:
            key = (receipt.get('decision_id'), receipt.get('command_index'))
            record = self._fleet_commands.get(key)
            if (record is None or receipt.get('agent_id') != record['agent_id'] or
                    receipt.get('action') != record['action']):
                continue
            if (receipt.get('execution_status') in ('completed', 'failed', 'cancelled') or
                    receipt.get('validation_status') == 'rejected' or
                    receipt.get('delivery_status') in ('lost', 'expired')):
                self._fleet_commands.pop(key, None)
            elif (receipt.get('validation_status') == 'accepted' and
                  receipt.get('delivery_status') == 'delivered'):
                record['accepted'] = True
        self._fleet_commands = {key: row for key, row in self._fleet_commands.items()
                                if 0 <= response['time'] - row['sent_at'] <= 120}
        super().observe_response(response)

    def _signature(self, agent):
        return (tuple(sorted(agent['capabilities'])), agent.get('role'),
                tuple(sorted(agent.get('scenario_ids', ()))), self.resource_signature(agent))

    def _flow_wave(self, observation, zones, agents, claimed, orders):
        # One immutable release context is shared by all first-wave candidates.
        self._fleet_context = None
        if self.route_active and not orders and not self.program:
            for agent in observation['agents']:
                aid = agent['id']
                actions = {row['action'] for row in agent['available_actions']}
                if (agent['kind'] == 'ground' and 'carry' in agent['capabilities'] and
                        agent['status'] in ('active', 'degraded') and
                        agent['communication'].get('reachable', False) and
                        aid not in self.pending and aid not in self.cargo and
                        {'move', 'pickup', 'handover'} <= actions):
                    self._fleet_idle[aid] = (self._signature(agent),
                                             [dict(row) for row in agent['available_actions']])
        return super()._flow_wave(observation, zones, agents, claimed, orders)

    def _busy_release(self, observation, agent):
        aid = agent['id']

        def skip(reason):
            self.fleet_counts['skip_' + reason] += 1
            return None

        if (agent.get('position') is None or agent['status'] not in ('active', 'busy', 'degraded') or
                not agent['communication'].get('reachable', False)):
            return skip('unavailable_actor')
        idle = self._fleet_idle.get(aid)
        if idle is None or idle[0] != self._signature(agent):
            return skip('unknown_idle_capabilities')
        resource = agent['resources'].get('energy')
        speed = self.speeds.get(aid)
        if (resource is None or not finite(resource.get('value')) or
                not finite(resource.get('minimum')) or not finite(speed) or speed <= 0):
            return skip('unknown_resources_or_speed')
        # Do not create busy capacity using the initial inferred load multiplier.
        signature = self.resource_signature(agent)
        rates = []
        for loaded in (False, True):
            samples = [v for v in self.energy_samples.get((aid, signature, loaded), ())
                       if finite(v) and v > 0]
            if len(samples) < 3:
                return skip('unmeasured_load_rates')
            rates.append(median(samples))
        service = []
        for action in ('pickup', 'handover'):
            samples = [v for v in self.service_samples.get((aid, action), ())
                       if finite(v) and 0 <= v <= 120]
            if not samples:
                return skip('unmeasured_service')
            service.append(max(2., median(samples)))

        now = observation['time']
        position = tuple(agent['position'])
        if len(position) != 3 or not all(finite(x) for x in position):
            return skip('invalid_position')
        pending = self.pending.get(aid)
        cargo = self.cargo.get(aid)
        record = None
        prefix = 0.
        phase = 'return_cargo'
        if pending:
            record = self._fleet_commands.get(pending[:2])
            if (not record or not record['accepted'] or record['agent_id'] != aid or
                    record['action'] != pending[3][1] or not pending[2] or
                    pending[2] != ('casualty', record['target_id']) or
                    not 0 <= now - record['sent_at'] <= 120):
                return skip('unconfirmed_command')
            phase = record['action']
            if cargo is not None and record['target_id'] != cargo:
                return skip('contradictory_cargo')
            if phase == 'move':
                accepted = self.accepted_moves.get(pending[:2])
                waypoint = tuple(record['parameters']['target'])
                if (accepted is None or accepted[0] != aid or tuple(accepted[1]) != waypoint or
                        not self.router.clear(position, waypoint)):
                    return skip('unknown_accepted_path')
                # Existing movement cannot be rerouted by the forecast.
                prefix = math.dist(position, waypoint)
                position = waypoint
            elif phase not in ('pickup', 'handover'):
                return skip('unsupported_commitment')
        elif cargo is None:
            return skip('no_confirmed_commitment')
        elif agent['status'] == 'busy':
            return skip('unknown_busy_action')

        target_id = cargo if cargo is not None else record['target_id']
        if cargo is not None:
            if phase == 'pickup':
                return skip('contradictory_phase')
            outward = 0.
            cost = prefix * rates[1]
            duration = prefix / speed
            if phase == 'handover':
                station_id = record['parameters'].get('station_id')
                stations = [s for s in observation['infrastructure'] if s['id'] == station_id and
                            s['kind'] == 'base_station' and s['status'] == 'active' and
                            s.get('position') is not None]
                if (len(stations) != 1 or math.dist(position, stations[0]['position']) > 1 or
                        not self.router.clear(position, stations[0]['position'])):
                    return skip('unknown_handover_station')
                back = None
                endpoint = position
            else:
                back = self._back(observation, position)
            duration += service[1]
        else:
            if phase not in ('move', 'pickup'):
                return skip('missing_acknowledged_cargo')
            targets = {t['id']: t for t in observation['targets'] if t.get('kind') == 'person' and
                       t.get('status') == 'confirmed' and t.get('position') is not None and
                       t.get('evacuation_state', 'waiting') == 'waiting'}
            target = targets.get(target_id)
            if target is None:
                return skip('unknown_committed_target')
            if phase == 'pickup' and (record['parameters'].get('target_id') != target_id or
                                      math.dist(position, target['position']) > 1):
                return skip('invalid_pickup_position')
            path, arrival = self._approach(position, target['position'])
            if path is None:
                return skip('unreachable_commitment')
            outward = prefix + path[0]
            cost = outward * rates[0]
            duration = outward / speed + sum(service)
            position = tuple(arrival)
            back = self._back(observation, position)

        if phase != 'handover':
            if back is None:
                return skip('missing_return_route')
            path, endpoint = self._approach(position, back[3]['position'])
            if path is None:
                return skip('missing_dock_position')
            cost += back[0] * rates[1]
            duration += back[0] / speed
        usable = resource['value'] - resource['minimum']
        horizon = horizon_of(observation['mission'])
        if (not finite(cost) or not finite(duration) or duration <= 0 or
                1.10 * cost > usable or usable - cost <= 0 or
                horizon is None or now + duration >= horizon):
            return skip('no_remaining_capacity')
        # This copy describes only the conditional state AFTER the obligation.
        # It is never returned as an action authorization for the current actor.
        projected = dict(agent, status='active', position=list(endpoint),
                         available_actions=[dict(row) for row in idle[1]])
        projected['resources'] = dict(agent['resources'])
        projected['resources']['energy'] = dict(resource, value=resource['value'] - cost)
        return BusyRelease(projected, target_id, now + duration, tuple(endpoint),
                           usable - cost, cost, phase)

    def _releases(self, observation, crew):
        if self._fleet_context is None:
            free = {a['id'] for a in crew}
            busy = [a for a in sorted(observation['agents'], key=lambda a: a['id'])
                    if a['id'] not in free and a['kind'] == 'ground' and
                    'carry' in a['capabilities'] and
                    (a['id'] in self.pending or a['id'] in self.cargo)]
            self._fleet_context = tuple(r for a in busy if (r := self._busy_release(observation, a)) is not None)
            self.fleet_counts['release_contexts'] += 1
            self.fleet_counts['eligible_busy_carriers'] += len(self._fleet_context)
        return self._fleet_context

    def _projection(self, observation, crew, targets, first, trips):
        releases = self._releases(observation, crew)
        if not releases:
            self.fleet_counts['free_only_projections'] += 1
            return super()._projection(observation, crew, targets, first, trips)
        self.dispatch_counts['plan_projections'] += 1
        self.fleet_counts['full_fleet_projections'] += 1
        if len(crew) + len(releases) > 6:
            self.fleet_counts['total_crew_cap'] += 1
            return None
        now = observation['time']
        horizon = horizon_of(observation['mission'])
        objectives = {o.get('target_id'): o for o in observation['mission']['objectives']
                      if o.get('type') == 'rescue'}
        fleet = [*crew, *(r.agent for r in releases)]
        ready = [now] * len(crew) + [r.ready for r in releases]
        position = [tuple(a['position']) for a in crew] + [r.position for r in releases]
        budget = [a['resources']['energy']['value'] - a['resources']['energy']['minimum']
                  for a in crew] + [r.energy for r in releases]
        used = set(self.cargo.values()) | {p[2][1] for p in self.pending.values()
                                         if p[2] and p[2][0] == 'casualty'}
        counts = [0] * len(fleet)
        active = {i for i, tid in enumerate(first) if tid is not None}
        active.update(range(len(crew), len(fleet)))
        primary = spent = completed_at = 0.
        count = 0

        def priority(target, finish):
            objective = objectives.get(target['id'], {})
            value = target.get('priority', objective.get('priority', 1.))
            deadline = objective.get('deadline_s', objective.get('deadline', horizon))
            if not finite(value) or value < 0:
                return None
            if not finite(deadline):
                deadline = horizon
            return value * max(0., min(1., .5 + (min(deadline, horizon) - finish) / 12.))

        def estimate(i, tid):
            key = (fleet[i]['id'], tid, position[i], round(budget[i], 10), round(ready[i], 8))
            if key not in trips:
                trips[key] = self._trip(observation, fleet[i], targets[tid],
                                        position[i], budget[i], ready[i])
            return trips[key]

        def apply(i, tid, trip):
            nonlocal primary, spent, completed_at, count
            duration, energy, endpoint = trip
            finish = ready[i] + duration
            reward = priority(targets[tid], finish)
            if reward is None:
                return False
            ready[i], position[i] = finish, endpoint
            budget[i] -= energy
            counts[i] += 1
            used.add(tid)
            primary += reward
            spent += energy
            completed_at += finish - now
            count += 1
            return True

        for i, tid in enumerate(first):
            if tid is None:
                continue
            if tid not in targets or tid in used:
                return None
            trip = estimate(i, tid)
            if trip is None or not apply(i, tid, trip):
                return None
        # Busy carriers become forecast actors only AFTER their fixed handover.
        # Their future tasks are advisory; no busy first assignment is returned.
        while active:
            i = min(active, key=lambda j: (ready[j], fleet[j]['id']))
            if counts[i] >= self.rollout_depth:
                active.remove(i)
                continue
            choices = []
            for tid, target in targets.items():
                if tid in used:
                    continue
                trip = estimate(i, tid)
                if trip is None:
                    continue
                reward = priority(target, ready[i] + trip[0])
                if reward is not None:
                    choices.append((reward / trip[0], -trip[0], -trip[1], tid, trip))
            if not choices:
                active.remove(i)
                continue
            _, _, _, tid, trip = max(choices)
            if not apply(i, tid, trip):
                return None
        return Projection(primary, count, spent, completed_at)
