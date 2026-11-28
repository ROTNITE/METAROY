"""CPU-only successor: memoize pure geometry during one public decision.

The frozen FullFleetPolicy remains unchanged. This class uses its exact six
finalist settings, forecasts, candidate order, counters and admission rules.
Repeated _approach and _back queries reuse results. Within one _trip, the second
identical _estimate call may reuse the first call's full validated result. No
timing threshold, partial rollout, rounded coordinate key, new objective or new
action is added.

Geometry contents are discarded after every decide, including exceptions.
The estimate memo never survives a single _trip or crosses a forecast state.
This is an optimization to be measured, not a new performance certificate.
"""
from __future__ import annotations

from collections import Counter
from struct import pack

from controller.fullfleet.full_fleet import FullFleetPolicy


_MISSING = object()
_CACHE_LIMIT = 4096


def _point_key(point):
    """Keep numeric types and signed zero distinct, without any quantization.

    The packed doubles retain every float bit, including signed zero. Integer
    values also remain in the key to avoid loss above double integer precision.
    Type tags keep the parent's original endpoint representation distinct.
    """
    coordinates = tuple(point)
    if len(coordinates) != 3:
        return tuple((type(value), value.hex() if isinstance(value, float) else value)
                     for value in coordinates)
    x, y, z = coordinates
    return (type(x), type(y), type(z), pack('!ddd', x, y, z),
            x if type(x) is not float else None,
            y if type(y) is not float else None,
            z if type(z) is not float else None)


class FullFleetFastPolicy(FullFleetPolicy):
    """Identical policy decisions with a bounded per-decision geometry cache."""

    def __init__(self, *args, **kwargs):
        self._fast_geometry = None
        self._fast_trip_estimate = None
        self.fast_counts = Counter()
        super().__init__(*args, **kwargs)

    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self.fast_counts = Counter()
        # Keep strong references to every infrastructure list used in the
        # scope. Python object identity therefore cannot be recycled mid-call.
        previous = self._fast_geometry
        self._fast_geometry = {'approach': {}, 'back': {}, 'infrastructure': {}}
        self.fast_counts['decision_scopes'] += 1
        try:
            return super().decide(observation)
        finally:
            self._fast_geometry = previous

    def _trip(self, observation, agent, target, position, energy, ready):
        # EfficiencyDispatchPolicy._trip invokes SuccessorPolicy._trip, which
        # calls _estimate. If successful, tick_service calls _estimate again on
        # a freshly constructed but identical frame and projected agent. That
        # interval has no telemetry update, response, cooldown edit or command.
        previous = self._fast_trip_estimate
        self._fast_trip_estimate = []
        try:
            return super()._trip(observation, agent, target, position, energy, ready)
        finally:
            self._fast_trip_estimate = previous

    def _estimate(self, observation, agent, target):
        memo = self._fast_trip_estimate
        if memo is not None and memo:
            prior_observation, prior_agent, prior_target, result = memo[0]
            # Equality covers time, projected energy, position, all available
            # actions and the whole public frame, not just actor/target IDs.
            # Inputs are read-only throughout the inherited _trip. Keeping the
            # previous frames alive avoids object-id reuse; no result is made
            # available to another trip or a later command construction.
            if (target is prior_target and observation is not prior_observation and
                    agent is not prior_agent and observation == prior_observation and
                    agent == prior_agent):
                self.fast_counts['within_trip_estimate_hits'] += 1
                # Only the one redundant call consumes this result. A later
                # call would start a fresh validation even inside this trip.
                memo.clear()
                return result
        result = super()._estimate(observation, agent, target)
        if memo is not None:
            memo[:] = [(observation, agent, target, result)]
            self.fast_counts['within_trip_estimate_misses'] += 1
        return result

    def _approach(self, start, goal):
        cache = self._fast_geometry
        if cache is None:
            return super()._approach(start, goal)
        key = (self.router, self.route_mode, _point_key(start), _point_key(goal))
        result = cache['approach'].get(key, _MISSING)
        if result is not _MISSING:
            self.fast_counts['approach_hits'] += 1
            return result
        result = super()._approach(start, goal)
        self.fast_counts['approach_misses'] += 1
        if len(cache['approach']) < _CACHE_LIMIT:
            cache['approach'][key] = result
        else:
            self.fast_counts['approach_storage_cap'] += 1
        return result

    def _back(self, observation, goal):
        cache = self._fast_geometry
        if cache is None:
            return super()._back(observation, goal)
        infrastructure = observation['infrastructure']
        token = id(infrastructure)
        cache['infrastructure'][token] = infrastructure
        # Include the exact station set, identities and positions. Its order is
        # preserved so the parent's stable min tie-break is preserved as well.
        # Dictionaries are returned by reference, as in the parent _back.
        stations = tuple((id(station), station['id'], _point_key(station['position']))
                         for station in infrastructure
                         if station['kind'] == 'base_station' and
                         station['status'] == 'active' and station.get('position') is not None)
        key = (self.router, self.route_mode, token, stations, _point_key(goal))
        result = cache['back'].get(key, _MISSING)
        if result is not _MISSING:
            self.fast_counts['back_hits'] += 1
            return result
        result = super()._back(observation, goal)
        self.fast_counts['back_misses'] += 1
        if len(cache['back']) < _CACHE_LIMIT:
            cache['back'][key] = result
        else:
            self.fast_counts['back_storage_cap'] += 1
        return result
