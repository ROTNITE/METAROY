"""Bounded opportunistic reflight for an announced water route that stalled.

Uses only delivered agent/target/surface observations and public geometry.
It never invents a traversable water cell or directs the boat into unknown
water. The regular coverage planner and energy return reserve retain priority.
"""
from __future__ import annotations

import math

from controller.navigation import NavigationMap, RouteFailure, point
from controller.problem_builder import usable_energy
from controller.water_rescue import WaterRoute


def candidate_cells(route, origin, goal, bounds, recent, now):
    dx,dy=(b-a for a,b in zip(origin,goal))
    length=math.hypot(dx,dy)
    side=(-dy/length,dx/length) if length>.01 else (0.,1.)
    count=max(1,min(64,math.ceil(length/15.)))
    cells={}
    for i in range(count+1):
        for offset in (0.,-10.,10.):
            xy=tuple(a+(b-a)*i/count+offset*s for a,b,s in zip(origin,goal,side))
            cell=route.cell(xy)
            if recent.get(cell,-1000.)+25>now:continue
            center=tuple(v*5+2.5 for v in cell)
            if any(not bounds['min'][j]+2<=center[j]<=bounds['max'][j]-2 for j in (0,1)):continue
            # A recently measured shallow cell is evidence, not an unknown
            # to override. Try adjacent unmeasured/stale cells instead.
            measured=route.readings.get(cell)
            if measured is not None and now-measured[0]<=15:continue
            if measured is None and route.deep(*center):continue
            cells[cell]=center
    return cells


def water_survey_command(workflow,observation,announced,agent,job,known):
    if (observation.get('metaevolution',{}).get('disaster_revision')!=1 or
            not announced.get('hydrology') or not workflow.water_survey_requests):
        return None
    now=observation['time']
    if now<workflow.water_survey_next or agent.get('status') not in ('active','degraded') or not agent.get('position'):
        return None
    if 'sense' not in agent.get('capabilities',()) or not workflow.available(agent,'follow_route'):
        return None
    freshness=agent.get('freshness',{}).get('position',{}).get('observed_at')
    if type(freshness) not in (int,float) or not 0<=now-freshness<=5:return None
    physical=agent.get('physical',{})
    rates=tuple(physical.get(k,0) for k in ('speed_limit_m_s','climb_rate_m_s','descent_rate_m_s'))
    if not all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in rates):return None
    try:home=point(physical['dock_position_m'])
    except (ValueError,TypeError,KeyError):return None
    energy=usable_energy(agent)
    if energy is not None and energy<=.5:return None
    boats={a['id']:a for a in observation['agents'] if a['kind']=='surface_water' and a['status'] not in ('failed','offline','unknown')}
    requests={aid:item for aid,item in workflow.water_survey_requests.items()
              if aid in boats and 0<=now-item[0]<=25 and known.get(item[3],{}).get('evacuation_state')!='evacuated'}
    workflow.water_survey_requests=requests
    if not requests:return None
    workflow.water_survey_recent={cell:t for cell,t in workflow.water_survey_recent.items() if 0<=now-t<25}
    # Bound route searches to three goals, and one attempted sortie per
    # decision interval. No 48-drone fan-out of an expensive A* request.
    workflow.water_survey_next=now+3.
    route=WaterRoute(announced,observation,[],.55)
    candidates={}
    for _,origin,goal,_ in requests.values():
        candidates.update(candidate_cells(route,origin,goal,announced['bounds'],workflow.water_survey_recent,now))
    if not candidates:return None
    pose=tuple(agent['position'])
    try:nav=NavigationMap(announced,surfaces=workflow.known_surfaces)
    except (RouteFailure,ValueError,KeyError,TypeError):return None
    speed,climb,descent=rates
    for cell,xy in sorted(candidates.items(),key=lambda item:(math.dist(pose[:2],item[1]),item[0]))[:3]:
        try:ground=nav.ground_z(*xy)
        except (RouteFailure,ValueError,KeyError,TypeError):continue
        z=min(announced['bounds']['max'][2]-2,max(ground+18.,min(28.,pose[2])))
        if z<ground+12:continue
        goal=(*xy,z)
        if math.dist(pose,goal)<5:continue
        try:
            obstacles=workflow._obstacles(agent,observation['agents'])
            obstacles.extend(workflow.local_obstacles.get(agent['id'],()))
            outward=NavigationMap(announced,observed_obstacles=obstacles,surfaces=workflow.known_surfaces)
            leg=outward.route(pose,goal,aerial=True,radius_m=physical.get('radius_m',1.65),
                              half_height_m=.4,cruise_altitude_m=max(pose[2],goal[2]),max_expanded=512)
            reached=leg.points[-1]
            back=nav.route(reached,home,aerial=True,radius_m=physical.get('radius_m',1.65),
                           half_height_m=.4,cruise_altitude_m=max(z,home[2]),max_expanded=512)
        except (RouteFailure,IndexError,ValueError,KeyError,TypeError):
            continue
        def estimate(path):
            seconds=sum(max(math.dist(a[:2],b[:2])/speed,
                            abs(b[2]-a[2])/(climb if b[2]>=a[2] else descent))
                        for a,b in zip(path.points,path.points[1:]))
            return path.distance_m*.069+seconds*.04
        if energy is not None and estimate(leg)+estimate(back)+.5>energy:continue
        waypoints=[list(p) for p in leg.points[1:]]
        if not waypoints:continue
        workflow.water_survey_next=now+12.
        workflow.water_survey_recent[cell]=now
        job.inspection_route=True
        return {'agent_id':agent['id'],'action':'follow_route','parameters':{'waypoints':waypoints}}
    return None
