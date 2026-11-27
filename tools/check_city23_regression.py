"""Compare legacy world hashes with an unmodified baseline checkout.

No Unreal process is launched. The baseline and candidate are read-only.
Use --full to cover every supported size, three seeds and both event profiles.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from environment.config import MAP_SIZES, Settings, EVENT_DEFAULTS
from environment.events import validate_tape, generate_tape
from environment.layout import generate, fingerprint, public_map

REVISIONS=('1.1','2.0','2.1','2.2','2.3')
BASELINE_WORKER=r'''
import json,sys
from environment.config import Settings
from environment.layout import generate,fingerprint,public_map
for line in sys.stdin:
    options=json.loads(line)
    world=generate(Settings.parse(options),include_events=True)
    print(json.dumps({"world":fingerprint(world),"public":fingerprint(public_map(world)),
                      "preflight":world["preflight"]["passed"]}),flush=True)
'''


def git_head(root):
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def frozen(root):
    paths=[]
    for directory in ('environment','contracts','controller'):
        paths.extend(p for p in (root/directory).rglob('*.py') if p.is_file())
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def primary(tape):
    return [r for r in tape['events'] if not r['spread'] and 'cause' not in r]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--size',type=int,choices=MAP_SIZES,action='append')
    parser.add_argument('--seed',type=int,action='append')
    args=parser.parse_args()
    baseline=args.baseline_root.resolve(strict=True)
    output=args.output.resolve()
    if baseline==ROOT or output.exists() or output.is_relative_to(baseline) or output.is_relative_to(ROOT):
        parser.error('Use a separate baseline checkout and a new evidence path outside both worktrees')
    sizes=tuple(args.size or (MAP_SIZES if args.full else (25,75,150)))
    seeds=tuple(args.seed or ((2026,17,90210) if args.full else (2026,)))
    matrix=[{'city_revision':revision,'map_size':size,'seed':seed,'event_profile':profile,
             'horizon':480.,'boats':0,'probabilities':dict.fromkeys(EVENT_DEFAULTS[profile],100)}
            for revision,size,seed,profile in itertools.product(REVISIONS,sizes,seeds,('v052','v3'))]
    # Exercise full fleet/competition geometry separately, without mixing
    # these results into earlier frozen mission plans.
    matrix.append({'city_revision':'2.3','map_size':150,'seed':2026,'event_profile':'v3',
                   'horizon':480.,'boats':4,'scouts':48,'rescuers':40,'people':600,'competition':True})
    before=(frozen(baseline),frozen(ROOT))
    baseline_head,candidate_head=git_head(baseline),git_head(ROOT)
    start=time.monotonic()
    process=subprocess.run([sys.executable,'-B','-X','utf8','-c',BASELINE_WORKER],
        cwd=baseline,input=''.join(json.dumps(row)+'\n' for row in matrix),
        text=True,capture_output=True,check=True)
    expected=[json.loads(line) for line in process.stdout.splitlines()]
    if len(expected)!=len(matrix):raise RuntimeError('Baseline did not produce every requested world')
    rows=[]
    for options,reference in zip(matrix,expected):
        world=generate(Settings.parse(options),include_events=True)
        measured={'world':fingerprint(world),'public':fingerprint(public_map(world)),
                  'preflight':world['preflight']['passed']}
        row={'settings':options,'baseline':reference,'candidate':measured,
             'legacy_passed':reference==measured and measured['preflight']}
        validate_tape(world['event_model'],world)
        if options['city_revision']=='2.3':
            revised=generate(Settings.parse({**options,'disaster_revision':1}),include_events=True)
            tape=revised['event_model']
            row['v2']={'world':fingerprint(revised),'public':fingerprint(public_map(revised)),
                       'causal_collapses':sum('cause' in r for r in tape['events']),
                       'preflight':revised['preflight']['passed'],
                       'primary_trials_preserved':primary(tape)==primary(world['event_model']),
                       'repeatable':tape==generate_tape(revised)}
            row['v2']['passed']=(row['v2']['public']==measured['public'] and row['v2']['preflight'] and
                row['v2']['primary_trials_preserved'] and row['v2']['repeatable'])
            validate_tape(tape,revised)
        rows.append(row)
        print(json.dumps({'settings':options,'legacy_passed':row['legacy_passed'],'v2':row.get('v2')}),
              flush=True)
    preserved=before==(frozen(baseline),frozen(ROOT)) and (baseline_head,candidate_head)==(git_head(baseline),git_head(ROOT))
    report={'format':'metarescue.city23.regression.v1','baseline_commit':baseline_head,'candidate_commit':candidate_head,
            'seconds':time.monotonic()-start,'sources_preserved':preserved,'legacy_cases':len(rows),
            'v2_cases':sum('v2' in row for row in rows),'rows':rows}
    report['passed']=preserved and all(row['legacy_passed'] and row.get('v2',{}).get('passed',True) for row in rows)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2,ensure_ascii=False)
    print(json.dumps({'passed':report['passed'],'output':str(output),'legacy_cases':report['legacy_cases'],'v2_cases':report['v2_cases']}))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
