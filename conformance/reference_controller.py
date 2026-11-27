"""Reference active HTTP controller for the independently documented Core.

Uses standard-library HTTP, geometry and published action declarations. The
environment owns time, target visibility, command execution and results.
"""
from __future__ import annotations

import argparse
import heapq
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen
from uuid import uuid4

OPERATIONS=('keep','select','switch','modify','compose','synthesize','safe_stop')

VERSION='1.0'
SEMANTICS='metaroy.action_semantics.v1'


def public_call(origin, endpoint, body=None):
    payload=json.dumps(body,allow_nan=False).encode() if body is not None else None
    request=Request(origin+'/v1/environment/'+endpoint,data=payload,
                    headers={'Content-Type':'application/json','Accept':'application/json'})
    with urlopen(request,timeout=45) as transport:
        response=json.load(transport)
    if response.get('protocol_version')!=VERSION or 'error' in response:
        raise ValueError('Expected successful Core 1.0 reply')
    return response


def request(**values):
    return {'protocol_version':VERSION,'request_id':uuid4().hex,**values}


def catalog(capabilities):
    actions=capabilities['supported_actions']
    table={key:(key,{}) for key in ('move','follow_route','observe','inspect','pickup','handover','hold') if key in actions}
    if SEMANTICS in capabilities.get('extensions',()):
        for key,value in capabilities['action_semantics'].items():
            name=value['semantic_id']
            if name in ('move','follow_route','observe','inspect','pickup','handover','hold') and key in actions:
                table[name]=(key,value.get('bindings',{}))
    return table


def box(shape):
    if shape.get('kind',shape.get('type')) not in ('aabb','box'):
        return None
    return {'min':shape['min'],'max':shape['max']}


def unobstructed(volumes, origin, destination, clearance=0.):
    for shape in volumes:
        if shape is None:
            return False
        entry,exit=0.,1.
        for axis,(a,b,lo,hi) in enumerate(zip(origin,destination,shape['min'],shape['max'])):
            if axis<2:
                lo-=clearance;hi+=clearance
            if a==b:
                if not lo<=a<=hi:
                    entry,exit=1.,0.
                    break
                continue
            start,end=sorted(((lo-a)/(b-a),(hi-a)/(b-a)))
            entry=max(entry,start);exit=min(exit,end)
            if entry>exit:
                break
        if entry<=exit:
            return False
    return True


def ground_route(volumes, origin, destinations, bounds, clearance):
    """Short grid search using only public geometry and the service radius."""
    if not bounds:
        return None
    step=3.
    def p(key):
        return (origin[0]+key[0]*step,origin[1]+key[1]*step,origin[2])
    def estimate(pos):
        return min(math.dist(pos,goal) for goal in destinations)

    start=(0,0)
    queue=[(estimate(origin),0.,start)]
    prev={}
    distances={start:0.}
    for _ in range(3000):
        if not queue:
            return None
        _,cost,key=heapq.heappop(queue)
        if cost!=distances[key]:
            continue
        here=p(key)
        targets=sorted(destinations,key=lambda target:math.dist(here,target))
        target=next((target for target in targets if unobstructed(volumes,here,target,clearance)),None)
        if target is not None:
            route=[target]
            while key!=start:
                route.append(p(key));key=prev[key]
            return list(reversed(route))
        for dx,dy in ((1,0),(0,1),(-1,0),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            neighbour=key[0]+dx,key[1]+dy
            candidate=p(neighbour)
            if not any(all(shape['min'][i]<=candidate[i]<=shape['max'][i] for i in (0,1)) for shape in bounds):
                continue
            new_cost=cost+math.dist(here,candidate)
            if new_cost>=distances.get(neighbour,math.inf) or not unobstructed(volumes,here,candidate,clearance):
                continue
            prev[neighbour]=key;distances[neighbour]=new_cost
            heapq.heappush(queue,(new_cost+estimate(candidate),new_cost,neighbour))
    return None


def search_points(obs):
    for area in obs['zones']:
        if area['type']!='air':
            continue
        domain=box(area['geometry'])
        if not domain:
            continue
        lo,hi=domain['min'],domain['max']
        height=min(hi[2],max(lo[2],min(25.,.5*(lo[2]+hi[2]))))
        for x in (.10,.25,.50,.75,.90):
            for y in (.10,.25,.50,.75,.90):
                yield (lo[0]+x*(hi[0]-lo[0]),lo[1]+y*(hi[1]-lo[1]),height)


def policy(obs, caps, table, receipts, last_plan):
    commands=[]
    known={p['id']:p for p in obs['targets']}
    stations=[s for s in obs['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
    obstacles=[box(z['geometry']) for z in obs['zones'] if z['type'] in ('obstacle','restricted')]
    entries=obs['agents']
    selected_targets=set()
    tolerances=caps.get('physical_tolerances',{})
    pickup=tolerances.get('pickup_distance_m',1.)
    handover=tolerances.get('handover_distance_m',1.)
    radius=10.
    if 'observe' in table:
        wire, names=table['observe']
        schema=caps['supported_actions'][wire]['parameters_schema']
        bound=schema.get('properties',{}).get(names.get('radius','radius'),{}).get('maximum')
        if type(bound) in (int,float):
            radius=min(42.,bound)
    def command(agent, semantic, **params):
        if semantic not in table:
            return False
        wire, bindings=table[semantic]
        if not any((row if isinstance(row,str) else row['action'])==wire for row in agent['available_actions']):
            return False
        commands.append({'agent_id':agent['id'],'action':wire,
                         'parameters':{bindings.get(k,k):v for k,v in params.items()}})
        return True

    def move(agent, goal):
        return unobstructed(obstacles,agent['position'],goal) and command(agent,'move',target=list(goal))

    def approach(agent, position, distance):
        origin=agent['position']
        ground=(position[0],position[1],origin[2])
        heading=math.atan2(origin[1]-ground[1],origin[0]-ground[0])
        goals=[(ground[0]+.8*distance*math.cos(a),ground[1]+.8*distance*math.sin(a),ground[2])
               for a in (heading,heading+math.pi/2,heading-math.pi/2,heading+math.pi)]
        clearance=agent.get('physical',{}).get('chassis_radius_m',0.)
        if any(unobstructed(obstacles,origin,goal,clearance) and command(agent,'move',target=list(goal)) for goal in goals):
            return True
        domains=[parsed for z in obs['zones'] if z['type']=='land' and (parsed:=box(z['geometry']))]
        route=ground_route(obstacles,origin,goals,domains,clearance)
        if not route:
            return False
        if len(route)>1 and 'follow_route' in table:
            if command(agent,'follow_route',waypoints=route[:64]):
                return True
        return move(agent,route[0])

    for agent in entries:
        aid=agent['id']; a=agent['position']
        if a is None or agent['status'] not in ('active','degraded','busy') or agent['communication'].get('reachable') is False:
            continue
        if aid in receipts['pending']:
            continue
        if agent['kind']=='aerial' and 'sense' in agent['capabilities']:
            exposed=receipts['surveyed'].setdefault(aid,set())
            at=tuple(a)
            if not any(math.dist(at,p)<2. for p in exposed):
                if command(agent,'observe',radius=radius):
                    continue
            destinations=sorted(search_points(obs),key=lambda goal:(math.dist(at,goal),goal))
            if any(move(agent,goal) for goal in destinations
                   if not any(math.dist(goal,p)<2. for p in exposed)):
                continue
        if agent['kind']!='ground' or 'carry' not in agent['capabilities'] or not stations:
            continue
        station=min(stations,key=lambda s:math.dist(a,s['position']))
        if target:=receipts['cargo'].get(aid):
            if math.dist(a,station['position'])<=handover:
                command(agent,'handover',station_id=station['id'])
            else:
                approach(agent,station['position'],handover)
            continue
        departure=agent.get('physical',{})
        if ('metarescue.follow_route.v1' in caps.get('extensions',()) and departure.get('at_dock') and
                departure.get('dock_exit_waypoints') and known):
            if command(agent,'follow_route',waypoints=departure['dock_exit_waypoints']):
                continue
        waiting=[t for t in known.values() if t['kind']=='person' and t.get('evacuation_state','waiting')=='waiting'
                 and t['id'] not in selected_targets and t['status'] in ('suspected','confirmed') and t.get('position')
                 and (t['status']=='confirmed' or 'inspect' in table)]
        goal=min(waiting,key=lambda t:math.dist(a,t['position']),default=None)
        if goal:
            action='pickup' if goal['status']=='confirmed' else 'inspect'
            if (command(agent,action,target_id=goal['id']) if math.dist(a,goal['position'])<=pickup
                    else approach(agent,goal['position'],pickup)):
                selected_targets.add(goal['id'])
    return request(episode_id=obs['episode_id'],time=obs['time'],decision_id=uuid4().hex,
                   operation='keep',previous_configuration={'scenario_ids':last_plan['scenario_ids'],'role_assignments':{}},
                   selected_configuration=last_plan,candidate_evaluations=[],role_assignments={},
                   commands=commands,constraint_checks=[],explanation={'observation_refs':[obs['request_id']],
                     'reason_codes':['public_actions_and_confirmed_custody'],'expected_effects':[],'confidence':1.})


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        payload=json.dumps({'protocol_version':VERSION,'component':'controller','controller_id':'independent_reference',
                            'status':self.server.status,'supported_operations':OPERATIONS}).encode()
        self.send_response(200 if self.path=='/v1/controller/health' else 404)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def run(origin, health_port, seed, output):
    health=ThreadingHTTPServer(('127.0.0.1',health_port),Health)
    health.status='ready'
    watcher=Thread(target=health.serve_forever,daemon=True);watcher.start()
    receipts={'pending':{},'cargo':{},'surveyed':{}}
    last_plan={'scenario_ids':[],'parameters':{},'synthesized_scenario':None}
    try:
        caps=public_call(origin,'capabilities')
        table=catalog(caps)
        reverse={wire:key for key,(wire,bindings) in table.items()}
        mission={'id':'search_and_rescue','parameters':{}}
        current=public_call(origin,'reset',request(seed=seed,mission=mission))
        with output.open('x',encoding='utf-8') as stream:
            while current['status']=='running':
                obs=current['observation']
                decision=policy(obs,caps,table,receipts,last_plan)
                current=public_call(origin,'step',decision)
                if current['request_id']!=decision['request_id'] or current['episode_id']!=decision['episode_id']:
                    raise ValueError('Uncorrelated step response')
                for index,cmd in enumerate(decision['commands']):
                    aid=cmd['agent_id']
                    receipts['pending'][aid]=(decision['decision_id'],index,cmd)
                agents={a['id']:a for a in current['observation']['agents']}
                for result in current['command_results']:
                    aid=result['agent_id']
                    pending=receipts['pending'].get(aid)
                    if not pending or pending[:2]!=(result['decision_id'],result['command_index']):
                        continue
                    outcome=result['execution_status']
                    if outcome in ('completed','failed','cancelled') or result.get('delivery_status') in ('lost','expired'):
                        receipts['pending'].pop(aid)
                    if outcome=='completed' and reverse.get(pending[2]['action'])=='observe':
                        position=agents.get(aid,{}).get('position')
                        if position:
                            receipts['surveyed'].setdefault(aid,set()).add(tuple(position))
                    if outcome=='completed' and reverse.get(pending[2]['action'])=='pickup':
                        wire=table['pickup'][1].get('target_id','target_id')
                        receipts['cargo'][aid]=pending[2]['parameters'][wire]
                    if outcome=='completed' and reverse.get(pending[2]['action'])=='handover':
                        receipts['cargo'].pop(aid,None)
                stream.write(json.dumps({'decision':decision,'response':current})+'\n');stream.flush()
        close=public_call(origin,'close',request(episode_id=current['episode_id']))
        return {'status':current['status'],'episode':current['episode_id'],'closed':close.get('status')=='closed' or close.get('closed') is True,
                'metrics':current.get('metrics',current['observation']['metrics'])}
    finally:
        health.shutdown();health.server_close();watcher.join()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',required=True)
    parser.add_argument('--health-port',type=int,default=8138)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.environment,args.health_port,args.seed,args.output)))
