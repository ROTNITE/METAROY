"""Isolated, opt-in successors to the frozen Counterfactual Value policy.

These are research candidates, not replacements for the product controller.
No environment, hidden target, scenario seed, or family label is imported.
The short rollout is an approximate public-state forecast; its conservative
acceptance rule is not a policy-improvement guarantee in the real simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from controller.counterfactual_final import CounterfactualValuePolicy
from controller.portable import horizon_of


@dataclass(frozen=True)
class Projection:
    priority: float
    count: int
    energy: float
    completion: float

    @property
    def mean_completion(self):
        return self.completion / max(1, self.count)


class SuccessorPolicy(CounterfactualValuePolicy):
    """CV with separately ablatable survey pricing and bounded task rollout.

    queue_weight and survey_price use existing WaveSurvey mechanisms. The
    inherited high-value option deliberately retains its original behavior.
    rollout_depth is complete rescue trips per currently free carrier, 0..3.
    A depth of zero, with both survey knobs zero, preserves the frozen policy.
    The rollout activates only inside the original route-aware public profile
    and only after speed and energy telemetry are available for every actor.
    """

    def __init__(self, *args, queue_weight=0., survey_price=0.,
                 rollout_depth=0, rollout_candidates=32,
                 energy_tolerance=0., time_tolerance=0.,
                 improvement_margin=.01, **kwargs):
        if not 0 <= queue_weight <= 1 or not 0 <= survey_price <= 2:
            raise ValueError("survey knob range")
        if rollout_depth not in (0, 1, 2, 3):
            raise ValueError("rollout_depth must be 0, 1, 2, or 3")
        if not 1 <= rollout_candidates <= 64:
            raise ValueError("rollout candidate budget")
        if not 0 <= energy_tolerance <= .1 or not 0 <= time_tolerance <= .1:
            raise ValueError("relative tolerance range")
        if not 0 <= improvement_margin <= .2:
            raise ValueError("improvement margin range")
        self.rollout_depth = int(rollout_depth)
        self.rollout_candidates = int(rollout_candidates)
        self.energy_tolerance = float(energy_tolerance)
        self.time_tolerance = float(time_tolerance)
        self.improvement_margin = float(improvement_margin)
        super().__init__(*args, **kwargs)
        self.queue_weight = float(queue_weight)
        self.price = float(survey_price)

    def _trip(self, observation, agent, target, position, energy, ready):
        """One full out/pickup/return/handover trip from public estimates."""
        resource = agent['resources'].get('energy')
        speed = self.speeds.get(agent['id'])
        empty, loaded = self._rates(agent)
        if (resource is None or not speed or speed <= 0 or empty is None or
                loaded is None or empty <= 0 or loaded <= 0):
            return None
        projected = dict(agent, position=list(position))
        projected['resources'] = dict(agent['resources'])
        projected['resources']['energy'] = dict(resource, value=energy + resource['minimum'])
        frame = dict(observation, time=ready)
        estimate = self._estimate(frame, projected, target)
        if estimate is None:
            return None
        outward, returning = estimate[4:6]
        cost = outward * empty + returning * loaded
        if not math.isfinite(cost) or 1.10 * cost > energy:
            return None
        service = self.service_time(agent['id'])
        # A measured service includes the controller's actual ACK delay. Four
        # seconds is the inherited research prior, not a private simulator read.
        overhead = max(4., service if service is not None else 4.)
        duration = (outward + returning) / speed + overhead
        horizon = horizon_of(observation['mission'])
        if not math.isfinite(duration) or duration <= 0 or ready + duration > horizon:
            return None
        near = (math.dist(position, target['position']) <= 1 and
                self.router.clear(position, target['position']))
        if near:
            arrival = tuple(position)
        else:
            path, arrival = self._approach(position, target['position'])
            if path is None:
                return None
        back = self._back(frame, arrival)
        if back is None:
            return None
        path, endpoint = self._approach(arrival, back[3]['position'])
        if path is None:
            return None
        return duration, cost, tuple(endpoint)

    def _projection(self, observation, crew, targets, first, trips):
        """Evaluate a first wave plus a deterministic bounded continuation."""
        now = observation['time']
        horizon = horizon_of(observation['mission'])
        objectives = {o.get('target_id'): o for o in observation['mission']['objectives']
                      if o.get('type') == 'rescue'}
        ready = [now] * len(crew)
        position = [tuple(a['position']) for a in crew]
        budget = [a['resources']['energy']['value'] - a['resources']['energy']['minimum']
                  for a in crew]
        used = set()
        counts = [0] * len(crew)
        active = {i for i, target_id in enumerate(first) if target_id is not None}
        primary = spent = completed_at = 0.
        count = 0

        def priority(target, finish):
            objective = objectives.get(target['id'], {})
            value = target.get('priority', objective.get('priority', 1.))
            deadline = objective.get('deadline_s', objective.get('deadline', horizon))
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                return None
            if type(deadline) not in (float, int) or not math.isfinite(deadline):
                deadline = horizon
            # Smooth boundary avoids treating an uncertain ETA as a certificate
            # of deadline feasibility. Real score is measured by the harness.
            ontime = max(0., min(1., .5 + (min(deadline, horizon) - finish) / 12.))
            return value * ontime

        def estimate(i, tid):
            key = (crew[i]['id'], tid, position[i], round(budget[i], 10), round(ready[i], 8))
            if key not in trips:
                trips[key] = self._trip(observation, crew[i], targets[tid], position[i], budget[i], ready[i])
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
        # First actions remain simultaneous. The continuation fills the earliest
        # release next, and never pretends that a deliberately idle first actor
        # executes an unreported action in this decision.
        while active:
            i = min(active, key=lambda j: (ready[j], crew[j]['id']))
            if counts[i] >= self.rollout_depth:
                active.remove(i)
                continue
            alternatives = []
            for tid, target in targets.items():
                if tid in used:
                    continue
                trip = estimate(i, tid)
                if trip is None:
                    continue
                reward = priority(target, ready[i] + trip[0])
                if reward is None:
                    continue
                alternatives.append((reward / trip[0], -trip[0], -trip[1], tid, trip))
            if not alternatives:
                active.remove(i)
                continue
            _, _, _, tid, trip = max(alternatives)
            if not apply(i, tid, trip):
                return None
        return Projection(primary, count, spent, completed_at)

    def _acceptable(self, candidate, incumbent):
        if candidate is None or incumbent is None or candidate.count < incumbent.count:
            return False
        if candidate.priority + 1e-9 < incumbent.priority:
            return False
        if candidate.energy > incumbent.energy * (1 + self.energy_tolerance) + 1e-10:
            return False
        if candidate.mean_completion > incumbent.mean_completion * (1 + self.time_tolerance) + 1e-8:
            return False
        margin = self.improvement_margin
        return (candidate.priority > incumbent.priority + margin or
                candidate.count > incumbent.count or
                candidate.energy < incumbent.energy * (1 - margin) - 1e-10 or
                candidate.mean_completion < incumbent.mean_completion * (1 - margin) - 1e-8)

    def _flow_wave(self, observation, zones, agents, claimed, orders):
        baseline = super()._flow_wave(observation, zones, agents, claimed, orders)
        if (not self.rollout_depth or not self.route_active or not baseline or
                not any(v is not None for v in baseline.values()) or orders or self.program):
            return baseline
        now = observation['time']
        horizon = horizon_of(observation['mission'])
        if horizon is None or horizon - now < 12:
            return baseline
        # Preserve the incumbent's explicitly bounded information-wait window.
        if self.low_initial and now - self.started < self.coord_window and not self.scanned_at:
            return baseline
        crew = sorted((a for a in agents if a['id'] in baseline), key=lambda a: a['id'])
        targets = {t['id']: t for t in sorted(observation['targets'], key=lambda t: t['id'])
                   if t.get('kind') == 'person' and t.get('status') == 'confirmed' and
                   t.get('position') is not None and t.get('evacuation_state', 'waiting') == 'waiting' and
                   ('casualty', t['id']) not in claimed and ('casualty', t['id']) not in self.completed}
        if not 1 <= len(crew) <= 6 or not 2 <= len(targets) <= 20:
            return baseline
        for agent in crew:
            if (agent['id'] in self.pending or agent['id'] in self.cargo or
                    agent['status'] not in ('active', 'degraded') or
                    not agent['communication'].get('reachable', False) or
                    agent['resources'].get('energy') is None or
                    self.speeds.get(agent['id'], 0) <= 0 or
                    any(v is None for v in self._rates(agent))):
                return baseline
        first = tuple(baseline[a['id']] for a in crew)
        trips = {}
        incumbent = self._projection(observation, crew, targets, first, trips)
        if incumbent is None:
            return baseline
        plans = [first]
        seen = {first}
        # One reassignment and pair swaps form a small neighborhood around the
        # unchanged baseline. A future loop may select another move next tick.
        for i, agent in enumerate(crew):
            options = []
            energy = agent['resources']['energy']['value'] - agent['resources']['energy']['minimum']
            for tid, target in targets.items():
                if tid in first and tid != first[i]:
                    continue
                trip = self._trip(observation, agent, target, agent['position'], energy, now)
                if trip is not None:
                    options.append((trip[0], trip[1], tid))
            for _, _, tid in sorted(options)[:5]:
                plan = first[:i] + (tid,) + first[i + 1:]
                if plan not in seen:
                    plans.append(plan)
                    seen.add(plan)
        for i in range(len(crew)):
            for j in range(i):
                if first[i] is None or first[j] is None:
                    continue
                plan = list(first)
                plan[i], plan[j] = plan[j], plan[i]
                plan = tuple(plan)
                if plan not in seen:
                    plans.append(plan)
                    seen.add(plan)
        chosen, best = first, incumbent
        evaluated = 1
        for plan in plans[1:self.rollout_candidates]:
            forecast = self._projection(observation, crew, targets, plan, trips)
            evaluated += 1
            if (self._acceptable(forecast, incumbent) and
                    (chosen == first or (forecast.priority, forecast.count, -forecast.energy, -forecast.mean_completion) >
                     (best.priority, best.count, -best.energy, -best.mean_completion))):
                chosen, best = plan, forecast
        result = dict(baseline)
        result.update((a['id'], tid) for a, tid in zip(crew, chosen))
        if result != baseline:
            self.traces.append({'kind': 'successor_rollout', 'type': 'successor_rollout',
                                'episode_id': observation['episode_id'], 'request_id': observation['request_id'],
                                'time': now, 'baseline': baseline, 'matches': result,
                                'depth': self.rollout_depth, 'evaluated': evaluated,
                                'projected_before': incumbent.__dict__, 'projected_after': best.__dict__,
                                'basis': 'public_known_targets_telemetry_and_incumbent_forecast'})
        return result
