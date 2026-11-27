"""One real M5 laboratory trial; no web UI or scoring dependencies."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread

from contracts.protocol import strict_json
from controller.ai import atomic_json
from conformance.reference_environment import serve
from environment.server import PlanarServer
from tools.run_interop import independent_truth,run_controller,ROOT
from tools.m5_spec import checked_mission_parameters


def freeze_sources():
    paths=[p for name in ('contracts','controller','environment','conformance')
           for p in (ROOT/name).rglob('*.py')]
    paths += [ROOT/'tools'/name for name in ('m5_trial.py','run_interop.py','goal_evidence.py','m5_spec.py')]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


@contextmanager
def local_environment(engine,profile,output):
    context=serve(profile,0,output/'truth.json') if engine=='reference' else PlanarServer(0,estimates=False)
    with context as server:
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            yield server,f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown();worker.join()


def truth(engine,server):
    if engine=='reference':
        return independent_truth(server)
    episode=server.episode
    if episode is None:
        return {'target_count':None,'physical_handovers':None,'all_evacuated':False,'closed':False}
    count=len(episode.people)
    transfers=sum(p.custody==episode.base for p in episode.people.values())
    return {'target_count':count,'physical_handovers':transfers,
            'all_evacuated':count>0 and transfers==count,'closed':episode.closed}


def trial(args):
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    parameter_file=getattr(args,'mission_parameters',None)
    def inputs():
        values=freeze_sources()
        if parameter_file is not None:
            values['mission_parameters_file']=hashlib.sha256(parameter_file.read_bytes()).hexdigest()
        return values
    frozen=inputs()
    report={'engine':args.engine,'controller':args.controller,'profile':args.profile,
            'seed':args.seed,'core_mode':args.core_mode,'input_sha256':frozen}
    try:
        params=checked_mission_parameters(args.engine,args.controller,
                    strict_json(parameter_file.read_bytes(),maximum_bytes=16384) if parameter_file else {})
        mission={'id':'search_and_rescue','parameters':params}
        report['mission']=mission
        if args.controller=='independent' and args.core_mode!='strict':
            raise ValueError('The independent controller exposes strict only')
        with local_environment(args.engine,args.profile,output) as (server,origin):
            if args.controller=='metarescue':
                result=run_controller(origin,output/'run',args.seed,'net65',
                                      mission,
                                      args.timeout,compatibility=args.core_mode)
                report.update(result)
                report['controller']='metarescue'
                report['engine']=args.engine
            else:
                argv=[sys.executable,'-B','-X','utf8','-m','conformance.reference_controller',
                      '--environment',origin,'--health-port','0','--seed',str(args.seed),
                      '--output',str(output/'reference.jsonl')]
                with (output/'controller.log').open('xb') as log:
                    done=subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                                        timeout=args.timeout,check=False)
                report['exit_code']=done.returncode
                if done.returncode:
                    raise RuntimeError('Independent controller failed; see controller.log')
                result=json.loads((output/'controller.log').read_text(encoding='utf-8').splitlines()[-1])
                report.update(status=result.get('status'),metrics=result.get('metrics',{}),
                              exchange='passed' if result.get('closed') is True else 'failed',
                              episode_id=result.get('episode'))
            report['independent_truth']=truth(args.engine,server)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        report.update(exchange='failed',error=f'{type(error).__name__}: {error}')
    finally:
        report['inputs_preserved']=frozen==inputs()
        atomic_json(output/'report.json',report)
    return 0 if report.get('exchange')=='passed' and report['inputs_preserved'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller',choices=('metarescue','independent'),required=True)
    parser.add_argument('--engine',choices=('planar','reference'),required=True)
    parser.add_argument('--profile',choices=('A','K'),default='K')
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--core-mode',choices=('strict','declared'),default='strict')
    parser.add_argument('--timeout',type=int,default=90)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mission-parameters',type=Path)
    args=parser.parse_args()
    if not 0<=args.seed<=2**31-1 or not 1<=args.timeout<=300:
        parser.error('Invalid seed or timeout')
    return trial(args)


if __name__=='__main__':
    raise SystemExit(main())
