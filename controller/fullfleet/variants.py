"""Small ablations on top of the frozen winner; public observations only."""
import math

from controller.counterfactual_final import CounterfactualValuePolicy
from controller.portable import horizon_of


class TelemetryPolicy(CounterfactualValuePolicy):
    def __init__(self, *args, track_speed=False, service_estimate=False,
                 energy_price=None, **kwargs):
        self.track_speed = track_speed
        self.service_estimate = service_estimate
        self.dispatch_energy_price = energy_price
        super().__init__(*args, **kwargs)
        if energy_price is not None:
            self.route_mode = f'eco-1.8-{float(energy_price)}'

    def _reset(self, episode):
        super()._reset(episode)
        self.successor_slow_samples = {}

    def decide(self, observation):
        if (self.track_speed and self.episode == observation['episode_id'] and
                self._profile() and self.capabilities.get('environment_id') == 'metarescue_planar_reference'):
            now = observation['time']
            for agent in observation['agents']:
                aid = agent['id']
                previous = self.last_motion.get(aid)
                pending = self.pending.get(aid)
                prior_speed = self.speeds.get(aid)
                move = self.accepted_moves.get(pending[:2]) if pending else None
                if not (agent['kind'] == 'ground' and previous and pending and move and prior_speed and
                        agent.get('position') is not None and agent['status'] == 'busy' and
                        agent['communication'].get('reachable', False) and 0 < now - previous[0] <= 2):
                    self.successor_slow_samples.pop(aid, None)
                    continue
                speed = math.dist(agent['position'], previous[1]) / (now - previous[0])
                if .05 < speed < .85 * prior_speed and math.dist(agent['position'], move[1]) > prior_speed:
                    old = self.successor_slow_samples.get(aid)
                    self.successor_slow_samples[aid] = (speed, now)
                    if old and now - old[1] <= 2 and abs(speed - old[0]) <= .1 * max(speed, old[0]):
                        self.speeds[aid] = max(speed, old[0])
                        self.traces.append({'kind': 'successor_speed', 'agent_id': aid, 'time': now,
                                            'old': prior_speed, 'new': self.speeds[aid],
                                            'basis': 'two_public_interior_movement_samples'})
                        self.successor_slow_samples.pop(aid, None)
                else:
                    self.successor_slow_samples.pop(aid, None)
        return super().decide(observation)

    def _estimate(self, observation, agent, target):
        result = super()._estimate(observation, agent, target)
        if result is None or not self.service_estimate:
            return result
        speed = self.speeds.get(agent['id'])
        horizon = horizon_of(observation['mission'])
        if not speed or horizon is None:
            return result
        action, params, _, energy, outward, returning = result
        service = self.service_time(agent['id'])
        duration = math.ceil(outward / speed) + math.ceil(returning / speed) + max(4., service or 4.)
        if duration > horizon - observation['time']:
            return None
        return action, params, duration, energy, outward, returning
