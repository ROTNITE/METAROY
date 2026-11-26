"""Adapt publicly announced RescueQuarter geometry to the Net65 estimator."""
from functools import lru_cache
import math
import time

from controller.navigation import RouteFailure, point
from controller.net65 import Candidate, MAX_CANDIDATES, choose_sector, horizon_of
from controller.problem_builder import mobiles, usable_energy


def solve(builder, observation, excluded, scanned_at, eligible):
    began = time.perf_counter()
    now, horizon = observation['time'], horizon_of(observation['mission'])
    scan_s, side = 2.8, 50.
    power_wh_s, motion_wh_m = (130. + 14.) / 3600., .069
    agents = []
    for agent in mobiles(observation, 'aerial'):
        physical = agent.get('physical', {})
        rates = (physical.get('speed_limit_m_s'), physical.get('climb_rate_m_s'),
                 physical.get('descent_rate_m_s'))
        stamp = agent.get('freshness', {}).get('position', {}).get('observed_at')
        if (agent['id'] not in builder.altitudes or type(stamp) not in (int, float) or now - stamp > 5
                or 'sense' not in agent['capabilities']
                or any(type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0 for rate in rates)):
            continue
        try:
            home = point(physical['dock_position_m'])
        except (KeyError, ValueError):
            continue
        agents.append((agent, rates, home))
    if not agents or not any(a['id'] in eligible for a, _, _ in agents):
        return None

    cycle = len(builder.positions) / sum(1. / (scan_s + side / rates[0]) for _, rates, _ in agents)
    support = set()
    for rover in mobiles(observation, 'ground'):
        stamp = rover.get('freshness', {}).get('position', {}).get('observed_at')
        waypoint = rover.get('physical', {}).get('active_waypoint')
        if type(stamp) in (int, float) and now - stamp <= 12 and waypoint and rover['status'] == 'busy':
            nearby = min(builder.positions, key=lambda sid: math.dist(builder.positions[sid], waypoint[:2]))
            if math.dist(builder.positions[nearby], waypoint[:2]) <= side:
                support.add(nearby)
    routes = {}
    claimed = set(excluded)
    starts = {}
    for agent, (speed, climb, descent), home in agents:
        aid = agent['id']
        if aid not in eligible:
            continue
        start = tuple(agent['position'])
        altitude = builder.altitudes[aid]
        usable = usable_energy(agent)
        radius = agent.get('physical', {}).get('radius_m', 1.65)

        @lru_cache(maxsize=512)
        def estimate(origin, goal):
            try:
                route = builder.nav.route(origin, goal, aerial=True, radius_m=radius, half_height_m=.4,
                                          cruise_altitude_m=altitude, max_expanded=128)
            except RouteFailure:
                return None
            seconds = sum(max(math.dist(a[:2], b[:2]) / speed,
                              abs(b[2] - a[2]) / (climb if b[2] >= a[2] else descent))
                          for a, b in zip(route.points, route.points[1:]))
            return seconds, route.distance_m * motion_wh_m + seconds * power_wh_s

        def return_energy(goal):
            if usable is None:
                return 0.
            trip = estimate(goal, home)
            return trip[1] if trip else math.inf

        candidates = []
        for sid, stamp in scanned_at.items():
            age = now - stamp
            if (sid in claimed or sid not in builder.positions
                    or age < max(6., .35 * cycle) and not (sid in support and age >= max(12., .5 * cycle))):
                continue
            goal = (*builder.positions[sid], altitude)
            weight = 1.5 if sid in support else 1.
            flight = math.dist(start, goal) / speed
            other_eta = min((math.dist(peer['position'], goal) / rates[0] for peer, rates, _ in agents
                             if peer['id'] != aid and peer['id'] in eligible), default=math.inf)
            ownership = 1. / (1. + (flight - other_eta) / max(1., side / speed)) if other_eta + 2 < flight else 1.
            optimistic = age * max(0., horizon - now - flight - scan_s) / (cycle * cycle) * weight * ownership - flight * speed / side
            candidates.append((optimistic, sid, Candidate(sid, goal, stamp, weight, ownership)))
        shortlist = [candidate for _, _, candidate in sorted(candidates,
                     key=lambda item: (-item[0], item[1]))[:MAX_CANDIDATES]]
        sector = choose_sector(shortlist, start=start, now=now, horizon=horizon, cycle=cycle, scan_s=scan_s,
                               scan_energy=scan_s * power_wh_s, energy_scale=side * (motion_wh_m + power_wh_s / speed),
                               estimate=estimate, available_energy=math.inf if usable is None else usable,
                               return_energy=return_energy)
        routes[aid] = [sector] if sector is not None else []
        starts[aid] = start
        if sector is not None:
            claimed.add(sector)
    return {'plan': {'routes': routes, 'stats': {'algorithm': 'net65', 'candidate_limit': MAX_CANDIDATES}},
            'patrol': True, 'positions': {}, 'altitudes': dict(builder.altitudes), 'net65_starts': starts,
            'scout_mode': 'net65', 'build_and_solve_wall_s': time.perf_counter() - began}
