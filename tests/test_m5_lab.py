from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event,Lock,Thread
from unittest import TestCase
from unittest.mock import patch
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import io
import zipfile
import json
import time
import socket

from tools.m5_lab import Registry,Lab,Server,Handler,archive_series,build_command,endpoint,load_assessments,csv_cell,read_report
from tools.m5_matrix import build_matrix,pair_grid


class RegistryTests(TestCase):
    def spec(self,**changes):
        return {'controller':'metarescue','environment':'reference','profile':'K',
                'mode':'strict','seeds':[89725161],'timeout':30,'confirm_reset':True,**changes}

    def test_allowlist_and_commands(self):
        registry=Registry(['lab=8145'],['peer=8146'])
        out=registry.spec(self.spec(environment='lab'))
        self.assertIn('http://127.0.0.1:8145',build_command(out,7,'strict',Path('output')))
        inbound=registry.spec(self.spec(controller='peer',environment='planar'))
        self.assertIn('http://127.0.0.1:8146',build_command(inbound,7,'strict',Path('output')))
        self.assertIn('our-environment',build_command(inbound,7,'strict',Path('output')))
        for changes in ({'controller':'unknown'},{'environment':'http://example.invalid'},
                        {'environment':'lab','controller':'peer'},{'seeds':[1,1]},
                        {'seeds':[True]},{'seeds':[]},{'mode':'unsafe'},
                        {'controller':'independent','mode':'declared'},{'confirm_reset':False},
                        {'shell':'arbitrary'},{'timeout':301}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                registry.spec(self.spec(**changes))
        for value in ('host=http://127.0.0.1','host=0','../host=80','host=65536'):
            with self.assertRaises(ValueError):
                endpoint(value)

    def test_checked_planar_parameters_and_rpc_timeout(self):
        registry=Registry(controllers=['rpc=8146'])
        raw=self.spec(environment='planar',mission_parameters={'case':'late_target','opaque':True})
        spec=registry.spec(raw)
        self.assertEqual(spec['mission_parameters'],{'case':'late_target','opaque':True})
        argv=build_command(spec,7,'strict',Path('series/trial-000'))
        self.assertEqual(argv[argv.index('--mission-parameters')+1],str(Path('series/mission-parameters.json')))
        from contracts.protocol import encode
        self.assertEqual(argv[argv.index('--mission-parameters-sha256')+1],
                         hashlib.sha256(encode(spec['mission_parameters'])).hexdigest())
        default=registry.spec(self.spec(environment='planar',mission_parameters={'case':'normal','opaque':False}))
        self.assertEqual(default['mission_parameters'],{})
        inbound=registry.spec(self.spec(environment='planar',controller='rpc',decision_timeout=3,
                           mission_parameters={'case':'link_loss'}))
        argv=build_command(inbound,7,'strict',Path('series/trial-000'))
        self.assertEqual(argv[argv.index('--decision-timeout')+1],'3')
        for change in ({'mission_parameters':{'case':'bad'}},{'mission_parameters':{'opaque':1}},
                       {'mission_parameters':{'unknown':True}},{'mission_parameters':[]},
                       {'controller':'independent'},{'environment':'reference'},
                       {'decision_timeout':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                registry.spec(dict(raw,**change))

    def test_invalid_or_lost_trial_parameters_leave_failure_report(self):
        from argparse import Namespace
        from tools.m5_trial import trial

        with TemporaryDirectory() as temp:
            root=Path(temp)
            params=root/'parameters.json'
            for name,data,preserved in (
                    ('missing',None,False),
                    ('oversized',b' '*16385,False),
                    ('invalid',b'{"opaque":1}',True)):
                with self.subTest(case=name):
                    if params.exists():
                        params.unlink()
                    if data is not None:
                        params.write_bytes(data)
                    output=root/name
                    args=Namespace(output=output,engine='planar',controller='metarescue',
                                   profile='K',seed=7,core_mode='strict',timeout=10,
                                   mission_parameters=params)
                    with patch('tools.m5_trial.local_environment') as start:
                        self.assertEqual(trial(args),1)
                        start.assert_not_called()
                    report=json.loads((output/'report.json').read_text(encoding='utf-8'))
                    self.assertEqual(report['exchange'],'failed')
                    self.assertEqual(report['inputs_preserved'],preserved)
                    self.assertTrue(report['error'])
            params.write_bytes(b'{}')
            args.output=root/'lost'
            def lost(*args,**kwargs):
                params.unlink()
                raise RuntimeError('Test: parameters lost after freezing')
            with patch('tools.m5_trial.local_environment',side_effect=lost):
                self.assertEqual(trial(args),1)
            report=json.loads((args.output/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['exchange'],'failed')
            self.assertFalse(report['inputs_preserved'])
            self.assertIn('input_verification_error',report)

    def test_changed_approved_parameter_bytes_are_rejected_by_both_runners(self):
        from argparse import Namespace
        from tools.m5_trial import trial
        from tools.run_interop import main as interop_main

        with TemporaryDirectory() as temp:
            root=Path(temp)
            params=root/'parameters.json'
            approved=hashlib.sha256(b'{"case":"late_target"}').hexdigest()
            params.write_bytes(b'{"case":"link_loss"}')
            args=Namespace(output=root/'builtin',engine='planar',controller='metarescue',
                           profile='K',seed=7,core_mode='strict',timeout=10,
                           mission_parameters=params,mission_parameters_sha256=approved)
            with patch('tools.m5_trial.local_environment') as start:
                self.assertEqual(trial(args),1)
                start.assert_not_called()
            argv=['run_interop','our-environment','--controller','http://127.0.0.1:8146',
                  '--engine','planar','--mode','decide','--mission-parameters',str(params),
                  '--mission-parameters-sha256',approved,'--output',str(root/'rpc')]
            with patch('sys.argv',argv),patch('tools.run_interop.planar') as start:
                self.assertEqual(interop_main(),1)
                start.assert_not_called()
            for name in ('builtin','rpc'):
                report=json.loads((root/name/'report.json').read_text(encoding='utf-8'))
                self.assertEqual(report['exchange'],'failed')
                self.assertFalse(report['inputs_preserved'])
                self.assertIn('approved SHA-256',report['error'])

    def test_historical_interruption_does_not_restart(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);ident='a'*32;folder=root/ident;folder.mkdir()
            (folder/'series.json').write_text(json.dumps({'id':ident,'state':'running','rows':[]}))
            lab=Lab(root,Registry())
            self.assertEqual(lab.get(ident)['state'],'interrupted')
            self.assertIsNone(lab.active)


class HTTPBoundary(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.lab=Lab(Path(self.temp.name),Registry())
        self.server=Server(('127.0.0.1',0),self.lab)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.host=f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.lab.shutdown()
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.temp.cleanup()

    def call(self,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        default={'Host':self.host}
        if body is not None:
            default.update({'Content-Type':'application/json','Origin':'http://'+self.host,
                            'X-M5-Token':self.server.token})
        try:
            conn.request('GET' if body is None else 'POST',path,
                         body=json.dumps(body).encode() if body is not None else None,
                         headers={**default,**(headers or {})})
            response=conn.getresponse();data=response.read()
            return response.status,data
        finally:
            conn.close()

    def test_duplicate_listener_and_ambiguous_http_headers_are_rejected(self):
        with self.assertRaises(OSError):
            Server(('127.0.0.1',self.server.server_port),self.lab)
        with self.assertRaises(ValueError):
            Server(('0.0.0.0',0),self.lab)
        def raw(headers):
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
            try:
                conn.putrequest('POST','/api/probe',skip_host=True)
                for key,value in headers:
                    conn.putheader(key,value)
                # Prove the server rejects malformed headers without waiting
                # for a body. Windows can abort the socket when a rejected
                # request already has unread payload bytes in its buffer.
                conn.endheaders()
                response=conn.getresponse();response.read();return response.status
            finally:conn.close()
        base=[('Host',self.host),('Origin','http://'+self.host),
              ('X-M5-Token',self.server.token),('Content-Type','application/json')]
        self.assertEqual(raw(base+[('Content-Length','2'),('Content-Length','2')]),400)
        self.assertEqual(raw(base+[('Content-Length','2'),('Transfer-Encoding','chunked')]),400)
        self.assertEqual(raw(base+[('Content-Length','2'),('Host',self.host)]),403)
        self.assertEqual(raw(base+[('Content-Length','2'),('X-M5-Token',self.server.token)]),403)
        self.assertEqual(self.call('/api/series?'+'&'.join('x=1' for _ in range(17)))[0],400)
        self.assertEqual(self.lab.list(),[])

    def test_zip_export_limit_preserves_lightweight_gets_and_releases(self):
        ident='b'*32
        run={'id':ident,'created':'2026-01-01','state':'completed','rows':[],'spec':{}}
        folder=self.lab.root/ident;folder.mkdir()
        self.lab.runs[ident]=run;self.lab.persist(run)
        entered=Event();release=Event();results=[]
        def delayed(root,value,stream):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test export did not receive release')
            archive_series(root,value,stream)
        route='/api/series/'+ident+'/export/zip'
        with patch('tools.m5_lab.archive_series',side_effect=delayed):
            thread=Thread(target=lambda:results.append(self.call(route)),daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.call(route)[0],503)
                self.assertEqual(self.call('/api/series')[0],200)
                self.assertEqual(self.call('/api/matrix')[0],200)
            finally:
                release.set();thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0],200)
        self.assertEqual(self.call(route)[0],200)
        with patch('tools.m5_lab.archive_series',side_effect=ValueError('bad archive')):
            self.assertEqual(self.call(route)[0],400)
        self.assertEqual(self.call(route)[0],200)

    def test_worker_limit_rejects_excess_and_recovers(self):
        entered=Event();release=Event();lock=Lock();count=0;results=[]
        original=Handler.do_GET
        def delayed(handler):
            nonlocal count
            if handler.path=='/blocked':
                with lock:
                    count+=1
                    if count==2:entered.set()
                if not release.wait(5):
                    handler.send({'error':'test timeout'},500);return
                handler.send({'ok':True});return
            original(handler)
        with patch('tools.m5_lab.MAX_HTTP_WORKERS',2),patch.object(Handler,'do_GET',delayed):
            with Server(('127.0.0.1',0),self.lab) as limited:
                server_thread=Thread(target=limited.serve_forever,daemon=True);server_thread.start()
                def call(path):
                    conn=http.client.HTTPConnection('127.0.0.1',limited.server_port,timeout=5)
                    try:
                        conn.request('GET',path)
                        response=conn.getresponse();response.read();return response.status
                    finally:conn.close()
                workers=[Thread(target=lambda:results.append(call('/blocked')),daemon=True) for _ in range(2)]
                for worker in workers:worker.start()
                try:
                    self.assertTrue(entered.wait(5))
                    # TCP overload can close before the whole request arrives.
                    # Either explicit 503 or a closed socket is fail-closed.
                    try:status=call('/api/session')
                    except (OSError,http.client.HTTPException):status=503
                    self.assertEqual(status,503)
                    self.assertEqual(count,2)
                    release.set()
                    for worker in workers:worker.join(5)
                    self.assertEqual(results,[200,200])
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline:
                        try:
                            if call('/api/session')==200:break
                        except (OSError,http.client.HTTPException):pass
                        time.sleep(.02)
                    else:self.fail('HTTP capacity did not recover')
                finally:
                    release.set()
                    for worker in workers:worker.join(5)
                    limited.shutdown();server_thread.join(5)

    def test_security_and_probe_is_read_only(self):
        self.assertEqual(self.call('/api/session',headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(self.call('/api/probe',{'role':'controller','id':'metarescue'},
                                   {'Origin':'http://evil.invalid'})[0],403)
        self.assertEqual(self.call('/api/probe',{'role':'controller','id':'metarescue'},
                                   {'X-M5-Token':'wrong'})[0],403)
        code,raw=self.call('/api/probe',{'role':'environment','id':'reference'})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['state'],'launch_on_trial')
        self.assertEqual(self.call('/api/probe',{'role':'environment','id':'http://example.invalid'})[0],400)
        self.assertEqual(self.lab.list(),[])
        self.assertEqual(self.call('/../../requirements.txt')[0],404)
        self.assertEqual(self.call('/api/series',{'confirm_reset':False})[0],400)
        self.assertEqual(self.call('/')[0],200)
        self.assertEqual(self.call('/api/matrix?mode=bad')[0],400)
        self.assertEqual(self.call('/api/matrix?limit=101')[0],400)
        self.assertEqual(self.call('/api/pairs?limit=13')[0],400)
        self.assertEqual(self.call('/api/pairs',headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(self.call('/api/matrix?controller=')[0],400)
        empty=json.loads(self.call('/api/pairs')[1]);self.assertEqual(empty['pairs'],[])
        self.assertIn('metarescue',empty['controllers']);self.assertIn('reference',empty['environments'])
        self.assertEqual(self.call('/api/matrix',headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(json.loads(self.call('/api/matrix')[1])['cells'],[])
        self.assertEqual(self.lab.list(),[])

    def test_real_planar_parameterized_mission_is_frozen_and_reported(self):
        request={'controller':'metarescue','environment':'planar','profile':'K','mode':'strict',
                 'seeds':[17333],'timeout':40,'confirm_reset':True,
                 'mission_parameters':{'case':'late_target','opaque':True}}
        code,raw=self.call('/api/series',request)
        self.assertEqual(code,202,raw)
        ident=json.loads(raw)['id']
        deadline=time.monotonic()+70
        while time.monotonic()<deadline and self.lab.get(ident)['state'] in ('queued','running','stopping'):
            time.sleep(.1)
        run=self.lab.get(ident)
        self.assertEqual(run['state'],'completed',run)
        path=self.lab.root/ident/'mission-parameters.json'
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')),request['mission_parameters'])
        code,raw=self.call('/api/series/'+ident+'/trial/0')
        self.assertEqual(code,200)
        report=json.loads(raw)
        self.assertEqual(report['mission']['parameters'],request['mission_parameters'])
        self.assertTrue(report['inputs_preserved'])
        truth=report['independent_truth']
        self.assertEqual(truth['target_count'],2)
        # This is the minimal planar profile without motion estimates, not the
        # estimates-enabled NET65 benchmark. Test real inputs and honest truth,
        # without requiring the portable policy to solve every late target.
        self.assertIn(truth['physical_handovers'],(1,2))
        self.assertEqual(report['metrics']['confirmed_evacuations'],truth['physical_handovers'])
        self.assertEqual(truth['all_evacuated'],truth['physical_handovers']==2)
        self.assertEqual(report['status']=='success',truth['all_evacuated'])
        self.assertTrue(truth['closed'])
        code,raw=self.call('/api/series/'+ident+'/export/zip')
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(json.loads(archive.read('mission-parameters.json')),request['mission_parameters'])
        cells=json.loads(self.call('/api/matrix')[1])['cells']
        self.assertEqual(cells[0]['mission_parameters'],request['mission_parameters'])

    def test_trial_summary_does_not_read_full_large_report(self):
        ident='a'*32
        folder=self.lab.root/ident
        (folder/'trial-000').mkdir(parents=True)
        summary={'exchange':'passed','status':'success','inputs_preserved':True,
                 'evidence_basis':'independent_model_truth',
                 'independent_truth':{'physical_handovers':2,'target_count':2},
                 'first_protocol_error':{'code':'EXAMPLE','details':{'rule':'resource_id','private':'do not copy'}}}
        run={'id':ident,'created':'2026-01-01','state':'completed','rows':[{'index':0,'state':'completed',
             'seed':1,'mode':'strict','result':summary}],'spec':{}}
        self.lab.runs[ident]=run;self.lab.persist(run)
        path=folder/'trial-000'/'report.json'
        path.write_text(json.dumps({'extra':'x'*(6*1024*1024)}),encoding='utf-8')
        route='/api/series/'+ident+'/trial/0'
        with patch('tools.m5_lab.read_report',side_effect=AssertionError('summary read full report')):
            code,raw=self.call(route+'/summary')
            self.assertEqual(code,200,raw)
            self.assertLess(len(raw),4096)
            result=json.loads(raw)
            self.assertTrue(result['full_report_available'])
            self.assertEqual(result['evidence_basis'],'independent_model_truth')
            self.assertEqual(result['independent_truth']['physical_handovers'],2)
            self.assertEqual(result['first_protocol_error']['details'],{'rule':'resource_id'})
            self.assertIsNone(result['official_m5'])
            self.assertEqual(self.call(route+'/summary?unexpected=1')[0],400)
            self.assertEqual(self.call(route+'/summary',headers={'Host':'evil.invalid'})[0],403)
        path.unlink()
        self.assertFalse(json.loads(self.call(route+'/summary')[1])['full_report_available'])

    def test_real_bidirectional_series_and_exports(self):
        for controller,environment in (('metarescue','reference'),('independent','planar')):
            with self.subTest(controller=controller,environment=environment):
                request={'controller':controller,'environment':environment,'profile':'K','mode':'strict',
                         'seeds':[89725161],'timeout':30,'confirm_reset':True}
                code,data=self.call('/api/series',request)
                self.assertEqual(code,202,data)
                ident=json.loads(data)['id']
                self.assertEqual(self.call('/api/series',request)[0],409)
                deadline=time.monotonic()+65
                while time.monotonic()<deadline:
                    run=json.loads(self.call('/api/series/'+ident)[1])
                    if run['state'] not in ('queued','running','stopping'):
                        break
                    time.sleep(.15)
                self.assertEqual(run['state'],'completed',run)
                row=run['rows'][0]
                self.assertEqual(row['result']['exchange'],'passed')
                self.assertTrue(row['result']['inputs_preserved'])
                self.assertTrue(row['result']['independent_truth']['all_evacuated'])
                self.assertGreater(row['result']['independent_truth']['physical_handovers'],0)
                self.assertIsNone(run['official_m5'])
                matrix=json.loads(self.call('/api/matrix?mode=strict')[1])
                cell=next(c for c in matrix['cells'] if c['controller']==controller and c['environment']==environment)
                self.assertEqual((cell['attempts'],cell['exchange_passed'],cell['truth_trials'],cell['all_evacuated']),(1,1,1,1))
                self.assertIsNone(matrix['official_m5'])
                overview=json.loads(self.call('/api/pairs?mode=strict')[1])
                pair=next(p for p in overview['pairs'] if p['controller']==controller and p['environment']==environment)
                self.assertEqual((pair['configurations'],pair['exchange_passed'],pair['all_evacuated']),(1,1,1))
                self.assertIsNone(overview['official_m5'])
                filtered=json.loads(self.call('/api/matrix?controller='+controller+'&environment='+environment)[1])
                self.assertEqual(filtered['total'],1)
                self.assertEqual(filtered['cells'][0],cell)
                code,raw=self.call('/api/series/'+ident+'/trial/0')
                self.assertEqual(code,200)
                self.assertTrue(json.loads(raw)['independent_truth']['all_evacuated'])
                progress_path='/api/series/'+ident+'/trial/0/progress'
                code,raw=self.call(progress_path+'?limit=1')
                self.assertEqual(code,200)
                page=json.loads(raw);self.assertTrue(page['available']);self.assertEqual(len(page['rows']),1)
                self.assertEqual(page['rows'][0]['source'],'reference' if controller=='independent' else 'metarescue')
                self.assertTrue(page['more'])
                code,raw=self.call(progress_path+'?cursor='+str(page['cursor'])+'&limit=10')
                self.assertEqual(code,200);self.assertTrue(json.loads(raw)['rows'])
                first_id=page['rows'][0].get('episode_id') or page['rows'][0].get('request_id')
                self.assertIsNotNone(first_id)
                from urllib.parse import urlencode
                code,raw=self.call(progress_path+'?'+urlencode({'id':first_id,'limit':1}))
                self.assertEqual(code,200)
                matched=json.loads(raw)
                self.assertEqual(len(matched['rows']),1)
                self.assertIn(first_id,[matched['rows'][0].get(k) for k in ('episode_id','request_id','decision_id')])
                self.assertEqual(self.call(progress_path+'?id=')[0],400)
                self.assertEqual(self.call(progress_path+'?id=x&id=y')[0],400)
                self.assertEqual(self.call(progress_path+'?limit=101')[0],400)
                self.assertEqual(self.call(progress_path+'?cursor=1')[0],400)
                self.assertEqual(self.call(progress_path,headers={'Host':'evil.invalid'})[0],403)
                trace_path=progress_path.replace('/progress','/trace')
                cursor=0;types=set()
                for _ in range(100):
                    code,raw=self.call(trace_path+'?cursor='+str(cursor))
                    self.assertEqual(code,200,raw)
                    trace=json.loads(raw)
                    self.assertTrue(trace['available'])
                    for group in trace['rows']:
                        types.update(item['type'] for item in group['details'])
                    if not trace['more']:
                        break
                    self.assertGreater(trace['cursor'],cursor)
                    cursor=trace['cursor']
                self.assertIn('command',types)
                self.assertIn('receipt',types)
                self.assertEqual(self.call(trace_path+'?limit=11')[0],400)
                self.assertEqual(self.call(trace_path+'?cursor=1')[0],400)
                self.assertEqual(self.call(trace_path,headers={'Host':'evil.invalid'})[0],403)
                self.assertEqual(self.call('/api/series/'+ident+'/export/json')[0],200)
                self.assertIn(b'physical_handovers',self.call('/api/series/'+ident+'/export/csv')[1])
                code,raw=self.call('/api/series/'+ident+'/export/zip')
                self.assertEqual(code,200)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    self.assertIsNone(archive.testzip())
                    manifest=json.loads(archive.read('evidence-manifest.json'))
                    self.assertTrue(any(x['path'].endswith('/report.json') for x in manifest['entries']))
                    for entry in manifest['entries']:
                        self.assertEqual(hashlib.sha256(archive.read(entry['path'])).hexdigest(),entry['sha256'])
                    self.assertFalse(any(name.startswith('scratch/') for name in archive.namelist()))

    def test_slow_rpc_peer_does_not_block_read_only_ui_or_advance_model(self):
        entered=Event();release=Event()
        calls=[]
        class SlowController(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,value,status=200):
                raw=json.dumps(value).encode()
                self.send_response(status);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers()
                self.wfile.write(raw)
            def do_GET(self):
                if self.path!='/v1/controller/health':
                    self.send_error(404);return
                self.reply({'protocol_version':'1.0','component':'controller','status':'ready',
                            'controller_id':'deliberately-slow','supported_operations':['keep']})
            def do_POST(self):
                if self.path!='/v1/controller/decide':
                    self.send_error(404);return
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(request['observation']['time'])
                entered.set()
                release.wait(timeout=25)
                try:
                    self.reply({'protocol_version':'1.0','error':{'code':'TIMEOUT','message':'test peer released'}},408)
                except (ConnectionError,OSError):
                    pass
        with ThreadingHTTPServer(('127.0.0.1',0),SlowController) as peer:
            worker=Thread(target=peer.serve_forever,daemon=True);worker.start()
            self.lab.registry=Registry(controllers=[f'slow={peer.server_port}'])
            try:
                request={'controller':'slow','environment':'reference','profile':'K','mode':'strict',
                         'seeds':[89725161],'timeout':8,'confirm_reset':True}
                code,data=self.call('/api/series',request)
                self.assertEqual(code,202,data)
                ident=json.loads(data)['id']
                self.assertTrue(entered.wait(timeout=20),'real RPC request did not reach test peer')
                progress='/api/series/'+ident+'/trial/0/progress'
                code,raw=self.call(progress)
                self.assertEqual(code,200)
                before=json.loads(raw)
                self.assertEqual([r['kind'] for r in before['rows']],['reset_response'])
                self.assertEqual(before['rows'][0]['time'],0)
                for _ in range(8):
                    for endpoint in ('/api/series','/api/matrix',progress):
                        code,raw=self.call(endpoint)
                        self.assertEqual(code,200)
                        if endpoint==progress:
                            self.assertEqual(json.loads(raw),before)
                self.assertEqual(calls,[0])
                self.assertEqual(self.call('/api/series',request)[0],409)
                deadline=time.monotonic()+25
                while time.monotonic()<deadline and self.lab.get(ident)['state'] in ('queued','running','stopping'):
                    time.sleep(.1)
                run=self.lab.get(ident)
                self.assertEqual(run['state'],'failed',run)
                code,raw=self.call('/api/series/'+ident+'/trial/0')
                self.assertEqual(code,200)
                report=json.loads(raw)
                self.assertEqual(report['exchange'],'failed')
                self.assertTrue(report['inputs_preserved'])
                self.assertEqual(report['first_protocol_error']['stage'],'decide')
                self.assertIn('exceeded',report['first_protocol_error']['message'])
                self.assertEqual(report['independent_truth']['physical_handovers'],0)
                code,raw=self.call(progress)
                self.assertEqual(code,200)
                rows=json.loads(raw)['rows']
                self.assertIn('controller_failure',[r['kind'] for r in rows])
                self.assertNotIn('step_response',[r['kind'] for r in rows])
                self.assertEqual(calls,[0])
            finally:
                release.set();peer.shutdown();worker.join(timeout=5)

    def test_cancel_stops_owned_process_and_preserves_series(self):
        import sys
        request={'controller':'metarescue','environment':'reference','profile':'K','mode':'strict',
                 'seeds':[1,2],'timeout':30,'confirm_reset':True}
        # Controlled long-lived owned child isolates supervisor cancellation
        # semantics from planner runtime. Real trials are tested above.
        with patch('tools.m5_lab.build_command',return_value=[sys.executable,'-c','import time; time.sleep(120)']):
            code,data=self.call('/api/series',request)
            self.assertEqual(code,202,data)
            ident=json.loads(data)['id']
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and self.lab.get(ident)['rows'][0]['state']!='running':
                time.sleep(.05)
            self.assertEqual(self.call('/api/series/'+ident+'/cancel',{})[0],202)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and self.lab.get(ident)['state'] in ('queued','running','stopping'):
                time.sleep(.1)
            run=self.lab.get(ident)
            self.assertEqual(run['state'],'cancelled',run)
            self.assertTrue(all(row['state']=='cancelled' for row in run['rows']))
            self.assertEqual(json.loads((self.lab.root/ident/'series.json').read_text())['state'],'cancelled')


class AssessmentBoundary(TestCase):
    def test_external_q_file_is_not_official(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'q.json'
            path.write_text(json.dumps({'controllers':{'a':'A','b':'B'},'environments':{'a':'A','b':'B'},
                'method_id':'rubric-v1','assessments':[{'controller_id':'a','environment_id':'b',
                    'q':.8,'method_id':'rubric-v1','evidence':['a/b']},{'controller_id':'b','environment_id':'a',
                    'q':.4,'method_id':'rubric-v1','evidence':['b/a']}]}))
            result=load_assessments(path)
            self.assertAlmostEqual(result['value'],.3)
            self.assertFalse(result['official'])
            self.assertTrue(result['historical_formula'])
            document=json.loads(path.read_text())
            document['team_id']='A'
            path.write_text(json.dumps(document))
            result=load_assessments(path)
            self.assertAlmostEqual(result['value'],.68)
            self.assertEqual(result['weights'],{'outbound':.7,'inbound':.3})
            self.assertFalse(result['official'])
            self.assertEqual(result['outbound']['required_pairs'],1)
            self.assertEqual(result['inbound']['required_pairs'],1)
        self.assertEqual(csv_cell('=SUM(1,2)'),"'=SUM(1,2)")


class CatalogPerformance(TestCase):
    def test_450_archives_paginate_without_full_trial_reports(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            for i in range(450):
                ident=f'{i:032x}';folder=root/ident;folder.mkdir()
                (folder/'series.json').write_text(json.dumps({'id':ident,'created':f'{i:04d}',
                    'state':'completed','spec':{'controller':'metarescue','environment':'reference','mode':'strict'},
                    'rows':[{'state':'completed','result':{'exchange':'passed','error':'x'*2000}}]}))
            lab=Lab(root,Registry())
            page=lab.catalog(30,30)
            self.assertEqual(page['total'],450)
            self.assertEqual(len(page['series']),30)
            self.assertEqual(page['series'][0]['id'],f'{419:032x}')
            self.assertEqual(page['series'][0]['passed_count'],1)
            self.assertNotIn('rows',page['series'][0])
            self.assertLess(len(json.dumps(page)),12000)
            self.assertEqual(len(lab.catalog(440,30)['series']),10)
            with self.assertRaises(ValueError):lab.catalog(0,1000)


    def test_matrix_cached_paginated_and_invalidated(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            for i in range(450):
                ident=f'{i:032x}';folder=root/ident;folder.mkdir()
                (folder/'series.json').write_text(json.dumps({'id':ident,'created':f'{i:04d}',
                    'state':'completed','spec':{'controller':'metarescue','environment':'reference','profile':'K','timeout':90},
                    'rows':[{'index':0,'seed':i,'mode':'strict','state':'completed','result':{'exchange':'passed'}}]}))
            lab=Lab(root,Registry())
            with patch('tools.m5_lab.build_matrix',wraps=build_matrix) as builder:
                first=lab.matrix(0,30)
                for i in range(100):
                    page=lab.matrix(i,30)
                    self.assertLess(len(json.dumps(page)),22000)
                    self.assertEqual(len(page['cells']),30)
                self.assertEqual(builder.call_count,1)
                self.assertEqual(first['total'],450)
                self.assertEqual(lab.matrix(mode='declared')['total'],0)
                self.assertEqual(lab.matrix(profile='A')['total'],0)
                ident='0'*32
                run=lab.runs[ident]
                run['rows'][0]['result']['status']='success';lab.persist(run)
                page=lab.matrix(0,30)
                self.assertEqual(builder.call_count,2)
                self.assertNotEqual(first['revision'],page['revision'])
                self.assertEqual(page['cells'][0]['mission_success'],1)

    def test_matrix_does_not_merge_different_profiles_modes_or_seeds(self):
        spec={'controller':'metarescue','environment':'reference','profile':'K','timeout':90}
        runs=[]
        for i,(profile,mode,seed,timeout) in enumerate((('K','strict',1,90),('K','strict',1,90),
                                                         ('K','declared',1,90),('A','strict',1,90),
                                                         ('K','strict',2,90),('K','strict',1,30))):
            runs.append({'id':f'{i:032x}','spec':dict(spec,profile=profile,timeout=timeout),
                         'rows':[{'index':0,'seed':seed,'mode':mode,'state':'completed',
                                  'result':{'exchange':'passed','status':'time_limit','inputs_preserved':True}}]})
        rows,excluded=build_matrix(runs)
        self.assertEqual(excluded,0)
        self.assertEqual(len(rows),5)
        repeated=next(c for c in rows if c['attempts']==2)
        self.assertEqual(repeated['exchange_passed'],2)
        self.assertEqual(repeated['mission_success'],0)
        self.assertEqual(repeated['truth_trials'],0)
        self.assertEqual(repeated['inputs_preserved'],2)


class ArchiveAndRepeatTests(TestCase):
    def test_small_metadata_does_not_allocate_the_maximum_report_buffer(self):
        import tracemalloc
        with TemporaryDirectory() as temp:
            path=Path(temp)/'report.json'
            path.write_text('{"status":"running"}')
            tracemalloc.start()
            try:
                self.assertEqual(read_report(path),{'status':'running'})
                _,peak=tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLess(peak,4*1024*1024)
            with patch('tools.m5_lab.MAX_REPORT',8):
                self.assertEqual(read_report(path),{})

    def test_bad_archives_are_skipped_not_rewritten(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            broken=[{'state':'running','rows':[None]},
                    {'state':'completed','rows':[{'state':'completed','result':None}]},
                    {'state':'completed','rows':[],'spec':[]},
                    {'state':[],'rows':[]}]
            original={}
            for i,body in enumerate(broken):
                ident=f'{i:032x}';folder=root/ident;folder.mkdir()
                path=folder/'series.json'
                path.write_text(json.dumps(dict(body,id=ident)))
                original[path]=path.read_bytes()
            valid='f'*32;folder=root/valid;folder.mkdir()
            (folder/'series.json').write_text(json.dumps({'id':valid,'state':'completed','rows':[]}))
            lab=Lab(root,Registry())
            page=lab.catalog()
            self.assertEqual(page['invalid_archives'],len(broken))
            self.assertEqual([row['id'] for row in page['series']],[valid])
            self.assertEqual(lab.matrix()['cells'],[])
            for path,wire in original.items():
                self.assertEqual(path.read_bytes(),wire)

    def test_exact_repeat_rejects_changed_endpoint_and_inputs(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            body={'controller':'metarescue','environment':'peer','profile':'K','mode':'strict',
                  'seeds':[7],'timeout':30,'confirm_reset':True}
            first='a'*32;folder=root/first;folder.mkdir()
            spec=Registry(['peer=8140']).spec(body)
            path=folder/'series.json'
            path.write_text(json.dumps({'id':first,'state':'completed','spec':spec,'rows':[]}))
            original=path.read_bytes()
            other=Lab(root,Registry(['peer=8141']))
            with self.assertRaisesRegex(ValueError,'endpoint changed'):
                other.start(dict(body,repeat_of=first))
            lab=Lab(root,Registry(['peer=8140']))
            for changes in ({'seeds':[8]},{'timeout':31},{'repeat_of':'b'*32},{'repeat_of':None}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    lab.start(dict(body,**{'repeat_of':first,**changes}))
            self.assertEqual(len(list(root.iterdir())),1)
            with patch('tools.m5_lab.threading.Thread'):
                new=lab.start(dict(body,repeat_of=first))
            self.assertNotEqual(new['id'],first)
            self.assertEqual(new['repeat_of'],first)
            self.assertEqual(new['spec'],spec)
            self.assertEqual(path.read_bytes(),original)


class PairGridTests(TestCase):
    def test_sparse_grid_paginates_both_axes_without_promoting_missing_pairs(self):
        runs=[]
        for i in range(25):
            for seed in (1,2):
                runs.append({'id':f'{i*2+seed:032x}','spec':{
                    'controller':f'c{i:02d}','environment':f'e{i:02d}'},
                    'rows':[{'index':0,'seed':seed,'mode':'strict','state':'completed',
                             'result':{'exchange':'passed','status':'time_limit','inputs_preserved':True}}]})
        cells,excluded=build_matrix(runs)
        self.assertEqual(excluded,0)
        first=pair_grid(cells,['registered'],['empty'],limit=8)
        self.assertEqual(len(first['controllers']),8)
        self.assertEqual(len(first['environments']),8)
        self.assertEqual(first['controller_total'],26)
        self.assertEqual(first['environment_total'],26)
        self.assertEqual(len(first['pairs']),8)
        self.assertTrue(all(p['configurations']==2 and p['attempts']==2 and
                            p['exchange_passed']==2 and p['mission_success']==0 for p in first['pairs']))
        cross=pair_grid(cells,['registered'],['empty'],row_offset=8,column_offset=0,limit=8)
        self.assertEqual(cross['pairs'],[])
        self.assertIsNone(first['official_m5'])
        with self.assertRaises(ValueError):
            pair_grid(cells,[],[],limit=13)


class MissionMatrixTests(TestCase):
    def test_parameters_and_rpc_deadlines_never_merge(self):
        base={'controller':'rpc','environment':'planar','controller_origin':'http://127.0.0.1:8146',
              'timeout':30,'decide_path':'decide','decide_profile':'core'}
        variants=[{}, {'mission_parameters':{'case':'late_target'}},
                  {'mission_parameters':{'opaque':True}}, {'decision_timeout':3}]
        runs=[{'id':f'{i:032x}','spec':dict(base,**extra),
               'rows':[{'index':0,'seed':7,'mode':'strict','state':'completed'}]}
              for i,extra in enumerate(variants)]
        cells,excluded=build_matrix(runs)
        self.assertEqual(excluded,0)
        self.assertEqual(len(cells),4)
        self.assertTrue(all(cell['attempts']==1 for cell in cells))
