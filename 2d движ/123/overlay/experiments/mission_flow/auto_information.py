"""Resumable, bounded hypothesis search. Never installs a production winner.
Run from the root of MetaRescue after Adaptive Survey is installed.
"""
import argparse, hashlib, json, math, random, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from experiments.mission_flow.benchmark_information import _run

SUITES=('physical','mixed','mixed_stress','variable_stress')
BASE='range-late'
INITIAL=('info-center','info-split','info-cover','info-split-dense','info-split-only','info-repeat-age','info-repeat-gain','info-repeat-rescue')

def write_json(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def fingerprints():
    paths=sorted(p for folder in ('controller','contracts','environment','experiments/mission_flow') for p in Path(folder).rglob('*.py'))
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def propose(history,incumbent,seed,count):
    """New bounded variants; mutations of duration/rescue weights, not new code."""
    used=set(history); rng=random.Random(seed)
    pool=[x for x in INITIAL if x not in used]
    # Draw new weight combinations near the current incumbent when possible.
    weights=[0,.25,.5,.75,1,1.25,1.5,2,2.5,3]
    options=[f'info-repeat-tune-{a:g}-{b:g}' for a in weights for b in weights if f'info-repeat-tune-{a:g}-{b:g}' not in used]
    rng.shuffle(options)
    if incumbent.startswith('info-repeat-tune-'):
        a,b=map(float,incumbent.split('-')[-2:]); options.sort(key=lambda x: sum(abs(float(v)-t) for v,t in zip(x.split('-')[-2:],(a,b))))
    return (pool+options)[:count]

def evaluate(pool,arms,base,count,path):
    # Each stage has disjoint seed blocks. All arms get the same initial world.
    if path.exists():
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    else:
        rows=[]; partial=path.with_suffix('.partial.jsonl')
        with partial.open('w',encoding='utf-8') as f:
            for k,suite in enumerate(SUITES):
                jobs=[(seed,'stress' in suite,'mixed' in suite or 'variable' in suite,'variable' in suite,arms) for seed in range(base+k*10000,base+k*10000+count)]
                for block in pool.map(_run,jobs):
                    for row in block:
                        row['suite']=suite; rows.append(row); f.write(json.dumps(row,ensure_ascii=False)+'\n')
                    f.flush()
        partial.replace(path)
    groups={a:{} for a in arms}
    for r in rows:
        key=(r['suite'],r['seed']); a=r['arm']
        if a not in groups or key in groups[a]:raise ValueError('Unexpected or duplicate result')
        groups[a][key]=r
    expected={(suite,seed) for k,suite in enumerate(SUITES) for seed in range(base+k*10000,base+k*10000+count)}
    for a,g in groups.items():
        if set(g)!=expected:raise ValueError('Incomplete series: '+a)
    for key in expected:
        if len({g[key]['initial'] for g in groups.values()})!=1:raise ValueError('Unpaired inputs')
    return groups

def summarize(rows):
    return dict(n=len(rows),m1=statistics.mean(r['m1'] for r in rows),saved=sum(r['saved'] for r in rows),energy=statistics.mean(r['energy'] for r in rows),clipped=statistics.mean(r['clipped'] for r in rows),errors=sum(bool(r['error']) for r in rows),reserve=sum(r['reserve_violations'] for r in rows),rejections=sum(r['rejections'] for r in rows))

def compare(base,candidate,*,energy_tol=.0,suite_tol=.01,bootstrap=False,seed=0):
    keys=sorted(base); a=summarize([base[k] for k in keys]);b=summarize([candidate[k] for k in keys])
    delta=[candidate[k]['m1']-base[k]['m1'] for k in keys]
    by_suite={s:statistics.mean(candidate[k]['m1']-base[k]['m1'] for k in keys if k[0]==s) for s in SUITES}
    ci=None
    if bootstrap:
        rng=random.Random(seed); draws=sorted(statistics.mean(rng.choices(delta,k=len(delta))) for _ in range(2000));ci=[draws[50],draws[1949]]
    # Conservative acceptance. Resource and safety gates are not official M2-M7 scores.
    checks=dict(m1=b['m1']>a['m1']+1e-10,saved=b['saved']>=a['saved'],energy=b['energy']<=a['energy']*(1+energy_tol)+1e-12,time=b['clipped']<=a['clipped']+1e-10,errors=b['errors']<=a['errors'],reserve=b['reserve']<=a['reserve'],rejections=b['rejections']<=a['rejections'],suite=min(by_suite.values())>=-suite_tol,confidence=ci is None or ci[0]>0)
    # Screening score only, never substitutes for the gates.
    score=(b['m1']-a['m1'])-.15*max(0,(b['energy']-a['energy'])/max(1e-12,a['energy']))-.1*max(0,(b['clipped']-a['clipped'])/max(1e-12,a['clipped']))
    return dict(base=a,candidate=b,delta=statistics.mean(delta),by_suite=by_suite,ci=ci,checks=checks,passed=all(checks.values()),score=score)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--seed',type=int,default=20000000);p.add_argument('--rounds',type=int,default=40);p.add_argument('--patience',type=int,default=8);p.add_argument('--proposals',type=int,default=3);p.add_argument('--screen',type=int,default=24);p.add_argument('--validate',type=int,default=64);p.add_argument('--final',type=int,default=128);p.add_argument('--workers',type=int,default=4);p.add_argument('--energy-tol',type=float,default=0.);p.add_argument('--suite-tol',type=float,default=.01);args=p.parse_args()
    if not (1<=args.rounds<=1000 and 1<=args.patience<=args.rounds and 1<=args.proposals<=12 and all(1<=n<=5000 for n in (args.screen,args.validate,args.final)) and 1<=args.workers<=128 and 0<=args.energy_tol<=.05 and 0<=args.suite_tol<=.05 and 0<=args.seed<=2**32-1-200000000):p.error('Invalid campaign bounds')
    root=args.output;root.mkdir(parents=True,exist_ok=True);statefile=root/'state.json'
    config={k:v for k,v in vars(args).items() if k not in ('output','workers')};source=fingerprints()
    if statefile.exists():
        state=json.loads(statefile.read_text(encoding='utf-8'))
        if state['config']!=config or state['source']!=source:raise SystemExit('Config or sources changed: use a new output directory')
        if state.get('final_report') is not None:print(json.dumps(state['final_report'],ensure_ascii=False));return
    else:
        state=dict(config=config,source=source,round=0,incumbent=BASE,history=[],stale=0,rounds=[],final_report=None);write_json(statefile,state)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        while state['round']<args.rounds and state['stale']<args.patience:
            i=state['round']; incumbent=state['incumbent'];proposals=propose(state['history'],incumbent,args.seed+i,args.proposals)
            if not proposals:break
            offset=args.seed+i*100000
            arms=list(dict.fromkeys([incumbent,*proposals]));dev=evaluate(pool,arms,offset,args.screen,root/f'round_{i:03d}_screen.jsonl')
            screened={a:compare(dev[incumbent],dev[a],energy_tol=args.energy_tol,suite_tol=args.suite_tol) for a in proposals}
            # Validate at most the best screened candidate; new maps are never used to tune it.
            selected=max(proposals,key=lambda a:(screened[a]['passed'],screened[a]['score'],a));validation=None
            if screened[selected]['passed']:
                arms=list(dict.fromkeys([BASE,incumbent,selected]));val=evaluate(pool,arms,offset+50000,args.validate,root/f'round_{i:03d}_validation.jsonl')
                validation={ref:compare(val[ref],val[selected],energy_tol=args.energy_tol,suite_tol=args.suite_tol,bootstrap=True,seed=offset) for ref in dict.fromkeys([BASE,incumbent])}
            promoted=bool(validation and all(x['passed'] for x in validation.values()))
            state['rounds'].append(dict(round=i,incumbent=incumbent,screen=screened,selected=selected,validation=validation,promoted=promoted));state['history'].extend(proposals);state['round']+=1
            if promoted:state['incumbent']=selected;state['stale']=0
            else:state['stale']+=1
            write_json(statefile,state);print(f'round={i} selected={selected} promoted={promoted} incumbent={state["incumbent"]}',flush=True)
        finalist=state['incumbent'];report={'research_candidate':finalist,'rounds':state['round'],'stop':'patience' if state['stale']>=args.patience else 'budget_or_candidates_exhausted','production_changed':False}
        if finalist!=BASE:
            final=evaluate(pool,[BASE,finalist],args.seed+150000000,args.final,root/'final_unseen.jsonl');report['final']=compare(final[BASE],final[finalist],energy_tol=args.energy_tol,suite_tol=args.suite_tol,bootstrap=True,seed=args.seed);report['recommended_for_3d_validation']=finalist if report['final']['passed'] else BASE
        else:report['recommended_for_3d_validation']=BASE;report['final']=None
        state['final_report']=report;write_json(statefile,state);write_json(root/'final_report.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
