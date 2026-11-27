"""Compare the previous duplicate observation validation with the current path.

Synthetic public observations: this is not Unreal FPS or mission evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time

from contracts import protocol
from environment.world import World


def previous_validation(response):
    protocol.validate(response,protocol.STEP_RESPONSE)
    observation=protocol.validate_observation(response['observation'])
    for key in ('episode_id','request_id'):
        if response[key]!=observation[key]:
            raise protocol.ProtocolError('INVALID_REQUEST',f'Response and observation disagree on {key}')
    if abs(response['time']-observation['time'])>protocol.TIME_EPSILON:
        raise protocol.ProtocolError('INVALID_REQUEST','Response and observation time differ')
    return response


def measure(zones,samples=12):
    response=World(7).response('benchmark',step=True)
    obs=response['observation']
    zone=obs['zones'][0]
    obs['zones']=[dict(zone,id=f'zone-{i}') for i in range(zones)]
    digest=hashlib.sha256(protocol.encode(response)).hexdigest()
    functions={'previous':previous_validation,
               'current':lambda body:protocol.validate_episode_response(body,step=True)}
    timings={name:[] for name in functions}
    for fn in functions.values():
        assert fn(response) is response
    for i in range(samples):
        order=list(functions) if i%2==0 else list(reversed(functions))
        for name in order:
            cpu=time.process_time()
            wall=time.perf_counter()
            result=functions[name](response)
            timings[name].append((time.process_time()-cpu,(time.perf_counter()-wall)*1000))
            if result is not response:
                raise RuntimeError('Validation did not preserve the response')
    if hashlib.sha256(protocol.encode(response)).hexdigest()!=digest:
        raise RuntimeError('Validation mutated the input')
    stats={name:{'cpu_s':sum(v[0] for v in rows),
                 'median_wall_ms':statistics.median(v[1] for v in rows),
                 'max_wall_ms':max(v[1] for v in rows)} for name,rows in timings.items()}
    return {'zones':zones,'samples_per_path':samples,'input_preserved':True,**stats,
            'cpu_speedup':stats['previous']['cpu_s']/stats['current']['cpu_s']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result={'method':'synthetic public response validation, no native rendering',
            'cases':[measure(zones) for zones in (302,1002,3702)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    main()
