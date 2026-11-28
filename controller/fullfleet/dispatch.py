"""Research dispatch ablations, separated from the frozen initial screen.

An efficient additional rescue may spend more total energy. The per-rescue arm
permits that forecast tradeoff explicitly; it must not be described as improving
absolute energy consumption. No private world or evaluator state is available.
"""
from __future__ import annotations

from collections import Counter
import math

from controller.portable import horizon_of
from controller.fullfleet.policies import SuccessorPolicy
from controller.fullfleet.variants import TelemetryPolicy


class EfficiencyDispatchPolicy(TelemetryPolicy, SuccessorPolicy):
    """CV rollout with a forecast energy-per-rescue admission criterion.

    Both parents use cooperative super(). TelemetryPolicy contributes only its
    opt-in public movement tracking and service estimate. SuccessorPolicy owns
    candidate generation, incumbent comparison, commitment guards and rollout.
    tick_service optionally rounds the two estimated travel legs to model ticks.
    This still approximates waypoint rounding and future communication delays.
    """

    def __init__(self, *args, tick_service=False, per_rescue=True, **kwargs):
        self.tick_service = bool(tick_service)
        self.per_rescue = bool(per_rescue)
        self.dispatch_counts = Counter()
        super().__init__(*args, **kwargs)

    def _reset(self, episode):
        super()._reset(episode)
        self.dispatch_counts = Counter()

    def decide(self, observation):
        before = (self.dispatch_counts.copy() if self.episode == observation['episode_id']
                  else Counter())
        result = super().decide(observation)
        delta = self.dispatch_counts - before
        if delta:
            self.traces.append({
                'kind': 'successor_dispatch', 'type': 'successor_dispatch',
                'episode_id': observation['episode_id'], 'request_id': observation['request_id'],
                'decision_id': result['decision_id'], 'time': observation['time'],
                'energy_guard': ('per_rescue_only_when_count_increases' if self.per_rescue
                                 else 'total_energy'),
                'tick_service': self.tick_service, 'counts': dict(delta),
                'basis': 'public_known_targets_and_observed_motion_resources',
            })
        return result

    def _trip(self, observation, agent, target, position, energy, ready):
        result = super()._trip(observation, agent, target, position, energy, ready)
        if result is None or not self.tick_service:
            return result
        resource = agent['resources']['energy']
        projected = dict(agent, position=list(position))
        projected['resources'] = dict(agent['resources'])
        projected['resources']['energy'] = dict(resource, value=energy + resource['minimum'])
        estimate = self._estimate(dict(observation, time=ready), projected, target)
        if estimate is None:
            self.dispatch_counts['service_projection_rejected'] += 1
            return None
        speed = self.speeds[agent['id']]
        overhead = max(4., self.service_time(agent['id']) or 4.)
        duration = math.ceil(estimate[4] / speed) + math.ceil(estimate[5] / speed) + overhead
        if ready + duration > horizon_of(observation['mission']):
            self.dispatch_counts['service_projection_rejected'] += 1
            return None
        self.dispatch_counts['tick_service_projections'] += 1
        return duration, result[1], result[2]

    def _projection(self, *args, **kwargs):
        self.dispatch_counts['plan_projections'] += 1
        result = super()._projection(*args, **kwargs)
        if result is None:
            self.dispatch_counts['infeasible_plan_projections'] += 1
        return result

    def _acceptable(self, candidate, incumbent):
        self.dispatch_counts['admission_checks'] += 1
        if not self.per_rescue:
            accepted = super()._acceptable(candidate, incumbent)
            self.dispatch_counts['admissible_strict' if accepted else 'reject_strict'] += 1
            return accepted
        if candidate is None or incumbent is None:
            self.dispatch_counts['reject_missing_projection'] += 1
            return False
        if candidate.count < incumbent.count:
            self.dispatch_counts['reject_count'] += 1
            return False
        if candidate.priority + 1e-9 < incumbent.priority:
            self.dispatch_counts['reject_priority'] += 1
            return False
        if candidate.mean_completion > incumbent.mean_completion * (1 + self.time_tolerance) + 1e-8:
            self.dispatch_counts['reject_time'] += 1
            return False
        more = candidate.count > incumbent.count
        if more:
            if (incumbent.count <= 0 or candidate.energy / candidate.count >
                    incumbent.energy / incumbent.count * (1 + self.energy_tolerance) + 1e-10):
                self.dispatch_counts['reject_energy_per_rescue'] += 1
                return False
        elif candidate.energy > incumbent.energy * (1 + self.energy_tolerance) + 1e-10:
            self.dispatch_counts['reject_equal_count_total_energy'] += 1
            return False
        margin = self.improvement_margin
        improved = (more or candidate.priority > incumbent.priority + margin or
                    candidate.energy < incumbent.energy * (1 - margin) - 1e-10 or
                    candidate.mean_completion < incumbent.mean_completion * (1 - margin) - 1e-8)
        self.dispatch_counts['admissible_more_rescues' if more and improved else
                             'admissible_same_rescues' if improved else 'reject_no_margin'] += 1
        return improved

    def _flow_wave(self, *args, **kwargs):
        first_trace = len(self.traces)
        result = super()._flow_wave(*args, **kwargs)
        if any(t.get('kind') == 'successor_rollout' for t in self.traces[first_trace:]):
            self.dispatch_counts['accepted_replans'] += 1
        return result
