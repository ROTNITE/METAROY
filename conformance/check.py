"""Independent Core 1.0 wire and two-process mission checks."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from urllib.error import HTTPError
from uuid import uuid4

from conformance.reference_controller import public_call, request
from conformance.reference_environment import OPERATIONS, serve


REQUIRED = set('protocol_version episode_id request_id time mission agents infrastructure targets zones network events constraints metrics observation_quality'.split())
SEEDS = (89725161, 89725187, 89725213)


def observation(frame):
    if not REQUIRED<=frame.keys() or frame['protocol_version']!='1.0':
        raise ValueError('Incomplete normalized Observation')
    unique_actors=set()
    for agent in frame['agents']:
        if agent['id'] in unique_actors or any(type(value) not in (int,float) for value in agent['position'] or []):
            raise ValueError('Invalid agent identity or coordinates')
        unique_actors.add(agent['id'])
        for value in agent['resources'].values():
            if not 0<=value['minimum']<=value['value']<=value['maximum']<=1 or value['unit']!='normalized':
                raise ValueError('Invalid normalized resource')
    return frame


def prepare(observation, operation='keep', selected=None, previous=None, commands=()):
    def config(ids):
        return {'scenario_ids':ids,'parameters':{},'synthesized_scenario':None}
    return request(episode_id=observation['episode_id'],time=observation['time'],decision_id=uuid4().hex,
                   operation=operation,selected_configuration=config(selected or []),
                   previous_configuration={'scenario_ids':previous or [],'role_assignments':{}},
                   candidate_evaluations=[],role_assignments={},commands=list(commands),constraint_checks=[],
                   explanation={'observation_refs':[observation['request_id']],
                                'reason_codes':['independent_wire_contract_test'],'expected_effects':[],
                                'confidence':1.})


def asserted_step(origin, decision, *, accepted=True):
    try:
        response=public_call(origin,'step',decision)
    except HTTPError as error:
        if accepted:
            raise
        response=json.load(error)
        if response.get('error',{}).get('code')!='INVALID_COMMAND':
            raise ValueError('Expected normative INVALID_COMMAND') from error
        return response
    if not accepted or response['request_id']!=decision['request_id'] or response['episode_id']!=decision['episode_id']:
        raise ValueError('Invalid step execution or correlation')
    return response


def environment_checks(origin, seed):
    health=public_call(origin,'health');caps=public_call(origin,'capabilities')
    if health['component']!='environment' or health['status']!='ready' or not {'keep','switch'}<=set(caps['supported_operations']):
        raise ValueError('Unexpected environment readiness or operations')
    episodes=[]
    for _ in range(2):
        body=request(seed=seed,mission={'id':'search_and_rescue','parameters':{}})
        reply=public_call(origin,'reset',body)
        current=observation(reply['observation'])
        if reply['request_id']!=body['request_id'] or reply['time']!=0:
            raise ValueError('Reset response differs from the request')
        keep=asserted_step(origin,prepare(current))['observation']
        ids=list(caps['supported_scenarios'])
        selected=asserted_step(origin,prepare(keep,'select',ids[:1]))['observation']
        switched=asserted_step(origin,prepare(selected,'switch',ids[1:2],ids[:1]))['observation']
        wrong={'agent_id':switched['agents'][0]['id'],'action':'undeclared-action','parameters':{}}
        asserted_step(origin,prepare(switched,selected=ids[1:2],previous=ids[1:2],commands=[wrong]),accepted=False)
        actual=observation(public_call(origin,'observation'))
        if actual['time']!=switched['time']:
            raise ValueError('Rejected command or GET advanced time')
        page=public_call(origin,'logs?episode_id='+actual['episode_id'])
        if page['episode_id']!=actual['episode_id'] or not page['records']:
            raise ValueError('Missing public journal entries')
        public_call(origin,'close',request(episode_id=actual['episode_id']))
        episodes.append({key:val for key,val in actual.items() if key not in ('episode_id','request_id')})
    if episodes[0]!=episodes[1]:
        raise ValueError('Same seed and decisions differ')
    return {'health_caps':True,'observations':True,'keep_select_switch':True,'invalid_command':True,
            'same_seed':True,'public_logs':True,'close':True}


def run_controller(origin, work, mode, seed, *, reference=False):
    base=[sys.executable,'-B','-X','utf8','-m']
    if reference:
        argv=[*base,'conformance.reference_controller','--environment',origin,'--health-port','0',
              '--seed',str(seed),'--output',str(work/'reference.jsonl')]
    else:
        argv=[*base,'controller.main','--environment',origin,'--health-port','0','--seed',str(seed),
              '--public-core','--core-mode',mode,'--scout-patrol','net65','--output',str(work/'controller')]
    with (work/'stdout.log').open('xb') as output:
        done=subprocess.run(argv,stdout=output,stderr=subprocess.STDOUT,timeout=90,check=False)
    manifest=work/'controller/manifest.json'
    return done.returncode, json.loads(manifest.read_bytes()) if manifest.is_file() else {}


def matrix(output, profiles, seeds):
    with serve('G',0) as server:
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            origin=f'http://127.0.0.1:{server.server_port}'
            readiness=environment_checks(origin,seeds[0])
        finally:
            server.shutdown();worker.join()
    rows=[]
    for profile in profiles:
        for seed in seeds:
            modes=('strict','declared') if profile=='J' else ('strict',)
            for mode in modes:
                dest=output/f'{profile}-{seed}-{mode}';dest.mkdir()
                with serve(profile,0,dest/'truth.json') as server:
                    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
                    try:
                        url=f'http://127.0.0.1:{server.server_port}'
                        status,report=run_controller(url,dest,mode,seed)
                        truth=server.trial.private_handover if server.trial.episode else []
                        expected_refusal=profile=='J' and mode=='strict'
                        valid=(status!=0 and not truth) if expected_refusal else (
                            status==0 and report['episode_status']=='success' and len(truth)==1)
                        repeats=report.get('patrol',{}).get('net65_repeats',0)
                        valid=valid and (profile not in ('D','F') or repeats>0)
                        decisions=[row['payload'] for row in getattr(server.trial,'journal',[]) if row['record_type']=='decision']
                        if decisions and not all(row['explanation']['reason_codes'] for row in decisions):
                            valid=False
                        rows.append({'profile':profile,'seed':seed,'mode':mode,'status':report.get('episode_status'),
                                     'exit':status,'physical_handovers':len(truth),'confirmed_repeats':repeats,
                                     'operation_counts':{kind:sum(d['operation']==kind for d in decisions)
                                                         for kind in OPERATIONS},'passed':valid})
                        if report.get('error'):
                            rows[-1]['error']=report['error']
                        (dest/'public.json').write_text(json.dumps(getattr(server.trial,'journal',[]),indent=2),encoding='utf-8')
                        print(profile,seed,mode,'pass' if valid else 'fail',flush=True)
                    finally:
                        server.shutdown();worker.join()
    data={'environment_checks':readiness,'trials':rows,'passed':all(r['passed'] for r in rows)}
    (output/'report.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    return 0 if data['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profiles',default='ABCDEFGHIJ')
    parser.add_argument('--seed',type=int,action='append',default=[])
    args=parser.parse_args()
    if not args.profiles or set(args.profiles)-set('ABCDEFGHIJ'):
        parser.error('Profiles must use A through J')
    args.output.mkdir(parents=True,exist_ok=False)
    raise SystemExit(matrix(args.output,args.profiles,args.seed or SEEDS))
