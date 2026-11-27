"""Generate explicit private test tapes, not stochastic competition results.

Two orders share exactly one generated city, real building, road source and
hazard cells. Never modify a frozen map, event plan or historical evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts.urban_terrain import Terrain
from environment.config import MAP_SIZES, Settings, EVENT_DEFAULTS
from environment.events import CELL_M, Placement, validate_tape
from environment.fire_failure import append_fire_failures, expected_failure, failure_delay
from environment.layout import generate, fingerprint, public_map


def cell_of(point):
    return tuple(math.floor(v/CELL_M) for v in point[:2])


def footprint(placement, point, kind, radius):
    cx,cy=cell_of(point)
    return [[x,y] for x in range(cx-radius,cx+radius+1)
            for y in range(cy-radius,cy+radius+1)
            if (x-cx)**2+(y-cy)**2<=radius**2+1 and placement.cell_eligible((x,y),kind)]


def row(placement,terrain,kind,point,at,site=None,height=None,expires=None,visible=None):
    return {'id':'fixture-'+kind,'kind':kind,'time':float(at),'source':list(point),
            'source_object_id':site,'parent_id':None,'spread':False,
            'cells':footprint(placement,point,kind,2 if kind in ('road_damage','mud','collapse') else 3),
            'expires_at':expires,'visibility_expires_at':visible,'rejection':None,
            'smoke_emission_height_m':height if height is not None else .2+terrain.height(*point[:2])}


def fixtures(size=25,seed=2026):
    settings=Settings.parse({'city_revision':'2.3','disaster_revision':1,'map_size':size,
        'seed':seed,'event_profile':'v3','horizon':480.,'boats':0,
        'probabilities':dict.fromkeys(EVENT_DEFAULTS['v3'],0)})
    base=generate(settings,include_events=True)
    placement=Placement(base)
    terrain=Terrain(base.get('terrain'))
    roads=defaultdict(list)
    for xy in placement.ground:roads[cell_of(xy)].append(xy)
    selected=None
    for house in sorted(base['buildings'],key=lambda x:x['id']):
        if failure_delay(house) is None or 'street_source' not in house:continue
        x,y=house['street_source']
        if placement.protected(x,y):continue
        source=[x,y,.05+terrain.height(x,y)]
        fire=row(placement,terrain,'fire',source,20.,house['id'],house['bounds']['max'][2],200.,245.)
        heat=expected_failure(fire,house,settings.horizon)
        if heat is None:continue
        options=[xy for c in heat['cells'] for xy in roads.get(tuple(c),())]
        if not options:continue
        road_xy=min(options,key=lambda p:(math.dist(p,source[:2]),p))
        if math.dist(road_xy,source[:2])>15:continue
        road_source=[*road_xy,.05+terrain.height(*road_xy)]
        road=row(placement,terrain,'road_damage',road_source,5.)
        overlap=sorted(set(map(tuple,road['cells']))&set(map(tuple,heat['cells'])))
        if not overlap:continue
        selected=(fire,heat,road,overlap);break
    if selected is None:raise RuntimeError('No eligible co-located structure/road fixture in this world')
    fire,heat,road,overlap=selected
    cases={}
    for name,road_at in (('road-first',5.),('fire-first',heat['time']+15.)):
        world=deepcopy(base)
        tape=world['event_model']
        mud=row(placement,terrain,'mud',road['source'],heat['time']+5.)
        smoke=row(placement,terrain,'smoke',road['source'],heat['time']+2.,expires=heat['time']+92.,visible=heat['time']+92.)
        tape['events']=[deepcopy(fire),{**deepcopy(road),'time':road_at},mud,smoke]
        append_fire_failures(tape,world,settings.horizon)
        validate_tape(tape,world)
        cases[name]=world
    metadata={'format':'metarescue.vis23.co-located-fixtures.v1','purpose':'explicit native acceptance, not probability evidence',
              'size':size,'seed':seed,'building_id':fire['source_object_id'],'source_m':fire['source'],
              'road_source_m':road['source'],'shared_cells':[list(x) for x in overlap],
              'collapse_at':heat['time'],'public_map_sha256':fingerprint(public_map(base)),
              'cases':{name:fingerprint(world) for name,world in cases.items()}}
    return cases,metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size',type=int,choices=MAP_SIZES,default=25)
    parser.add_argument('--seed',type=int,default=2026)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.is_relative_to(ROOT):parser.error('Use a new evidence directory outside the source checkout')
    cases,metadata=fixtures(args.size,args.seed)
    output.mkdir(parents=True,exist_ok=False)
    for name,world in cases.items():
        (output/(name+'.private.json')).write_text(json.dumps(world,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'fixture-manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metadata,ensure_ascii=False))


if __name__=='__main__':main()
