import unittest
from hashlib import sha256
from controller.bottleneck import BottleneckPolicy
from controller.portable import PortablePolicy, _box
from environment.world import World, Agent, Person, capabilities
from contracts.protocol import encode


class BottleneckTests(unittest.TestCase):
    def fixture(self):
        w = World(73, opaque=True)
        w.agents = {w.scout: w.agents[w.scout],
                    'r/one': Agent('ground', (10., 10., 0.), 1.),
                    'r/two': Agent('ground', (20., 10., 0.), 2.)}
        w.people = {'p/a': Person((16., 10., 0.), 0), 'p/b': Person((10., 18., 0.), 0)}
        w.pose = {k: a.position for k, a in w.agents.items()}
        w.energy = {k: a.energy for k, a in w.agents.items()}
        w.visible = {k: 0 for k in w.people}
        return w

    def policy(self, staged=False, estimates=True):
        return BottleneckPolicy(capabilities(estimates=estimates), 'nsga2_nsga2', scout_patrol='net65', staged_patrol=staged)

    def test_full_episode_and_public_motion_estimates(self):
        w, p = self.fixture(), self.policy()
        while w.status == 'running':
            decision = p.decide(w.observation(str(w.time)))
            p.observe_response(w.step(decision, sha256(encode(decision)).hexdigest(), capabilities()))
        self.assertEqual(w.status, 'success')
        self.assertEqual(sum(a.custody == w.base for a in w.people.values()), 2)
        self.assertAlmostEqual(p.speeds['r/one'], 1.)
        self.assertAlmostEqual(p.speeds['r/two'], 2.)
        self.assertTrue(all(a.energy >= .08 for a in w.agents.values()))

    def test_deadline_gate_has_correlated_audit(self):
        w, p = self.fixture(), self.policy()
        obs = w.observation('late')
        obs['time'] = 199
        p._reset(obs['episode_id']); p.started = 0
        p.speeds = {'r/one': 1., 'r/two': 2.}
        d = p.decide(obs)
        trace = next(t for t in p.drain_traces() if t.get('method') == 'bottleneck_v1')
        self.assertEqual(trace['decision_id'], d['decision_id'])
        self.assertEqual(trace['cost_unit'], 'millisecond')
        self.assertTrue(trace['deadline_filtered'])
        self.assertTrue(all(x is None for x in trace['matches'].values()))
        self.assertFalse(any(c['agent_id'].startswith('r/') for c in d['commands']))

    def test_unknown_speed_retains_geometric_cost(self):
        w, p = self.fixture(), self.policy()
        d = p.decide(w.observation('first'))
        trace = next(t for t in p.drain_traces() if t.get('method') == 'bottleneck_v1')
        self.assertEqual(trace['decision_id'], d['decision_id'])
        self.assertEqual(trace['cost_unit'], 'millimetre')
        self.assertFalse(trace['deadline_filtered'])

    def test_staged_patrol_uses_reachable_component(self):
        w = World(2, 'blocked_route')
        p = self.policy(staged=True)
        obs = w.observation('first')
        p.decide(obs)
        scout = next(a for a in obs['agents'] if a['kind']=='aerial')
        zones = [(z, _box(z)) for z in obs['zones']]
        baseline = list(PortablePolicy(capabilities(), 'nsga2_nsga2')._scout_points(zones, scout))
        reachable = p._scout_points(zones, scout)
        self.assertTrue(p.scout_enabled)
        self.assertLess(len(reachable), len(baseline))
        self.assertTrue(p._patrol_due(80, 0, 200, True))
        self.assertFalse(p._patrol_due(79, 0, 200, True))
        self.assertFalse(p._patrol_due(100, 0, 200, False))

    def test_low_energy_and_missing_estimates_fallback(self):
        w = self.fixture()
        obs = w.observation('first')
        for a in obs['agents']:
            if a['kind'] == 'ground': a['resources']['energy']['value'] = .18
        p = self.policy(staged=True)
        p.decide(obs)
        self.assertFalse(p.scout_enabled)
        self.assertFalse(p._patrol_due(80, 0, 200, True))
        self.assertFalse(any(t.get('method')=='bottleneck_v1' for t in p.drain_traces()))
        p = self.policy(staged=True, estimates=False)
        p.decide(self.fixture().observation('new'))
        self.assertFalse(p.scout_enabled)

    def test_episode_reset_clears_learned_state(self):
        w, p = self.fixture(), self.policy(staged=True)
        p.decide(w.observation('first'))
        p.speeds['r/one']=12.; p.visits[(1,2,3)]=3
        p.decide(self.fixture().observation('new'))
        self.assertEqual(p.speeds, {})
        self.assertEqual(p.visits, {})
