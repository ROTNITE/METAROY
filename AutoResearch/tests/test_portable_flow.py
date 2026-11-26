import unittest
from controller.portable import PortablePolicy
from environment.world import World,Agent,Person,capabilities
from contracts.protocol import encode
from hashlib import sha256

class PortableFlowTests(unittest.TestCase):
    def fixture(self):
        w=World(73,opaque=True)
        w.agents={w.scout:w.agents[w.scout], 'r/one':Agent('ground',(10.,10.,0.),1.), 'r/two':Agent('ground',(20.,10.,0.),1.)}
        w.people={'p/a':Person((16.,10.,0.),0),'p/b':Person((10.,18.,0.),0)}
        w.pose={k:a.position for k,a in w.agents.items()};w.energy={k:a.energy for k,a in w.agents.items()};w.visible={k:0 for k in w.people}
        return w
    def test_global_match_beats_sequential_nearest(self):
        w=self.fixture();obs=w.observation('one');p=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True)
        d=p.decide(obs);trace=next(t for t in p.drain_traces() if t.get('kind')=='flow_wave')
        self.assertEqual(trace['matches'],{'r/one':'p/b','r/two':'p/a'})
        self.assertEqual(trace['decision_id'],d['decision_id'])
        self.assertIn('portable_flow_wave',d['explanation']['reason_codes'])
        w.step(d,sha256(encode(d)).hexdigest(),capabilities())
    def test_reorder_same_match(self):
        w=self.fixture();o=w.observation('one');a=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True);a.decide(o)
        expected=next(t['matches'] for t in a.drain_traces() if t.get('kind')=='flow_wave')
        for k in ('agents','zones','targets'):o[k].reverse()
        b=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True);b.decide(o)
        self.assertEqual(next(t['matches'] for t in b.drain_traces() if t.get('kind')=='flow_wave'),expected)
    def test_default_and_contradiction_do_not_match(self):
        w=self.fixture();o=w.observation('one');p=PortablePolicy(capabilities(),'nsga2_nsga2');p.decide(o)
        self.assertFalse(any(t.get('kind')=='flow_wave' for t in p.drain_traces()))
        o['observation_quality']['contradiction_count']=1;p=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True);p.decide(o)
        self.assertFalse(any(t.get('kind')=='flow_wave' for t in p.drain_traces()))
    def test_low_public_energy_keeps_stock_dispatch(self):
        w=self.fixture();o=w.observation('one')
        for a in o['agents']:
            if a['id']=='r/one':a['resources']['energy']['value']=.18
        p=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True);p.decide(o)
        self.assertFalse(any(t.get('kind')=='flow_wave' for t in p.drain_traces()))
    def test_real_episode_and_ack(self):
        w=self.fixture();p=PortablePolicy(capabilities(),'nsga2_nsga2',flow_dispatch=True)
        while w.status=='running':
            d=p.decide(w.observation(str(w.time)));r=w.step(d,sha256(encode(d)).hexdigest(),capabilities());p.observe_response(r)
        self.assertEqual(w.status,'success');self.assertEqual(sum(v.custody==w.base for v in w.people.values()),2)
