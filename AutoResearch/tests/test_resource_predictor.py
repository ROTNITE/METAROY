import math
import unittest
from collections import deque
from hashlib import sha256
from controller.resource_predictor import ResourcePolicy
from environment.world import World, Agent, Person, capabilities, MOVE_COST
from contracts.protocol import encode
from experiments.mission_flow.benchmark_resource import physical


class ResourceTests(unittest.TestCase):
    def fixture(self):
        w=World(73,opaque=True)
        w.agents={w.scout:w.agents[w.scout], 'r/one':Agent('ground',(10.,10.,0.),1.), 'r/two':Agent('ground',(20.,10.,0.),1.)}
        w.people={'p/a':Person((16.,10.,0.),0),'p/b':Person((10.,18.,0.),0)}
        w.pose={k:a.position for k,a in w.agents.items()};w.energy={k:a.energy for k,a in w.agents.items()};w.visible={k:0 for k in w.people}
        return w

    def policy(self):
        return ResourcePolicy(capabilities(),'nsga2_nsga2')

    def test_learns_rate_and_service_from_execution(self):
        w,p=self.fixture(),self.policy()
        while w.status=='running':
            d=p.decide(w.observation(str(w.time)))
            p.observe_response(w.step(d,sha256(encode(d)).hexdigest(),capabilities()))
        self.assertEqual(w.status,'success')
        for a in w.observation('end')['agents']:
            if a['kind']=='ground':
                self.assertAlmostEqual(p.rate(a,False),MOVE_COST)
                self.assertAlmostEqual(p.rate(a,True),MOVE_COST)
                self.assertIsNotNone(p.service_time(a['id']))

    def test_reset_and_unknown_units(self):
        p=self.policy(); w=self.fixture();obs=w.observation('first');p.decide(obs)
        a=next(a for a in obs['agents'] if a['kind']=='ground')
        p.energy_samples[(a['id'],p.resource_signature(a),False)]=deque([.001]*3)
        self.assertAlmostEqual(p.rate(a,False),.001)
        a['resources']['energy']['unit']='Wh'; self.assertIsNone(p.rate(a,False))
        p.decide(self.fixture().observation('second'))
        self.assertEqual(p.energy_samples,{})

    def test_no_learning_from_disconnect_or_charge(self):
        w,p=self.fixture(),self.policy();obs=w.observation('first');p.decide(obs)
        a=next(a for a in obs['agents'] if a['kind']=='ground'); aid=a['id']
        p.pending[aid]=('d',0,('casualty','p/a'),(aid,'move',''))
        obs['time']=1;a['position'][0]+=1;a['resources']['energy']['value']=.99
        a['communication']['reachable']=False;p.decide(obs)
        self.assertFalse(p.energy_samples)
        obs['time']=2;a['communication']['reachable']=True;p.decide(obs)
        obs['time']=3;a['position'][0]+=1;a['resources']['energy']['value']=1.
        p.decide(obs);self.assertFalse(p.energy_samples)

    def test_guard_does_not_break_handover(self):
        w,p=self.fixture(),self.policy();obs=w.observation('first');p.decide(obs)
        a=next(a for a in obs['agents'] if a['id']=='r/one');p.cargo[a['id']]='p/a'
        a['resources']['energy']['value']=.0801
        p.energy_samples[(a['id'],p.resource_signature(a),False)]=deque([.001]*3)
        d=p.decide(obs)
        self.assertTrue(any(c['agent_id']=='r/one' and c['action']=='handover' for c in d['commands']))

    def test_known_prior_resource_exhaustion(self):
        b=physical(94045,'bottleneck-patrol',True,True,False)
        a=physical(94045,'localguard',True,True,False)
        self.assertIn('RESOURCE_EXHAUSTED',b['error'])
        self.assertIsNone(a['error']);self.assertEqual(a['saved'],b['saved']);self.assertEqual(a['time'],200)
        self.assertEqual(a['reserve_violations'],0)

    def test_local_guard_fallback_repro(self):
        b=physical(106345,'guard-buffer',True,True,True)
        a=physical(106345,'localguard',True,True,True)
        self.assertIn('RESOURCE_EXHAUSTED',b['error'])
        self.assertIsNone(a['error']);self.assertGreaterEqual(a['saved'],b['saved']);self.assertEqual(a['reserve_violations'],0)
