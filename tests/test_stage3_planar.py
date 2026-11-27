"""HTTP regressions for independently declared planar capabilities."""
from copy import deepcopy
from threading import Thread
from unittest import TestCase

from conformance.check import prepare
from contracts.protocol import ProtocolError, validate_decision, validate_observation
from controller.client import EnvironmentClient
from controller.portable import PortablePolicy, _box
from environment.server import PlanarServer
from environment.world import World, capabilities


class PlanarBoundary(TestCase):
    def setUp(self):
        self.server = PlanarServer(0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = EnvironmentClient(f'http://127.0.0.1:{self.server.server_port}')
        self.caps = self.client.capabilities()
        self.frame = self.client.reset(seed=17)['observation']

    def tearDown(self):
        self.client.disconnect()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_exact_replay_and_changed_body_rejection(self):
        first = prepare(self.frame)
        a = self.client.step(first)
        self.assertEqual(a,self.client.step(first))
        changed = deepcopy(first)
        changed['explanation']['reason_codes'].append('different-body')
        with self.assertRaises(ProtocolError) as failure:
            self.client.step(changed)
        self.assertEqual(failure.exception.code,'INVALID_REQUEST')
        self.assertEqual(self.server.episode.time,a['time'])
        self.assertEqual(len(self.server.episode.journal),1)

    def test_observe_default_is_published_and_detects_target(self):
        scout = self.frame['agents'][0]['id']
        params = self.caps['supported_actions']['observe']['parameters_schema']
        self.assertEqual(params['required'],[])
        self.assertEqual(params['properties']['radius']['default'],15.)
        result=self.client.step(prepare(self.frame,commands=[{'agent_id':scout,'action':'observe','parameters':{}}]))
        for _ in range(4):
            result=self.client.step(prepare(result['observation']))
        self.assertTrue(result['observation']['targets'])
        self.assertTrue(self.server.episode.visible)

    def test_planar_bounds_are_public_and_enforced_atomically(self):
        scout = self.frame['agents'][0]
        self.assertEqual(scout['constraints'][0]['max'],[50,50,0])
        decision=prepare(self.frame,commands=[{'agent_id':scout['id'],'action':'move','parameters':{'target':[20,20,5]}}])
        with self.assertRaises(ProtocolError) as failure:
            self.client.step(decision)
        self.assertEqual(failure.exception.code,'INVALID_COMMAND')
        self.assertEqual(failure.exception.details['action'],'move')
        self.assertEqual(failure.exception.details['path'][:3],['commands',0,'parameters'])
        self.assertEqual(self.server.episode.time,0)
        self.assertFalse(self.server.episode.journal)

    def test_missing_parameter_has_machine_readable_context(self):
        scout=self.frame['agents'][0]
        with self.assertRaises(ProtocolError) as failure:
            self.client.step(prepare(self.frame,commands=[{'agent_id':scout['id'],'action':'move','parameters':{}}]))
        self.assertEqual(failure.exception.details['missing'],['target'])
        self.assertEqual(failure.exception.details['command_index'],0)

    def test_generic_bounds_are_enforced_by_strict_portable_planner(self):
        frame=World(17).observation('test')
        validate_observation(frame)
        policy=PortablePolicy(capabilities(),'nsga2_nsga2')
        agent=frame['agents'][1]
        zones=[(z,_box(z)) for z in frame['zones']]
        self.assertTrue(policy._motion_clear(frame,zones,agent,[20,20,0]))
        self.assertFalse(policy._motion_clear(frame,zones,agent,[20,20,1]))
