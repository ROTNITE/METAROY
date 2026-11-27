"""Explicitly launched loopback M5 laboratory, separate from read-only analytics.

Only built-in test engines and operator-configured loopback HTTP services are
launchable. Browser requests cannot supply executables, filesystem paths or
arbitrary URLs. One active series at a time; subprocesses own their trial.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
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
import tempfile
import zipfile
import threading
import time
from urllib.parse import urlsplit,parse_qs
import uuid

from contracts.protocol import strict_json
from controller.ai import atomic_json,file_lock
from controller.client import EnvironmentClient
from tools.run_interop import controller_call,ROOT
from tools.m5_score import aggregate_m5
from environment.main import DEFAULT_OUTPUT

WEB=ROOT/'web'/'m5'
MAX_REQUEST=16384
MAX_REPORT=16*1024*1024
MAX_ARCHIVE=256*1024*1024
MAX_ARCHIVE_FILES=2000
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
    result={}
    for k in ('exchange','status','goal_status','evacuated','located','aid_delivered',
              'correlated_public_target_count','planning_p95_ms','step_http_p95_ms','inputs_preserved','error'):
        value=report.get(k)
        if isinstance(value,str):
            result[k]=value[:1000]
        elif type(value) in (int,float,bool):
            result[k]=value
    error=report.get('first_protocol_error')
    if isinstance(error,dict):
        result['first_protocol_error']={k:str(error[k])[:500] for k in ('kind','code','stage','message') if k in error}
    truth=report.get('independent_truth')
    if isinstance(truth,dict):
        result['independent_truth']={k:truth[k] for k in ('profile','target_count','physical_handovers','all_evacuated','closed')
                                     if k in truth and (truth[k] is None or type(truth[k]) in (str,int,bool))}
    result['evidence_basis']=('independent_model_truth' if report.get('engine') in ('reference','independent-reference')
                              else 'project_model_truth') if isinstance(truth,dict) else 'public_only'
    return result


def csv_cell(value):
    # Prevent a foreign status/error from becoming a spreadsheet formula.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
        return "'"+value
    return value


def archive_series(root,run,stream):
    """Export a stable, bounded evidence bundle, excluding scratch ledgers."""
    if run['state'] in ACTIVE:
        raise ValueError('Wait until the series reaches a terminal process state')
    folder=(root/run['id']).resolve(strict=True)
    if not folder.is_relative_to(root):
        raise ValueError('Unsafe archive root')
    candidates=[folder/'series.json']
    for row in run['rows']:
        stem=f"trial-{row['index']:03d}"
        candidates.append(folder/(stem+'.log'))
        trial=folder/stem
        if trial.is_symlink():
            raise ValueError('Unsafe trial directory')
        if trial.is_dir():
            candidates.extend(trial.rglob('*'))
    files=[];total=0
    for path in candidates:
        if path.is_symlink():
            raise ValueError('Symlink in evidence archive')
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(folder):
            raise ValueError('Unsafe evidence path')
        if any(part.is_symlink() for part in path.parents if part!=folder and part.is_relative_to(folder)):
            raise ValueError('Symlinked evidence parent')
        size=path.stat().st_size
        total+=size
        files.append((path,size))
        if total>MAX_ARCHIVE or len(files)>MAX_ARCHIVE_FILES:
            raise ValueError('Evidence bundle exceeds export budget; use local files')
    entries=[]
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        for path,size in files:
            name=path.relative_to(folder).as_posix()
            hasher=hashlib.sha256();written=0
            with path.open('rb') as source,archive.open(name,'w') as destination:
                for block in iter(lambda:source.read(1024*1024),b''):
                    written+=len(block)
                    if written>size:
                        raise ValueError('Evidence changed during export')
                    hasher.update(block);destination.write(block)
            if written!=size:
                raise ValueError('Evidence changed during export')
            entries.append({'path':name,'sha256':hasher.hexdigest(),'bytes':written})
        archive.writestr('evidence-manifest.json',json.dumps({'format':'metarescue.m5-evidence.v1',
                         'series_id':run['id'],'entries':entries,'official_m5':None},ensure_ascii=False,indent=2))
    stream.seek(0)


def load_assessments(path):
    if path is None:
        return None
    if path.stat().st_size>MAX_REQUEST*16:
        raise ValueError('Assessment file exceeds size limit')
    document=strict_json(path.read_bytes(),maximum_bytes=MAX_REQUEST*16)
    if not isinstance(document,dict) or set(document)!={'controllers','environments','assessments','method_id'}:
        raise ValueError('Invalid external Q assessment document')
    if not isinstance(document['assessments'],list):
        raise ValueError('Expected list of pair assessments')
    return aggregate_m5(document['controllers'],document['environments'],
                        document['assessments'],method_id=document['method_id'])


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
                for row in saved['rows']:
                    if row.get('state') in ACTIVE:
                        row['state']='interrupted'
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

    def catalog(self,offset=0,limit=30):
        if not 0<=offset<=1000000 or not 1<=limit<=100:
            raise ValueError('Invalid catalog page')
        with self.lock:
            ordered=sorted(self.runs.values(),key=lambda r:r.get('created',''),reverse=True)
            rows=[]
            for run in ordered[offset:offset+limit]:
                spec=run.get('spec',{})
                results=run['rows']
                rows.append({'id':run['id'],'created':run.get('created'),'state':run['state'],
                             'spec':{k:spec.get(k) for k in ('controller','environment','mode')},
                             'trial_count':len(results),
                             'passed_count':sum(r.get('result',{}).get('exchange')=='passed' for r in results)})
            return {'series':rows,'total':len(ordered),'offset':offset,'limit':limit,'active_id':self.active}

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
        if not path.resolve().is_relative_to(self.root/ident):
            raise KeyError('Unsafe report path')
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
    def __init__(self,address,lab,assessment=None):
        self.lab=lab;self.assessment=assessment;self.token=secrets.token_urlsafe(32)
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    timeout=15
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
            parsed=urlsplit(self.path);path=parsed.path;lab=self.server.lab
            if path=='/api/session':
                self.send({'token':self.server.token,'registry':lab.registry.public(),'official_m5':None,'assessed_m5':self.server.assessment});return
            if path=='/api/series':
                query=parse_qs(parsed.query,keep_blank_values=True)
                if set(query)-{'offset','limit'} or any(len(v)!=1 for v in query.values()):
                    raise ValueError('Invalid catalog query')
                self.send(lab.catalog(int(query.get('offset',['0'])[0]),int(query.get('limit',['30'])[0])));return
            match=re.fullmatch(r'/api/series/([0-9a-f]{32})(?:/trial/(\d{1,3})|/export/(json|csv|zip))?',path)
            if match:
                ident,index,format=match.groups()
                if index is not None:
                    self.send(lab.report(ident,int(index)));return
                run=lab.get(ident)
                if format=='zip':
                    with tempfile.TemporaryFile() as stream:
                        archive_series(lab.root,run,stream)
                        stream.seek(0,2);size=stream.tell();stream.seek(0)
                        self.send_response(200);self.send_header('Content-Type','application/zip')
                        self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store')
                        self.send_header('Content-Disposition',f'attachment; filename="{ident}.zip"')
                        self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                        for block in iter(lambda:stream.read(1024*1024),b''):self.wfile.write(block)
                    return
                if format=='csv':
                    stream=io.StringIO(newline='');writer=csv.writer(stream)
                    writer.writerow(['seed','mode','state','exchange','mission_status','physical_handovers','public_transitions','wall_s'])
                    for row in run['rows']:
                        result=row.get('result',{});truth=result.get('independent_truth') or {}
                        writer.writerow([csv_cell(v) for v in (row['seed'],row['mode'],row['state'],result.get('exchange'),
                                         result.get('status'),truth.get('physical_handovers'),
                                         result.get('correlated_public_target_count'),row.get('wall_s'))])
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
            data=self.rfile.read(length)
            if len(data)!=length:
                raise ValueError('Incomplete body')
            body=strict_json(data,maximum_bytes=MAX_REQUEST)
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
    parser.add_argument('--q-assessments',type=Path,help='Operator-provided cross-team Q and evidence; never official jury certification')
    args=parser.parse_args()
    assessment=load_assessments(args.q_assessments)
    registry=Registry(args.environment,args.controller)
    with file_lock(args.output.resolve()/'laboratory-owner.lock'):
        lab=Lab(args.output,registry)
        with Server(('127.0.0.1',args.port),lab,assessment=assessment) as server:
            print(f'M5 laboratory: http://127.0.0.1:{server.server_port}',flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                lab.shutdown()


if __name__=='__main__':
    main()
