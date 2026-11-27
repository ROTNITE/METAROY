"""Explicitly launched loopback M5 laboratory, separate from read-only analytics.

Only built-in test engines and operator-configured loopback HTTP services are
launchable. Browser requests cannot supply executables, filesystem paths or
arbitrary URLs. One active series at a time; subprocesses own their trial.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from contracts.protocol import strict_json
from controller.ai import atomic_json
from controller.client import EnvironmentClient
from tools.run_interop import controller_call,ROOT
from environment.main import DEFAULT_OUTPUT

WEB=ROOT/'web'/'m5'
MAX_REQUEST=16384
MAX_REPORT=16*1024*1024
ID=re.compile(r'[a-zA-Z0-9_-]{1,48}\Z')
RUN_ID=re.compile(r'[0-9a-f]{32}\Z')
ACTIVE={'queued','running','stopping'}


def now():
    return datetime.now(timezone.utc).isoformat()


def endpoint(value):
    """CLI only: name=port; no DNS resolution, redirect or external URL."""
    name,sep,port=value.partition('=')
    if not sep or not ID.fullmatch(name) or not port.isdecimal() or not 1<=int(port)<=65535:
        raise ValueError('Use NAME=PORT for a trusted loopback service')
    return name,f'http://127.0.0.1:{int(port)}'


class Registry:
    def __init__(self,environments=(),controllers=()):
        self.environments={};self.controllers={}
        for values,target,reserved in ((environments,self.environments,{'planar','reference'}),
                                       (controllers,self.controllers,{'metarescue','independent'})):
            for value in values:
                key,origin=endpoint(value)
                if key in target or key in reserved:
                    raise ValueError('Duplicate or reserved component ID')
                target[key]=origin

    def public(self):
        return {'controllers':[{'id':'metarescue','name':'MetaRescue','kind':'builtin'},
                               {'id':'independent','name':'Независимый контроллер','kind':'reference'}]+
                              [{'id':k,'name':k,'kind':'external','origin':v} for k,v in self.controllers.items()],
                'environments':[{'id':'reference','name':'Независимая 3D-модель','kind':'reference'},
                                {'id':'planar','name':'MetaRescue 2D','kind':'builtin'}]+
                               [{'id':k,'name':k,'kind':'external','origin':v} for k,v in self.environments.items()]}

    def spec(self,raw):
        allowed={'controller','environment','profile','mode','seeds','timeout','decide_path','decide_profile','confirm_reset'}
        if not isinstance(raw,dict) or set(raw)-allowed or raw.get('confirm_reset') is not True:
            raise ValueError('Confirm episode creation/reset and use recognized fields')
        controller,environment=raw.get('controller'),raw.get('environment')
        if not isinstance(controller,str) or not isinstance(environment,str):
            raise ValueError('Choose controller and environment')
        if controller not in ('metarescue','independent') and controller not in self.controllers:
            raise ValueError('Unknown controller')
        if environment not in ('planar','reference') and environment not in self.environments:
            raise ValueError('Unknown environment')
        if environment in self.environments and controller!='metarescue':
            raise ValueError('External environment requires the MetaRescue controller in this lab version')
        profile=raw.get('profile','K');mode=raw.get('mode','strict')
        if profile not in ('A','K') or mode not in ('strict','declared','both'):
            raise ValueError('Invalid profile or mode')
        if controller=='independent' and mode!='strict':
            raise ValueError('Independent controller supports strict mode only')
        seeds=raw.get('seeds')
        if (not isinstance(seeds,list) or not 1<=len(seeds)<=10 or
                any(type(s) is not int or not 0<=s<=2**31-1 for s in seeds) or len(set(seeds))!=len(seeds)):
            raise ValueError('Choose 1–10 distinct integer seeds, from 0 to 2147483647')
        timeout=raw.get('timeout',90)
        if type(timeout) is not int or not 5<=timeout<=300:
            raise ValueError('Timeout must be 5–300 seconds per trial')
        path=raw.get('decide_path','decide');rpc=raw.get('decide_profile','core')
        if path not in ('decide','decision') or rpc not in ('core','metaroi-v9'):
            raise ValueError('Invalid RPC profile')
        return {'controller':controller,'environment':environment,'profile':profile,'mode':mode,
                'seeds':seeds[:],'timeout':timeout,'decide_path':path,'decide_profile':rpc,
                'controller_origin':self.controllers.get(controller),'environment_origin':self.environments.get(environment),
                'mission_id':'search_and_rescue','ai_mode':'OFF'}


def build_command(spec,seed,mode,output):
    prefix=[sys.executable,'-B','-X','utf8','-m']
    common=['--seed',str(seed),'--core-mode',mode,'--timeout',str(spec['timeout']),'--output',str(output)]
    if spec['environment_origin']:
        return prefix+['tools.run_interop','our-controller','--environment',spec['environment_origin']]+common
    if spec['controller_origin']:
        return prefix+['tools.run_interop','our-environment','--controller',spec['controller_origin'],
                       '--engine',spec['environment'],'--mode','decide','--decide-path',spec['decide_path'],
                       '--decide-profile',spec['decide_profile'],'--reference-profile',spec['profile'],
                       '--environment-port','0','--decision-timeout','10']+common
    return prefix+['tools.m5_trial','--controller',spec['controller'],'--engine',spec['environment'],
                   '--profile',spec['profile']]+common


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name=='nt':
        # Terminate only the owned child tree, never registered services.
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,timeout=10,check=False)
    else:
        try:
            os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name!='nt':
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)


def read_report(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size>MAX_REPORT:
        return {}
    try:
        result=strict_json(path.read_bytes(),maximum_bytes=MAX_REPORT)
        return result if isinstance(result,dict) else {}
    except (ValueError,OSError):
        return {}


def summarize(report):
    result={k:report[k] for k in ('exchange','status','goal_status','evacuated','located','aid_delivered',
                                 'correlated_public_target_count','planning_p95_ms','step_http_p95_ms',
                                 'inputs_preserved','error','first_protocol_error','independent_truth')
            if k in report}
    result['evidence_basis']='independent_model_truth' if isinstance(report.get('independent_truth'),dict) else 'public_only'
    return result


class Lab:
    def __init__(self,root,registry):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.registry=registry;self.lock=threading.RLock();self.runs={}
        self.cancelled=set();self.active=None;self.worker=None
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir() or not RUN_ID.fullmatch(path.name):
                continue
            saved=read_report(path/'series.json')
            if saved.get('id')!=path.name or not isinstance(saved.get('rows'),list):
                continue
            if saved.get('state') in ACTIVE:
                saved.update(state='interrupted',ended=now(),note='Previous laboratory process ended; external episode state unknown.')
                atomic_json(path/'series.json',saved)
            self.runs[path.name]=saved

    def persist(self,run):
        atomic_json(self.root/run['id']/'series.json',run)

    def start(self,raw):
        spec=self.registry.spec(raw)
        with self.lock:
            if self.active is not None:
                raise RuntimeError('Another series is active')
            ident=uuid.uuid4().hex;folder=self.root/ident;folder.mkdir()
            modes=('strict','declared') if spec['mode']=='both' else (spec['mode'],)
            rows=[{'index':i,'seed':seed,'mode':mode,'state':'queued'}
                  for i,(seed,mode) in enumerate((s,m) for s in spec['seeds'] for m in modes)]
            run={'id':ident,'created':now(),'state':'queued','spec':spec,'rows':rows,
                 'official_m5':None,'note':'Diagnostic trials; no agreed external Q score.'}
            self.persist(run);self.runs[ident]=run;self.active=ident
            self.worker=threading.Thread(target=self.execute,args=(ident,),daemon=True)
            self.worker.start()
            return deepcopy(run)

    def cancel(self,ident):
        with self.lock:
            if self.active!=ident:
                raise ValueError('Series is not active')
            self.cancelled.add(ident)
            run=self.runs[ident];run['state']='stopping';self.persist(run)
            return deepcopy(run)

    def execute(self,ident):
        folder=self.root/ident;run=self.runs[ident];spec=run['spec']
        child=None
        try:
            with self.lock:
                if ident not in self.cancelled:
                    run['state']='running';self.persist(run)
            for row in run['rows']:
                with self.lock:
                    if ident in self.cancelled:
                        break
                    row.update(state='running',started=now());self.persist(run)
                output=folder/f"trial-{row['index']:03d}"
                # OFF-only scratch accounting; never changes the LIVE ledger.
                env=dict(os.environ,METARESCUE_OUTPUT=str(folder/'scratch'),
                         METARESCUE_LIVE_BUDGET=str(folder/'scratch'/'offline-budget.json'),PYTHONUTF8='1')
                (folder/'scratch').mkdir(exist_ok=True)
                begin=time.monotonic();state='completed'
                argv=build_command(spec,row['seed'],row['mode'],output)
                with (folder/f"trial-{row['index']:03d}.log").open('xb') as log:
                    child=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                                           start_new_session=os.name!='nt',
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
                    while child.poll() is None:
                        with self.lock:
                            abort=ident in self.cancelled
                        if abort or time.monotonic()-begin>spec['timeout']+35:
                            state='cancelled' if abort else 'timed_out'
                            stop_child(child);break
                        time.sleep(.1)
                result=read_report(output/'report.json')
                if state=='completed' and (not result or child.returncode!=0 or result.get('exchange')!='passed'
                                           or result.get('inputs_preserved') is not True):
                    state='failed'
                with self.lock:
                    row.update(state=state,ended=now(),wall_s=round(time.monotonic()-begin,3),
                               exit_code=child.returncode,result=summarize(result))
                    if state in ('cancelled','timed_out'):
                        row['note']='Process stopped; remote episode outcome is not guaranteed.'
                    self.persist(run)
                child=None
        except Exception as error:
            with self.lock:
                run['error']=f'{type(error).__name__}: {error}'
                for row in run['rows']:
                    if row['state']=='running':
                        row.update(state='failed',ended=now())
        finally:
            if child is not None:
                try:
                    stop_child(child)
                except (OSError,subprocess.SubprocessError):
                    pass
            with self.lock:
                for row in run['rows']:
                    if row['state']=='queued':
                        row['state']='cancelled' if ident in self.cancelled else 'interrupted'
                run['state']='cancelled' if ident in self.cancelled else (
                    'completed' if all(r['state']=='completed' for r in run['rows']) else 'failed')
                run['ended']=now();self.cancelled.discard(ident);self.active=None
                self.persist(run)

    def list(self):
        with self.lock:
            return deepcopy(sorted(self.runs.values(),key=lambda r:r.get('created',''),reverse=True)[:100])

    def get(self,ident):
        if not RUN_ID.fullmatch(ident):
            raise KeyError('Unknown series')
        with self.lock:
            return deepcopy(self.runs[ident])

    def report(self,ident,index):
        run=self.get(ident)
        if not 0<=index<len(run['rows']):
            raise KeyError('Unknown trial')
        path=self.root/ident/f'trial-{index:03d}'/'report.json'
        result=read_report(path)
        if not result:
            raise KeyError('Trial report is unavailable')
        return result

    def shutdown(self):
        with self.lock:
            if self.active:
                self.cancelled.add(self.active)
            worker=self.worker
        if worker:
            worker.join(timeout=15)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,lab):
        self.lab=lab;self.token=secrets.token_urlsafe(32)
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def send(self,value,status=200,mime='application/json; charset=utf-8',name=None):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8') if not isinstance(value,bytes) else value
        self.send_response(status);self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if name:
            self.send_header('Content-Disposition',f'attachment; filename="{name}"')
        self.end_headers();self.wfile.write(data)

    def guard(self,post=False):
        port=self.server.server_port
        host=self.headers.get('Host','')
        if host not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            raise PermissionError('Loopback host required')
        if post and (self.headers.get('Origin')!='http://'+host or
                     not secrets.compare_digest(self.headers.get('X-M5-Token',''),self.server.token)):
            raise PermissionError('Local origin and session token required')

    def do_GET(self):
        try:
            self.guard()
            path=urlsplit(self.path).path;lab=self.server.lab
            if path=='/api/session':
                self.send({'token':self.server.token,'registry':lab.registry.public(),'official_m5':None});return
            if path=='/api/series':
                self.send({'series':lab.list()});return
            match=re.fullmatch(r'/api/series/([0-9a-f]{32})(?:/trial/(\d{1,3})|/export/(json|csv))?',path)
            if match:
                ident,index,format=match.groups()
                if index is not None:
                    self.send(lab.report(ident,int(index)));return
                run=lab.get(ident)
                if format=='csv':
                    stream=io.StringIO(newline='');writer=csv.writer(stream)
                    writer.writerow(['seed','mode','state','exchange','mission_status','physical_handovers','public_transitions','wall_s'])
                    for row in run['rows']:
                        result=row.get('result',{});truth=result.get('independent_truth') or {}
                        writer.writerow([row['seed'],row['mode'],row['state'],result.get('exchange'),
                                         result.get('status'),truth.get('physical_handovers'),
                                         result.get('correlated_public_target_count'),row.get('wall_s')])
                    self.send(stream.getvalue().encode('utf-8-sig'),mime='text/csv; charset=utf-8',name=ident+'.csv');return
                self.send(run,name=ident+'.json' if format=='json' else None);return
            static={'/':('index.html','text/html; charset=utf-8'),'/lab.js':('lab.js','text/javascript; charset=utf-8'),
                    '/lab.css':('lab.css','text/css; charset=utf-8')}
            if path not in static:
                raise KeyError('Unknown route')
            filename,mime=static[path];self.send((WEB/filename).read_bytes(),mime=mime)
        except PermissionError as error:self.send({'error':str(error)},403)
        except (KeyError,FileNotFoundError):self.send({'error':'Not found'},404)
        except (OSError,ValueError,TypeError) as error:self.send({'error':str(error)},400)

    def do_POST(self):
        try:
            self.guard(post=True)
            if self.headers.get_content_type()!='application/json':
                raise ValueError('Expected application/json')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_REQUEST:
                raise ValueError('Invalid body length')
            body=strict_json(self.rfile.read(length),maximum_bytes=MAX_REQUEST)
            if not isinstance(body,dict):
                raise ValueError('Expected JSON object')
            path=urlsplit(self.path).path;lab=self.server.lab
            if path=='/api/series':
                self.send(lab.start(body),202);return
            if path=='/api/probe':
                if set(body)!={'role','id'} or body['role'] not in ('controller','environment') or not isinstance(body['id'],str):
                    raise ValueError('Choose a registered component')
                role=body['role'];key=body['id']
                registry=lab.registry.controllers if role=='controller' else lab.registry.environments
                if key not in registry:
                    # No pretend health response for an engine that has not been started.
                    if key not in (('metarescue','independent') if role=='controller' else ('reference','planar')):
                        raise ValueError('Unknown component')
                    self.send({'state':'launch_on_trial','message':'Built-in service starts only during a trial'});return
                origin=registry[key]
                if role=='controller':
                    health=controller_call(origin,'health',timeout=3)
                    self.send({'state':'responded','health':health});return
                client=EnvironmentClient(origin,timeout_s=3)
                try:
                    self.send({'state':'responded','health':client.health(),'capabilities':client.capabilities()})
                finally:
                    client.disconnect()
                return
            match=re.fullmatch(r'/api/series/([0-9a-f]{32})/cancel',path)
            if match and not body:
                self.send(lab.cancel(match.group(1)),202);return
            raise KeyError('Unknown route')
        except PermissionError as error:self.send({'error':str(error)},403)
        except KeyError:self.send({'error':'Not found'},404)
        except RuntimeError as error:self.send({'error':str(error)},409)
        except (OSError,ValueError,TypeError) as error:self.send({'error':str(error)},400)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT/'m5-lab')
    parser.add_argument('--environment',action='append',default=[],metavar='NAME=PORT')
    parser.add_argument('--controller',action='append',default=[],metavar='NAME=PORT')
    args=parser.parse_args()
    registry=Registry(args.environment,args.controller)
    lab=Lab(args.output,registry)
    with Server(('127.0.0.1',args.port),lab) as server:
        print(f'M5 laboratory: http://127.0.0.1:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            lab.shutdown()


if __name__=='__main__':
    main()
