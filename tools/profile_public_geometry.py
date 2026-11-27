"""Reproducible CPU/p95/memory benchmark for old scan vs indexed broad phase."""
import argparse
import json
from pathlib import Path
from random import Random
import statistics
import time
import tracemalloc

from controller.navigation import Box
from controller.public_geometry import Cylinder
from controller.portable import _path_clear
from controller.zone_index import ZoneIndex


def zone(kind,shape):
    return {'type':kind,'risk':0.,'access':{},'constraints':[]},shape


def benchmark(n,queries,seed):
    rng=Random(seed)
    zones=[zone('land',Box((-750,-750,0),(750,750,0))),
           zone('air',Box((-750,-750,0),(750,750,100)))]
    for i in range(n):
        x,y,z=rng.uniform(-730,730),rng.uniform(-730,730),rng.uniform(0,55)
        shape=Box((x,y,z),(x+7,y+9,z+8)) if i%2 else Cylinder((x,y),5,z,z+9)
        zones.append(zone('obstacle',shape))
    routes=[]
    for i in range(queries):
        kind='ground' if i%2 else 'aerial'
        z=0 if kind=='ground' else rng.uniform(2,98)
        a=(rng.uniform(-690,690),rng.uniform(-690,690),z)
        b=(a[0]+rng.uniform(-55,55),a[1]+rng.uniform(-55,55),z)
        routes.append(({'kind':kind,'position':a},b))
    start=time.perf_counter();index=ZoneIndex(zones);build_ms=(time.perf_counter()-start)*1000
    def measure(data):
        times=[];result=[]
        before=time.process_time()
        for agent,goal in routes:
            start=time.perf_counter()
            result.append(_path_clear(data,agent,goal))
            times.append((time.perf_counter()-start)*1000)
        return result,{'cpu_s':time.process_time()-before,'p50_ms':statistics.median(times),
                       'p95_ms':sorted(times)[int(.95*(len(times)-1))],
                       'p99_ms':sorted(times)[int(.99*(len(times)-1))]}
    plain,baseline=measure(zones)
    optimized,indexed=measure(index)
    if plain!=optimized:
        raise AssertionError('Geometry parity failed')
    tracemalloc.start()
    memory_index=ZoneIndex(zones)
    for agent,goal in routes[:min(queries,100)]:
        _path_clear(memory_index,agent,goal)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    candidates=[len(index.candidates(a['position'],b,ground=a['kind']=='ground')) for a,b in routes]
    return {'zones':len(zones),'queries':queries,'seed':seed,'parity':True,
            'baseline':baseline,'indexed':indexed,'index_build_ms':build_ms,
            'index_peak_allocated_bytes':peak,'mean_candidates':statistics.mean(candidates),
            'max_candidates':max(candidates),'cpu_speedup':baseline['cpu_s']/max(indexed['cpu_s'],1e-12)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--queries',type=int,default=2000)
    args=parser.parse_args()
    if not 100<=args.queries<=10000:parser.error('Queries must be 100..10000')
    result={'method':'same routes, exact geometry parity; Python synthetic model, not Unreal',
            'cases':[benchmark(n,args.queries,89725161) for n in (300,1000,3700)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
