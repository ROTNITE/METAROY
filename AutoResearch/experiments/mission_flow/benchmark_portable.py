"""Run from repo root: python -m experiments.mission_flow.benchmark_portable physical 53000 192."""
import sys,random,time,json,math,hashlib
from controller.portable import PortablePolicy
from contracts.protocol import encode, ProtocolError
from environment.world import World,Agent,Person,capabilities,HORIZON

class StressWorld(World):
 def reachable(self, identifier):
  # Public radio blackout for a subset of carriers, without changing their motion.
  return super().reachable(identifier) and not (identifier in ('r/0','r/2') and 35<=self.time<95)

def physical(seed,arm,stress=False):
 r=random.Random(seed);case=['normal','link_loss','blocked_route'][seed%3];w=(StressWorld if stress else World)(seed,case,opaque=bool(seed%2));w.people.clear();w.agents={w.scout:w.agents[w.scout]}
 # Same initial geometry, motion, energy and action execution in both arms.
 for i in range(r.randint(2,4)):
  x=r.uniform(2,27 if w.wall else 48);y=r.uniform(2,48)
  w.agents['r/'+str(i)]=Agent('ground',(x,y,0.),r.uniform(.7,1.8),r.uniform(.14,.28) if stress else 1.)
 for i in range(r.randint(8,15)):
  x=r.uniform(2,27 if w.wall else 48);y=r.uniform(2,48)
  w.people['p/'+str(i)]=Person((x,y,0.),0 if i%4 else (125 if stress else 80))
 w.visible={k:0 for k,p in w.people.items() if p.appears_at==0 and r.random()<.7}
 w.pose={k:a.position for k,a in w.agents.items()};w.energy={k:a.energy for k,a in w.agents.items()}
 initial_data=(w.pose,w.people,w.visible,[a.speed for a in w.agents.values()]);initial=hashlib.sha256(repr(initial_data+(w.energy,stress) if stress else initial_data).encode()).hexdigest()
 initial_energies=list(w.energy.values());caps=capabilities();policy=PortablePolicy(caps,'nsga2_nsga2',scout_patrol='net65',flow_dispatch=arm=='flow')
 seen={};calls=0;start=time.perf_counter();matches=0;err=None;decisions=[];reserve_violations=0;rejections=0;consecutive=0
 try:
  while w.status=='running':
   obs=w.observation(str(calls));t0=time.perf_counter();d=policy.decide(obs);decisions.append((time.perf_counter()-t0)*1000);calls+=1
   try:
    response=w.step(d,hashlib.sha256(encode(d)).hexdigest(),caps)
   except ProtocolError as ex:
    # Mirror controller.main: recoverable atomic refusals, at most three retries.
    if consecutive>=3 or ex.code not in ('INVALID_COMMAND','INVALID_SCENARIO','CONSTRAINT_VIOLATION','RESOURCE_EXHAUSTED'):
     raise
    rejections+=1;consecutive+=1;policy.reject(w.time,ex.code);policy.drain_traces();continue
   consecutive=0;policy.observe_response(response)
   matches+=sum(t.get('kind')=='flow_wave' for t in policy.drain_traces());reserve_violations+=sum(a.energy<.08-1e-8 for a in w.agents.values())
   for k,p in w.people.items():
    if p.custody==w.base:seen.setdefault(k,w.time)
 except Exception as e:err=repr(e)
 return dict(seed=seed,arm=arm,case=case,initial=initial,n=len(w.people),saved=len(seen),m1=len(seen)/len(w.people),energy=sum(initial_energy-a.energy for initial_energy,a in zip(initial_energies,w.agents.values())),time=w.time,clipped=sum(seen.get(k,HORIZON) for k in w.people)/len(w.people),found=len(w.visible),calls=calls,wall=time.perf_counter()-start,matches=matches,error=err,status=w.status,stress=stress,reserve_violations=reserve_violations,rejections=rejections,p95_decision_ms=sorted(decisions)[int(.95*(len(decisions)-1))] if decisions else None)
def _run(job):
 seed, stress=job
 return [physical(seed,a,stress) for a in ('heuristic','flow')]

if __name__=='__main__':
 import argparse
 from concurrent.futures import ProcessPoolExecutor
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('suite',choices=('physical','stress'))
 parser.add_argument('start',type=int);parser.add_argument('count',type=int)
 parser.add_argument('--workers',type=int,default=4)
 args=parser.parse_args()
 with ProcessPoolExecutor(max_workers=args.workers) as pool:
  for rows in pool.map(_run,[(seed,args.suite=='stress') for seed in range(args.start,args.start+args.count)]):
   for row in rows:print(json.dumps(row),flush=True)
