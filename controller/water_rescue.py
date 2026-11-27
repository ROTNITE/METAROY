"""Native surface-water dispatch based on announced and delivered data."""
from __future__ import annotations

import heapq
import math

from contracts.urban_terrain import Terrain
from controller.navigation import Box, NavigationMap, RouteFailure


class WaterRoute:
    def __init__(self, announced, observation, obstacles, draft):
        self.terrain = Terrain(announced.get('terrain'))
        geometry = {key: value for key, value in announced.items() if key != 'road_graph'}
        self.revised = observation.get('metaevolution', {}).get('disaster_revision') == 1
        self.now = observation['time']
        self.edges = {}
        restrictions = []
        for zone in observation.get('zones', ()):
            access = zone.get('access', {}).get('allowed_kinds')
            shape = zone.get('geometry', {})
            restriction = (zone.get('type') == 'restricted' and isinstance(access, list)
                           and 'surface_water' not in access)
            surface = zone.get('surface', {})
            debris = (self.revised and isinstance(surface, dict) and surface.get('active') is True
                      and surface.get('physical_debris') is True)
            if (restriction or debris) and shape.get('type') == 'box':
                received = zone.get('received_at')
                if self.revised and (type(received) not in (int, float) or not 0 <= received <= self.now):
                    continue
                try:
                    box = Box.parse({'kind': 'box', 'min': shape['min'], 'max': shape['max']})
                except (ValueError, KeyError, TypeError):
                    continue
                restrictions.append({'kind': 'box', 'min': list(box.minimum), 'max': list(box.maximum)})
        self.nav = NavigationMap(geometry, observed_obstacles=[*obstacles, *restrictions])
        self.basin = announced['hydrology']['basin']
        self.stage = announced['hydrology']['initial_stage_m']
        self.depth = draft + .12
        self.readings = {}
        now = observation['time']
        for zone in observation.get('zones', ()):
            surface, geometry = zone.get('surface'), zone.get('geometry', {})
            seen, received = zone.get('observed_at'), zone.get('received_at')
            if (not isinstance(surface, dict) or geometry.get('type') != 'box' or
                    type(seen) not in (int, float) or type(received) not in (int, float) or
                    not 0 <= seen <= received <= now or (not self.revised and now - seen > 20)):
                continue
            depth = surface.get('depth_m')
            if type(depth) not in (int, float) or not math.isfinite(depth) or depth < 0:
                continue
            try:
                box = Box.parse({'kind': 'box', 'min': geometry['min'], 'max': geometry['max']})
            except (ValueError, KeyError, TypeError):
                continue
            if self.revised and (any(abs(box.maximum[i]-box.minimum[i]-5.) > 1.e-6 for i in (0, 1))
                                 or any(abs(v/5-round(v/5)) > 1.e-6 for v in box.minimum[:2])):
                continue
            row = self.cell(box.minimum)
            previous = self.readings.get(row)
            # Equal-time conflicting depths cannot turn an unsafe cell deep.
            if previous is None or seen > previous[0] or (self.revised and seen == previous[0] and depth < previous[1]):
                self.readings[row] = (seen, depth)

    @staticmethod
    def cell(position):
        return tuple(math.floor(coordinate / 5) for coordinate in position[:2])

    def deep(self, x, y):
        reading = self.readings.get(self.cell((x, y)))
        if reading is not None:
            return (not self.revised or self.now - reading[0] <= 20) and reading[1] >= self.depth
        return (all(low + .6 <= value <= high - .6 for low, value, high in zip(self.basin['min'], (x, y), self.basin['max']))
                and self.stage - self.terrain.height(x, y) >= self.depth)

    def _measured_segment(self, start, goal):
        # Split at every crossed 5 m grid boundary. Fixed-distance samples
        # can skip a shallow sliver at the corner of a diagonal cell.
        cuts = {0., 1.}
        for a, b in zip(start, goal):
            if a == b:
                continue
            low, high = sorted((a, b))
            for cell in range(math.floor(low/5)+1, math.ceil(high/5)):
                cuts.add((cell*5-a)/(b-a))
        cuts = sorted(cuts)
        checks = [0., 1., *((a+b)*.5 for a,b in zip(cuts,cuts[1:]))]
        return all(self.deep(*(a+(b-a)*t for a,b in zip(start,goal))) for t in checks)

    def clear(self, start, goal):
        if self.revised:
            key = tuple(sorted((tuple(start), tuple(goal))))
            if key not in self.edges:
                # Cap the cache even when a caller probes many alternatives.
                if len(self.edges) >= 50000:
                    self.edges.clear()
                self.edges[key] = self._clear_revised(start, goal)
            return self.edges[key]
        if not self.nav.clear(start, goal, 1.2, .5):
            return False
        distance = math.dist(start[:2], goal[:2])
        steps = max(1, math.ceil(distance / 1.5))
        for step in range(1, steps + 1):
            x, y = tuple(a + (b - a)*step/steps for a, b in zip(start[:2], goal[:2]))
            if not self.deep(x, y):
                return False
        return True

    def _clear_revised(self, start, goal):
        if not self.nav.clear(start, goal, 1.2, .8):
            return False
        dx,dy = (b-a for a,b in zip(start[:2],goal[:2]))
        length = math.hypot(dx,dy)
        # Sample the width of the planned swept corridor, not just its axis.
        # Native oriented hull and local depth/braking checks remain decisive.
        offset = (-dy/length*1.1,dx/length*1.1) if length else (1.1,0.)
        return all(self._measured_segment(tuple(a+factor*d for a,d in zip(start[:2],offset)),
                                          tuple(b+factor*d for b,d in zip(goal[:2],offset)))
                   for factor in (-1,0,1))

    def route(self, start, goal):
        if self.revised and not self.deep(*goal[:2]):
            return None
        if self.clear(start, goal):
            return [list(goal)]
        source, target = self.cell(start), self.cell(goal)
        z = start[2]
        def center(cell):
            return (cell[0]*5 + 2.5, cell[1]*5 + 2.5, z)

        cost = {source: 0.}
        parent = {}
        queue = [(0., 0., source)]
        expanded = 0
        # A search on a fresh public snapshot cannot expand arbitrarily into
        # the city: only measured wet cells or the small declared basin enter.
        limit = min(12000, max(640, 2*len(self.readings)+128)) if self.revised else 640
        while queue and expanded < limit:
            _, travel, cell = heapq.heappop(queue)
            if travel != cost.get(cell):
                continue
            expanded += 1
            here = start if cell == source else center(cell)
            if max(abs(a - b) for a, b in zip(cell, target)) <= 1 and self.clear(here, goal):
                route = [list(goal)]
                while cell != source:
                    route.append(list(center(cell)))
                    cell = parent[cell]
                return route[::-1]
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                peer = (cell[0] + dx, cell[1] + dy)
                there = center(peer)
                distance = travel + math.dist(here, there)
                if distance >= cost.get(peer, math.inf) or not self.clear(here, there):
                    continue
                cost[peer] = distance
                parent[peer] = cell
                heapq.heappush(queue, (distance + math.dist(there, goal), distance, peer))
        return None


def ground_target(target):
    return target.get('access_medium') != 'surface_water'


def shore_target(target, announced):
    shore = announced.get('watercraft', {}).get('shore_station', {})
    return bool(shore and target.get('support') == 'shore_platform' and target.get('transfer_station_id') == shore['id'])


def reserve_shore(workflow, observation, announced, known, directives):
    shore = announced.get('watercraft', {}).get('shore_station')
    if not shore:
        return None
    agents = {agent['id']: agent for agent in observation['agents']}
    previous = workflow.shore_transfer
    if previous:
        boat = agents.get(previous['boat_id'])
        rover = agents.get(previous['rover_id'])
        target = known.get(previous['target_id'], {})
        if (boat is not None and rover is not None and target.get('evacuation_state') != 'evacuated' and rover['status'] != 'failed'
                and rover['id'] not in directives and (boat['status'] != 'failed' or shore_target(target, announced))):
            return previous

    now = observation['time']
    pier = shore['ground_pickup']
    dock = shore['boat_position_xy']
    options = []
    for boat in agents.values():
        physical = boat.get('physical', {})
        target_id = physical.get('cargo_target_id')
        hull = physical.get('watercraft')
        if not hull or not target_id or not boat.get('position') or boat['status'] != 'busy':
            continue
        observer = boat.get('freshness', {}).get('position', {}).get('observed_at')
        if type(observer) not in (float,int) or now-observer>5:
            continue
        travel = math.dist(boat['position'][:2],dock)
        current = math.hypot(*hull.get('current_m_s',(0,0,0))[:2])
        energy_rate = max(.04, physical.get('energy_breakdown_wh',{}).get('movement',0.)/max(1.,physical.get('distance_m',0.)))
        energy = travel*energy_rate+2.
        if not physical.get('unlimited_energy',False) and energy>physical.get('remaining_wh',0.)-physical.get('reserve_wh',0.):
            continue
        arrival = travel/max(.6,2.2-current)
        options.append((arrival, -known.get(target_id,{}).get('priority',0), boat['id'],target_id,energy))
    if not options:
        return None
    boat_eta, _, boat_id, target_id, boat_energy = min(options)
    team = [a for a in agents.values() if a['kind']=='ground' and a.get('position') and a['status'] in ('active','degraded')
            and a.get('communication',{}).get('reachable') is not False and a['id'] not in directives
            and a['id'] not in workflow.scenario_reserved and a['id'] not in workflow.blocked_agents
            and workflow.jobs[a['id']].phase=='choose' and not workflow.jobs[a['id']].pending]
    base = announced['base']
    return_distance = math.dist(pier[:2],[(lo+hi)*.5 for lo,hi in zip(base['min'][:2],base['max'][:2])])
    for rover in sorted(team,key=lambda a:(math.dist(a['position'][:2],pier[:2]),a['id']))[:4]:
        physical = rover['physical']
        exits = physical.get('dock_exit_waypoints',[]) if physical.get('at_dock') else []
        start = {**rover,'position':exits[-1]} if exits else rover
        try:
            path = workflow._planned_route(announced,start,observation['agents'],pier,precise_stop=True)
        except RouteFailure:
            continue
        exit_distance = sum(math.dist(a,b) for a,b in zip([rover['position'],*exits],exits))
        drive_eta = path.distance_m/2.+exit_distance
        wait = max(0.,boat_eta+12.-drive_eta)
        energy = (path.distance_m+exit_distance)*.082 + return_distance*.118 + wait*.005 + 2.
        if not physical.get('unlimited_energy',False) and energy>physical.get('remaining_wh',0.)-physical.get('reserve_wh',0.):
            continue
        return {'target_id':target_id,'boat_id':boat_id,'rover_id':rover['id'],'computed_at':now,
                'boat_to_dock_s':boat_eta,'rover_to_pier_s':drive_eta,'rover_wait_s':wait,
                'handover_eta_s':max(boat_eta+12.,drive_eta)+16.+return_distance/1.6,
                'boat_estimated_wh':boat_energy,'rover_estimated_wh':energy}
    return None


def water_route_changed(job, known, now):
    if job.water_intercept is None:
        return False
    old_observed, issued, old_position = job.water_intercept
    target = known.get(job.target, {})
    observed = target.get('observed_at')
    return (target.get('access_medium') == 'surface_water'
            and target.get('evacuation_state') == 'waiting' and target.get('position')
            and type(observed) in (int, float) and old_observed < observed <= now
            and now - observed <= 6 and now - issued >= 3
            and math.dist(old_position, target['position'][:2]) >= 1.5)


def water_pickup_ready(boat, target):
    physical = boat['physical']
    velocity = physical.get('velocity_m_s', (0, 0, 0))[:2]
    drift = target.get('velocity_m_s', (0, 0, 0))[:2]
    roll, pitch, yaw = physical.get('rotation_degrees', (0, 0, 0))
    bearing = math.radians(yaw)
    dx, dy = (t-a for t,a in zip(target['position'][:2],boat['position'][:2]))
    lateral = abs(-math.sin(bearing)*dx+math.cos(bearing)*dy)
    # A supported casualty can be lifted from a settled approach by the
    # other vessel. Leave 5 cm of margin to the native 1.45 m hoist limit.
    supported = target.get('supported_by_agent') and target.get('evacuation_state') == 'waiting'
    lateral_limit = 1.40 if supported else 1.28
    return (target.get('status') == 'confirmed'
            and math.dist(boat['position'][:2], target['position'][:2]) < 4.32
            and math.hypot(*velocity) <= .62 and math.dist(velocity, drift) < .27
            and lateral <= lateral_limit and max(abs(roll), abs(pitch)) <= 7
            and abs(physical['watercraft'].get('yaw_rate_degrees_s', 0)) <= 7.5)


def water_intercept_opportunity(boat, job, known, now):
    physical = boat.get('physical', {})
    if physical.get('watercraft', {}).get('moored', True) or physical.get('carrying'):
        return False
    for tid, target in known.items():
        if job.target and job.target != tid:
            continue
        observed = target.get('observed_at')
        if (target.get('access_medium') == 'surface_water' and target.get('evacuation_state') == 'waiting'
                and target.get('position') and type(observed) in (int, float) and 0 <= now - observed <= 3
                and (water_pickup_ready(boat, target)
                     or job.water_intercept is None and math.dist(boat['position'], target['position']) <= 11.)):
            return True
    return water_route_changed(job, known, now)


def failed_carriers(agents, known, reserved):
    failures = {a['id']:a for a in agents if a['kind']=='surface_water'
                and a['status']=='failed' and a.get('position')}
    return [(tid,failures[t['supported_by_agent']]['position']) for tid,t in known.items()
            if t.get('evacuation_state') in ('loaded','waiting') and t.get('supported_by_agent') in failures
            and tid not in reserved]


def water_command(workflow, observation, announced, boat, job, known, *, reroute=False):
    profile = announced.get('watercraft', {})
    shore = profile.get('shore_station')
    physical = boat.get('physical', {})
    hull = physical.get('watercraft')
    if not shore or not hull or not announced.get('hydrology') or not boat.get('position'):
        return None, None

    boat_id = boat['id']
    pose = tuple(boat['position'])
    agents = observation['agents']
    actions = {action.get('action') for action in boat['available_actions']}
    nav = None

    def request(action, parameters, phase):
        if action not in actions:
            return None, None
        job.water_intercept = None
        job.water_approach = None
        return {'agent_id': boat_id, 'action': action, 'parameters': parameters}, phase

    def marine_navigation(peers):
        draft = hull.get('hydrostatic_draft_m', hull.get('draft_m', .5))
        obstacles = workflow._obstacles(boat, peers)
        obstacles.extend(workflow.local_obstacles.get(boat_id, ()))
        return WaterRoute(announced, observation, obstacles, draft)

    def route(goal):
        nonlocal nav
        if nav is None:
            nav = marine_navigation(agents)
        return nav.route(pose, (*goal[:2], pose[2]))

    def leave_failed_hull(carrier):
        if not carrier.get('position') or 'follow_route' not in actions:
            return None
        offset = tuple(value - peer for value, peer in zip(pose[:2], carrier['position']))
        gap = math.hypot(*offset)
        padding = carrier.get('physical', {}).get('radius_m', 2.55) + 1.2
        if gap <= .15 or max(map(abs, offset)) >= padding:
            return None
        away = tuple(value + delta * 5. / gap for value, delta in zip(pose[:2], offset))
        departure = marine_navigation([peer for peer in agents if peer is not carrier])
        if departure.clear(pose, (*away, pose[2])):
            return {'waypoints': [[*away, pose[2]]]}
        return None

    if physical.get('carrying'):
        job.target = physical.get('cargo_target_id') or job.target
        # The pickup itself can leave the boat inside a disabled peer's
        # clearance margin. Leave directly before planning the shore route.
        for carrier in agents:
            if carrier['kind'] == 'surface_water' and carrier['status'] == 'failed':
                departure = leave_failed_hull(carrier)
                if departure:
                    return request('follow_route', departure, 'shore')
        reservation = workflow.shore_transfer
        if reservation and reservation['boat_id'] != boat_id:
            return None, None
        dock = shore['boat_position_xy']
        yaw = math.radians(shore['boat_yaw_degrees'])
        angle = abs(math.remainder(physical['rotation_degrees'][2] - shore['boat_yaw_degrees'], 360.))
        if math.dist(pose[:2], dock) <= .77 and angle <= 14.5:
            if math.hypot(*physical['velocity_m_s'][:2]) > .24:
                return None, None
            if any(shore_target(t, announced) and t['evacuation_state'] == 'waiting' for t in known.values()):
                return None, None
            return request('disembark', {'station_id': shore['id']}, 'search_water')
        approach = (dock[0] - math.cos(yaw)*8, dock[1] - math.sin(yaw)*8)
        waypoints = route(approach)
        if waypoints and nav.clear((*approach, pose[2]), (*dock, pose[2])):
            waypoints.append([*dock, pose[2]])
            return request('follow_route', {'waypoints': waypoints, 'precise_stop': True}, 'shore')
        return None, None

    reserved = {other.target for key, other in workflow.jobs.items() if key != boat_id and other.target}
    waiting = {tid: t for tid, t in known.items() if t.get('access_medium') == 'surface_water'
               and t.get('evacuation_state') == 'waiting' and t.get('position') and tid not in reserved}
    target = waiting.get(job.target)
    if target is None:
        job.target = None
        fleet = [agent for agent in agents if agent['kind'] == 'surface_water' and agent.get('position')
                 and agent.get('physical', {}).get('watercraft')
                 and agent['status'] not in ('failed', 'offline', 'unknown', 'busy')
                 and agent.get('communication', {}).get('reachable') is not False
                 and not workflow.jobs[agent['id']].target]
        if fleet:
            def suitable(t):
                nearest = min(fleet, key=lambda a: (
                    math.dist(a['position'][:2], t['position'][:2])
                    + (12 if a['physical']['watercraft'].get('moored') else 0), a['id']))
                return nearest['id'] == boat_id
            target = min((t for t in waiting.values() if suitable(t)),
                         key=lambda t: (math.dist(t['position'], pose), -t.get('priority', 0), t['id']), default=None)
    if hull.get('moored'):
        first = next((a for a in agents if a['kind'] == 'surface_water' and a.get('physical', {}).get('watercraft')
                      and a['status'] not in ('failed', 'offline', 'unknown')
                      and a.get('communication', {}).get('reachable') is not False), None)
        if target is None and first is not boat:
            return None, None
        spawn = next((spawn for spawn in profile['spawns'] if spawn['id'] == boat_id), None)
        if spawn is None:
            return None, None
        slip = next(s for s in profile['slipways'] if s['id'] == spawn['slipway_id'])
        basin = announced['hydrology']['basin']
        sweep = (basin['min'][1] + basin['max'][1])/2 + 2.
        job.target = target['id'] if target else None
        return request('follow_route', {'waypoints': [[slip['x_m'], slip['lower_y_m'], pose[2]],
                                                       [slip['x_m'], sweep, pose[2]]]}, 'search_water')
    if target is None:
        for tid, position in failed_carriers(agents,known,reserved):
            x,y = position[:2]
            if math.dist(pose,position)<=11. and 'inspect' in actions:
                job.target=tid
                return request('inspect',{'target_id':tid},'search_water')
            radius = 8.
            bearing = math.atan2(pose[1]-y,pose[0]-x)
            for angle in (bearing,bearing+math.pi/3,bearing-math.pi/3,bearing+math.pi):
                vantage = (x+radius*math.cos(angle),y+radius*math.sin(angle))
                if math.dist(pose[:2],vantage)<2.:
                    continue
                waypoints = route(vantage)
                if waypoints:
                    job.target = tid
                    return request('follow_route',{'waypoints':waypoints},'search_water')
        return None, None

    now = observation['time']
    observed = target.get('observed_at')
    age = now - observed if type(observed) in (int, float) else math.inf
    position = tuple(target['position'][:2])
    current = pose[:2]
    ready = water_pickup_ready(boat, target)
    needs_confirmation = target.get('status') != 'confirmed' or target['id'] in workflow.reconfirm_after
    stale_support = target.get('supported_by_agent') and not 0 <= age <= 3
    stale_water = observation.get('metaevolution', {}).get('disaster_revision') == 1 and not 0 <= age <= 6
    if ready and not (needs_confirmation or stale_support or stale_water):
        job.target = target['id']
        return request('pickup', {'target_id': job.target}, 'shore')
    if 'inspect' in actions and ((needs_confirmation and math.dist(pose, target['position']) < 11.5)
                                 or (ready and stale_support) or (stale_water and math.dist(pose, target['position']) < 11.5)):
        # Inspection can refresh the last delivered pose after carrier loss
        # even if passive reporting has seen too little movement to transmit.
        failed = workflow.failed_inspections.get((boat_id, target['id']), ())
        if not any(math.dist(last, position) < 1. and math.dist(vantage, current) < 2. for last, vantage in failed):
            job.target = target['id']
            job.inspection = (job.target, position, current)
            return request('inspect', {'target_id': job.target}, 'search_water')
    velocity = target.get('velocity_m_s', (0, 0, 0))[:2]
    job.blocked_water_approaches = {key: expires for key, expires in job.blocked_water_approaches.items()
                                   if expires > now}
    reach = 3.5
    if target.get('supported_by_agent'):
        carrier = next((item for item in agents if item['id'] == target['supported_by_agent']), None)
        if carrier and carrier['kind'] == 'surface_water' and carrier['status'] == 'failed':
            # Another hull requires more clearance. Keep the staging point
            # within the native hoist's 4.4 m envelope.
            reach = 4.12
            if carrier.get('position'):
                departure = leave_failed_hull(carrier)
                if departure:
                    job.target = target['id']
                    return request('follow_route', departure, 'search_water')
                if age > 6 and math.dist(carrier['position'][:2], position) > 2.5:
                    position = tuple(carrier['position'][:2])
                    if math.dist(current, position) < 11.:
                        job.target = target['id']
                        return request('inspect', {'target_id': job.target}, 'search_water')
    time_to_reach = max(0., math.dist(current, position) - reach) / 2.
    latency = boat.get('communication', {}).get('latency') or 0.
    lead = min(5., age + latency + time_to_reach + 1.5) if 0 <= age <= 6 else 0.
    intercept = tuple(value + speed * lead for value, speed in zip(position, velocity))
    for x, y in dict.fromkeys((intercept, position)):
        distance = math.dist(current, (x, y))
        offsets = [(0, reach), (0, -reach), (reach, 0), (-reach, 0)]
        if distance > .01:
            safe_range = min(reach, max(2.2, distance - 1.))
            offsets.append(((current[0]-x)*safe_range/distance, (current[1]-y)*safe_range/distance))
        candidates = [(x+dx, y+dy) for dx,dy in offsets]
        for candidate in sorted(candidates, key=lambda point: (math.dist(current, point), point)):
            if math.dist(current, candidate) < .6 or 'follow_route' not in actions:
                continue
            dx, dy = x-candidate[0], y-candidate[1]
            direction = round(math.atan2(dy, dx) * 4 / math.pi) % 8
            approach_key = (target['id'], direction)
            if approach_key in job.blocked_water_approaches:
                continue
            length = math.hypot(dx, dy)
            approach = (candidate[0]-dx*3.2/length,candidate[1]-dy*3.2/length)
            waypoints = route(approach)
            if waypoints and nav.clear((*approach, pose[2]), (*candidate, pose[2])):
                waypoints.append([*candidate,pose[2]])
                job.target = target['id']
                command = request('follow_route', {'waypoints': waypoints, 'precise_stop': True}, 'search_water')
                job.water_intercept = (observed if math.isfinite(age) else now, now, position)
                job.water_approach = approach_key
                return command
    if not reroute:
        job.target = None
    return None, None
