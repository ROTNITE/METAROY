"""Independent standard-library peer for Core 1.0 conformance.

The physical model owns private target creation, movement, custody and transfer.
It imports no project code. An accepted action alone does not establish a goal.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from random import Random
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

PROTOCOL = '1.0'
OPERATIONS = ('keep','select','switch','modify','compose','synthesize','safe_stop')
KINDS = ('aerial','ground','underground','surface_water','underwater','infrastructure','digital','operator')
ACTIONS = {
    'move': {'required_capabilities':['move'],'parameters_schema':{'type':'object',
        'properties':{'target':{'type':'array','items':{'type':'number'},'minItems':3,'maxItems':3}},
        'required':['target'],'additionalProperties':False}},
    'observe': {'required_capabilities':['sense'],'parameters_schema':{'type':'object',
        'properties':{'radius':{'type':'number','minimum':1,'maximum':25}},'required':['radius'],'additionalProperties':False}},
    'pickup': {'required_capabilities':['carry'],'parameters_schema':{'type':'object','properties':{
        'target_id':{'type':'string'}},'required':['target_id'],'additionalProperties':False}},
    'handover': {'required_capabilities':['carry'],'parameters_schema':{'type':'object','properties':{
        'station_id':{'type':'string'}},'required':['station_id'],'additionalProperties':False}},
    'hold': {'required_capabilities':['move'],'parameters_schema':{'type':'object','additionalProperties':False}},
}
GEOMETRY = {
    'land': {'kind':'box','min':[0.,0.,0.],'max':[44.,44.,0.]},
    'air': {'kind':'box','min':[0.,0.,4.],'max':[44.,44.,24.]},
}
EMPTY = {'scenario_ids':[],'parameters':{},'synthesized_scenario':None}


class Reject(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def box_segment(geometry, a, b):
    floor,ceil=0.,1.
    for value,end,lower,upper in zip(a,b,geometry['min'],geometry['max']):
        delta=end-value
        if delta==0.:
            if not lower<=value<=upper:
                return False
        else:
            passes=sorted(((lower-value)/delta,(upper-value)/delta))
            floor=max(floor,passes[0]);ceil=min(ceil,passes[1])
            if floor>ceil:
                return False
    return True


def zone(identifier, category, shape):
    return {'id':identifier,'type':category,'geometry':shape,'access':{},'risk':0.,'cost':{},
            'communication':{},'constraints':[]}


def resource(amount, alias=False):
    return {'energy':{'value':amount,'minimum':.05,'maximum':1.,'unit':'normalized',
                      **({} if alias else {'id':'energy'})}}


def scenario(identifier, goal):
    return {'id':identifier,'version':'1.0','title':goal,
            'game_model':{'class':'team','information_structure':'partial','players':['unit'],'strategies':{},
                          'information_sources':['observation'],'utilities':{},'uncertainty':[],
                          'stability_criterion':'bounded_eligibility'},
            'applicability':{'entry':[],'continuation':[],'exit':[],'emergency_exit':[]},
            'roles':[{'id':'unit','minimum':1,'maximum':2,'required_capabilities':['sense' if goal=='search' else 'carry']}],
            'objectives':[],'constraints':[],'allowed_actions':list(ACTIONS),'transition_targets':[],
            'metrics':[],'parameter_schema':{'type':'object','additionalProperties':False}}


def is_point(value):
    return (isinstance(value,list) and len(value)==3 and
            all(type(v) in (int,float) and math.isfinite(v) for v in value))


class Trial:
    def __init__(self, profile):
        self.profile=profile
        self.episode=None
        self.wire=dict(zip(ACTIONS,('flypoint-45','acquire-72','custody-23','transfer-49','brake-11'))) if profile in 'DJ' else dict(zip(ACTIONS,ACTIONS))
        self.internal={value:key for key,value in self.wire.items()}
        self.scenarios={'survey-9004':scenario('survey-9004','search'),'recover-09':scenario('recover-09','rescue')} if profile=='C' else {
            'coverage_search':scenario('coverage_search','search'),'priority_response':scenario('priority_response','rescue')}
        for key,desc in self.scenarios.items():
            desc['transition_targets']=[other for other in self.scenarios if other!=key]
            desc['allowed_actions']=list(self.wire.values())

    def capabilities(self):
        action_catalog={wire:deepcopy(ACTIONS[key]) for key,wire in self.wire.items()}
        if self.profile=='J':
            props=action_catalog[self.wire['observe']]['parameters_schema']['properties']['radius']
            props.update(minimum=100,maximum=2500)
        extensions=[]
        result={'protocol_version':PROTOCOL,'environment_id':'independent_core','mission_ids':['search_and_rescue'],
                'agent_kinds':list(KINDS),'supported_operations':list(OPERATIONS),'supported_actions':action_catalog,
                'supported_scenarios':self.scenarios,'coordinates':{'unit':'meter','axes':['x','y','z']},
                'extensions':extensions,'time_model':{'unit':'second','step_s':1}}
        if self.profile in 'DJ':
            extensions.append('metaroy.action_semantics.v1')
            result['action_semantics']={wire:{'semantic_id':key,**({'effect':'evacuated'} if key=='handover' else {})}
                                        for key,wire in self.wire.items()}
            move=action_catalog[self.wire['move']]['parameters_schema']
            move['properties']['destination']=move['properties'].pop('target')
            move['required']=['destination']
            result['action_semantics'][self.wire['move']]['bindings']={'target':'destination'}
        if self.profile=='C':
            extensions.append('metaroy.scenario_semantics.v1')
            result['scenario_semantics']={'survey-9004':{'goal':'search'},'recover-09':{'goal':'rescue'}}
        if self.profile=='D':
            extensions.append('metaroy.motion_estimates.v1')
            result['motion_estimates']={'aerial':{'speed_m_s':8.,'scan_s':1.,'move_fraction_per_m':.001,
                                                  'scan_fraction':.001,'reference_distance_m':9.}}
        if self.profile=='J':
            extensions.append('metaroy.wire_compatibility.v1')
            result['wire_compatibility']=['resource_id_from_key','short_available_actions']
            result['coordinates']={'unit':'centimeter','axes':['x','up','z']}
        return result

    def fx(self, point):
        return [point[0]*100,point[2]*100,point[1]*100] if self.profile=='J' else list(point)

    def position_from_wire(self, position):
        if not is_point(position):
            raise Reject('INVALID_COMMAND','Expected finite XYZ point')
        return [position[0]/100,position[2]/100,position[1]/100] if self.profile=='J' else position

    def reset(self, body):
        seed=body.get('seed')
        mission=body.get('mission',{})
        if type(seed) is not int or mission!={'id':'search_and_rescue','parameters':{}}:
            raise Reject('INVALID_SCENARIO','Expected documented reference mission')
        rng=Random(seed)
        renamed=self.profile=='B'
        ids=[f'opaque-{rng.randrange(10**10):010d}' for _ in range(5)] if renamed else [
            'mobile-a','mobile-b','mobile-x','casualty-a','assistance-yard']
        self.episode=uuid4().hex
        self.t=0
        self.status='running'
        self.plan=deepcopy(EMPTY)
        self.roles={}
        self.agents={
            ids[0]:{'kind':'aerial','position':[8.,8.,14.],'energy':.94,'abilities':['sense','move'],'cargo':None},
            ids[1]:{'kind':'ground','position':[3.,3.,0.],'energy':.93,'abilities':['carry','move'],'cargo':None},
        }
        if self.profile=='E':
            self.agents[ids[2]]={'kind':'aerial','position':[16.,16.,14.],'energy':.94,'abilities':['sense','move'],'cargo':None}
        self.destination=ids[-1]
        self.targets={ids[3]:{'position':[rng.uniform(7.8,9.3),rng.uniform(7.8,9.3),0.],
                              'known':False,'state':'waiting','seen':None,'appear':125 if self.profile in 'FD' else 0}}
        self.areas=[zone(f'z-{idx*37}' if renamed else key,key,shape) for idx,(key,shape) in enumerate(GEOMETRY.items())]
        if self.profile=='H':
            for idx in range(300):
                x=21+idx%15;y=21+idx//15
                self.areas.append(zone(f'barrier-{idx}','obstacle',{'kind':'box','min':[x,y,2.],
                                                                 'max':[x+.3,y+.3,7.]}))
        if self.profile=='I':
            self.areas.append(zone('three-dimensional-pier','obstacle',
                                   {'kind':'box','min':[14.,5.,0.],'max':[15.,14.,9.]}))
        self.journal=[]
        self.private_handover=[]
        self.log('reset',{'seed':seed})
        return self.response(body['request_id'])

    def reachable(self, aid):
        return self.profile!='F' or self.agents[aid]['kind']!='aerial' or not 66<=self.t<78

    def observation(self, request):
        alias=self.profile=='J'
        agents=[]
        for aid,a in self.agents.items():
            choices=[wire for key,wire in self.wire.items() if set(ACTIONS[key]['required_capabilities'])<=set(a['abilities'])]
            fresh={'observed_at':self.t,'received_at':self.t,'source_id':aid,'state':'current'}
            connected=self.reachable(aid)
            agents.append({'id':aid,'kind':a['kind'],'position':self.fx(a['position']),
                'status':'active' if connected else 'offline','capabilities':a['abilities'],'resources':resource(a['energy'],alias),
                'communication':{'reachable':connected},'role':self.roles.get(aid),'scenario_ids':self.plan['scenario_ids'],
                'available_actions':(choices if alias else [{'action':wire} for wire in choices]) if connected else [],
                'constraints':[],**({'freshness':{'position':fresh,'resources':fresh}} if self.profile in 'FD' else {})})
        targets=[{'id':pid,'kind':'person','status':'resolved' if t['state']=='evacuated' else 'confirmed',
                  'position':self.fx(t['position']),'evacuation_state':t['state'],'observed_at':t['seen'],
                  'priority':1.,'confidence':1.} for pid,t in self.targets.items() if t['known']]
        goals=[{'id':'locate','type':'search','priority':.4,'status':'active'}]
        goals.extend({'id':'answer-'+t['id'],'type':'rescue','priority':1.,
                      'status':'completed' if t['evacuation_state']=='evacuated' else 'active','target_id':t['id']} for t in targets)
        zones=deepcopy(self.areas)
        if alias:
            for item in zones:
                shape=item['geometry']
                shape.update(min=self.fx(shape['min']),max=self.fx(shape['max']))
        if self.profile=='B':
            agents.reverse();targets.reverse();zones.reverse()
        ratio=sum(self.reachable(aid) for aid in self.agents)/len(self.agents)
        return {'protocol_version':PROTOCOL,'episode_id':self.episode,'request_id':request,'time':self.t,
                'mission':{'id':'search_and_rescue','objectives':goals,'priorities':[],'directives':[],'constraints':[],
                           'status':{'running':'active','success':'completed','time_limit':'failed','safe_stopped':'safe_stopped'}[self.status],
                           'termination':{'horizon':200}},
                'agents':agents,'infrastructure':[{'id':self.destination,'kind':'base_station','status':'active',
                                                 'position':self.fx([3.,3.,0.]),'capabilities':['handover'],
                                                 'resources':{},'communication':{},'constraints':[]},
                                                 *([{'id':'aerial-recovery-platform','kind':'base_station','status':'active',
                                                      'position':self.fx([8.,8.,14.]),'capabilities':['support_air_recovery'],
                                                      'resources':{},'communication':{},'constraints':[]}]
                                                   if self.profile in 'DF' else [])],
                'targets':targets,'zones':zones,'events':[],'constraints':[],
                'network':{'connectivity_score':ratio,'partition_count':int(ratio<1),'reachable_agent_ratio':ratio,
                           'average_link_quality':ratio,'mesh_available':True,'known_links':[]},
                'metrics':{'confirmed_evacuations':len(self.private_handover)},
                'observation_quality':{'global_completeness':1.,'maximum_delay':0.,'stale_data_ratio':0.,
                                       'contradiction_count':0,'unknown_state_ratio':0.}}

    def response(self, request, *, results=None):
        body={'protocol_version':PROTOCOL,'episode_id':self.episode,'request_id':request,'time':self.t,
              'status':self.status,'observation':self.observation(request)}
        if results is not None:
            body.update(command_results=results,events=[],metrics=body['observation']['metrics'])
        return body

    def log(self, kind, payload):
        self.journal.append({'record_type':kind,'episode_id':self.episode,'time':self.t,'payload':deepcopy(payload)})

    def preflight(self, command):
        if not isinstance(command,dict):
            raise Reject('INVALID_COMMAND','Command must be object')
        aid=command.get('agent_id'); a=self.agents.get(aid)
        wire=command.get('action'); action=self.internal.get(wire)
        params=command.get('parameters')
        if not a or not action or not self.reachable(aid) or not isinstance(params,dict):
            raise Reject('INVALID_COMMAND','Unknown agent, action or inaccessible actor')
        if set(ACTIONS[action]['required_capabilities'])-set(a['abilities']):
            raise Reject('INVALID_COMMAND','Agent lacks capabilities')
        if action=='move':
            name='destination' if self.profile in 'DJ' else 'target'
            if set(params)!={name}:
                raise Reject('INVALID_COMMAND','Unknown move parameters')
            goal=self.position_from_wire(params[name])
            medium=GEOMETRY['air' if a['kind']=='aerial' else 'land']
            if not all(low<=v<=high for low,v,high in zip(medium['min'],goal,medium['max'])):
                raise Reject('CONSTRAINT_VIOLATION','Goal outside published motion medium')
            if any(z['type']=='obstacle' and box_segment(z['geometry'],a['position'],goal) for z in self.areas):
                raise Reject('CONSTRAINT_VIOLATION','Proposed motion intersects occupied volume')
            return aid,action,goal
        expected={'observe':{'radius'},'pickup':{'target_id'},'handover':{'station_id'},'hold':set()}[action]
        if set(params)!=expected:
            raise Reject('INVALID_COMMAND','Invalid action parameters')
        if action=='observe':
            radius=params['radius']
            scale=100 if self.profile=='J' else 1
            if type(radius) not in (int,float) or not 1<=radius/scale<=25:
                raise Reject('INVALID_COMMAND','Invalid observation radius')
            params={'radius':radius/scale}
        return aid,action,params

    def apply(self, aid, action, params, decision_id, index):
        a=self.agents[aid]
        status='completed'
        if action=='move':
            step=min(1.,8./max(math.dist(a['position'],params),1e-9))
            destination=[x+step*(y-x) for x,y in zip(a['position'],params)]
            a['energy']-=.001*math.dist(a['position'],destination)
            a['position']=destination
        elif action=='observe':
            a['energy']-=.001
        elif action=='pickup':
            t=self.targets.get(params['target_id'])
            if not t or not t['known'] or t['state']!='waiting' or a['cargo'] or math.dist(t['position'],a['position'])>1.:
                status='failed'
            else:
                t['state']='loaded';a['cargo']=params['target_id']
        elif action=='handover':
            pid=a['cargo']
            if not pid or params['station_id']!=self.destination or math.dist(a['position'],[3.,3.,0.])>1.:
                status='failed'
            else:
                self.targets[pid].update(state='evacuated',position=[3.,3.,0.]);a['cargo']=None
                physical={'effect':'evacuated','target_id':pid,'agent_id':aid,'decision_id':decision_id,
                          'command_index':index,'outcome_id':f'{self.episode}:{pid}'}
                self.private_handover.append(physical);self.log('physical_outcome',physical)
        return {'agent_id':aid,'action':self.wire[action],'decision_id':decision_id,'command_index':index,
                'validation_status':'accepted','delivery_status':'delivered','execution_status':status,'updated_at':self.t}

    def step(self, doc):
        if any(key not in doc for key in ('request_id','decision_id','commands','time','operation','selected_configuration',
                                         'role_assignments','explanation','previous_configuration','candidate_evaluations','constraint_checks')):
            raise Reject('INVALID_REQUEST','Incomplete decision')
        if doc.get('episode_id')!=self.episode or doc['time']!=self.t or self.status!='running':
            raise Reject('INVALID_REQUEST','Episode or time differs')
        op=doc['operation']; selections=doc['selected_configuration'].get('scenario_ids')
        if op not in OPERATIONS or not isinstance(selections,list) or any(key not in self.scenarios for key in selections):
            raise Reject('INVALID_SCENARIO','Invalid operation or scenario ID')
        commands=[self.preflight(command) for command in doc['commands']]
        self.log('decision',doc)
        self.plan=deepcopy(doc['selected_configuration'])
        self.roles=deepcopy(doc['role_assignments'])
        self.t+=1
        results=[self.apply(*item,doc['decision_id'],idx) for idx,item in enumerate(commands)]
        for record in results:
            self.log('command_result',record)
        if op=='safe_stop':
            self.status='safe_stopped'
        for t in self.targets.values():
            if t['state']!='waiting' or t['appear']>self.t:
                continue
            if any(action=='observe' and math.dist(self.agents[aid]['position'][:2],t['position'][:2])<=params['radius']
                   and abs(self.agents[aid]['position'][2]-t['position'][2])<=25.
                   and not any(z['type']=='obstacle' and box_segment(z['geometry'],self.agents[aid]['position'],t['position'])
                               for z in self.areas)
                   for aid,action,params in commands):
                t['known']=True;t['seen']=self.t
        if self.private_handover:
            self.status='success'
        if self.t>=200 and self.status=='running':
            self.status='time_limit'
        return self.response(doc['request_id'],results=results)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def dispatch(self, method):
        url=urlsplit(self.path)
        endpoint=url.path.removeprefix('/v1/environment/')
        query=parse_qs(url.query)
        state=self.server.trial
        body={}
        if method=='POST':
            try:
                size=int(self.headers.get('content-length','0'))
                if not 0<size<=2_000_000:
                    raise ValueError('body length')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict) or body.get('protocol_version')!=PROTOCOL or not isinstance(body.get('request_id'),str):
                    raise ValueError('body envelope')
            except (ValueError,TypeError) as error:
                raise Reject('INVALID_REQUEST','Invalid Core envelope') from error
        with self.server.lock:
            if method=='GET' and endpoint=='health':
                return {'component':'environment','status':'ready','environment_id':'independent_core',
                        'capabilities_url':'/v1/environment/capabilities'}
            if method=='GET' and endpoint=='capabilities':
                return state.capabilities()
            if method=='POST' and endpoint=='reset':
                return state.reset(body)
            if state.episode is None:
                raise Reject('INVALID_REQUEST','Reset required')
            if method=='GET' and endpoint=='observation':
                return state.observation(query.get('request_id',[uuid4().hex])[0])
            if method=='POST' and endpoint=='step':
                return state.step(body)
            if method=='GET' and endpoint=='logs':
                if query.get('episode_id')!=[state.episode]:
                    raise Reject('INVALID_REQUEST','Journal episode differs')
                return {'episode_id':state.episode,'records':state.journal,'next_cursor':None}
            if method=='POST' and endpoint=='close' and body.get('episode_id')==state.episode:
                state.log('close',{})
                if self.server.output:
                    self.server.output.write_text(json.dumps({'episode_id':state.episode,'status':state.status,
                        'handover':state.private_handover,'time':state.t},indent=2),encoding='utf-8')
                return {'episode_id':state.episode,'request_id':body['request_id'],'status':'closed'}
            raise Reject('INVALID_REQUEST','Unknown Core operation')

    def answer(self, method):
        try:
            reply=self.dispatch(method);status=200
        except Reject as error:
            reply={'error':{'code':error.code,'message':str(error),'details':{}}};status=400
        raw=json.dumps({'protocol_version':PROTOCOL,**reply},allow_nan=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def do_GET(self):self.answer('GET')
    def do_POST(self):self.answer('POST')


def serve(profile, port, output=None):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.trial=Trial(profile);server.lock=Lock();server.output=output
    return server


if __name__=='__main__':
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='A',choices=list('ABCDEFGHIJ'))
    parser.add_argument('--port',type=int,default=8139)
    parser.add_argument('--truth',type=Path)
    opts=parser.parse_args()
    with serve(opts.profile,opts.port,opts.truth) as server:
        server.serve_forever()
