from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import http.client
import json
import time

from tools.m5_lab import Registry,Lab,Server,build_command,endpoint


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
                code,raw=self.call('/api/series/'+ident+'/trial/0')
                self.assertEqual(code,200)
                self.assertTrue(json.loads(raw)['independent_truth']['all_evacuated'])
                self.assertEqual(self.call('/api/series/'+ident+'/export/json')[0],200)
                self.assertIn(b'physical_handovers',self.call('/api/series/'+ident+'/export/csv')[1])
