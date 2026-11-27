from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import http.client
import hashlib
import io
import zipfile
import json
import time

from tools.m5_lab import Registry,Lab,Server,build_command,endpoint,load_assessments,csv_cell
from tools.m5_matrix import build_matrix


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
        self.assertEqual(self.call('/api/matrix',headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(json.loads(self.call('/api/matrix')[1])['cells'],[])
        self.assertEqual(self.lab.list(),[])

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
                self.assertEqual(self.call(progress_path+'?limit=101')[0],400)
                self.assertEqual(self.call(progress_path+'?cursor=1')[0],400)
                self.assertEqual(self.call(progress_path,headers={'Host':'evil.invalid'})[0],403)
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
