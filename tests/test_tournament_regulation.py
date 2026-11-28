"""Organizer admission suite plus independent boundary regressions."""
from copy import deepcopy
from threading import Thread
from unittest import TestCase

from conformance import check_tournament as checker
from contracts.protocol import ProtocolError
from controller.server import TournamentServer
from controller.tournament import TournamentPolicy
from environment.server import PlanarServer
from environment.world import World, capabilities


class TournamentHTTPTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env, cls.ctrl = PlanarServer(0), TournamentServer(0)
        for server in (cls.env, cls.ctrl):
            Thread(target=server.serve_forever, daemon=True).start()
        cls.env_url = f'http://127.0.0.1:{cls.env.server_port}'
        cls.ctrl_url = f'http://127.0.0.1:{cls.ctrl.server_port}'

    @classmethod
    def tearDownClass(cls):
        for server in (cls.env, cls.ctrl):
            server.shutdown()
            server.server_close()

    def test_organizer_strict_multiseed(self):
        for seed in (42, 777, 123456):
            with self.subTest(seed=seed):
                report = checker.check_selftest(self.env_url, self.ctrl_url, seed=seed, max_steps=20, strict=True)
                self.assertEqual((report.failed, report.warned), (0, 0), report.to_dict())
                self.assertTrue(report.admitted)

    def test_raw_wrapped_invalid_and_replayed_requests(self):
        obs = checker.make_synthetic_obs(0, episode_id='regression-http')
        path = '/v1/controller/decide'
        code, first, _, _ = checker.http_json(self.ctrl_url, 'POST', path, obs)
        self.assertEqual(code, 200)
        code, second, _, _ = checker.http_json(self.ctrl_url, 'POST', path, {'observation': obs})
        self.assertEqual((code, second), (200, first))
        changed = deepcopy(obs)
        changed['time'] = 1.
        code, failure, _, _ = checker.http_json(self.ctrl_url, 'POST', path, changed)
        self.assertEqual(code, 422)
        self.assertEqual(failure['error']['code'], 'INVALID_REQUEST')
        code, failure, _, _ = checker.http_json(self.ctrl_url, 'POST', path, {})
        self.assertEqual(code, 422)
        self.assertEqual(failure['error']['code'], 'INVALID_REQUEST')


class TournamentPolicyTests(TestCase):
    def test_id_dialects_and_extra_fields_invariance(self):
        signatures = []
        for dialect in ('object_action', 'object_id', 'strings'):
            obs = checker.make_synthetic_obs(0, dialect=dialect, episode_id='portable', extra=True, minimal=True)
            result = TournamentPolicy().decide(obs)
            self.assertFalse(checker.validate_decision_struct(result, obs, strict_axis=True)[0])
            signatures.append(checker._decision_signature(result))
        self.assertEqual(signatures[0], signatures[1])
        self.assertEqual(signatures[0], signatures[2])

    def test_episode_reset_and_id_renaming(self):
        policy = TournamentPolicy()
        original = checker.make_synthetic_obs(0, episode_id='first')
        policy.decide(original)
        policy.scan_attempts.add(('foreign-agent', (1, 2, 3)))
        policy.cargo['foreign-agent'] = 'foreign-target'
        second = checker.make_synthetic_obs(0, episode_id='second')
        second = checker.PublicNormalizer(seed=987).forward_obs(second)
        result = policy.decide(second)
        self.assertNotIn('foreign-agent', policy.cargo)
        self.assertNotIn(('foreign-agent', (1, 2, 3)), policy.scan_attempts)
        self.assertEqual(result['previous_configuration']['role_assignments'], {})
        self.assertFalse(checker.validate_decision_struct(result, second, strict_axis=True)[0])
        self.assertEqual(set(result['role_assignments']) - {a['id'] for a in second['agents']}, set())

    def test_stop_is_command_free(self):
        obs = checker.make_synthetic_obs(0, safe_stop=True)
        result = TournamentPolicy().decide(obs)
        self.assertEqual(result['operation'], 'safe_stop')
        self.assertFalse(result['commands'])

    def test_duplicate_entity_id_rejected(self):
        obs = checker.make_synthetic_obs(0)
        obs['agents'].append(deepcopy(obs['agents'][0]))
        with self.assertRaises(ProtocolError):
            TournamentPolicy().decide(obs)


class TournamentPlanarTests(TestCase):
    def test_synthesis_without_graph_and_bad_synthesis_atomicity(self):
        world = World(42)
        dec = checker.make_clean_decision(world.id, 0, 0, 'synthesize', ['tournament_synth_01'],
                                          synth=deepcopy(checker.MIN_SYNTH_SCENARIO))
        bad = deepcopy(dec)
        del bad['selected_configuration']['synthesized_scenario']['game_model']
        with self.assertRaises(ProtocolError):
            world.step(bad, 'bad', capabilities())
        self.assertEqual(world.time, 0)
        self.assertFalse(world.synthesized)
        result = world.step(dec, 'good', capabilities())
        self.assertEqual(result['time'], 1)
        self.assertEqual(world.configuration['scenario_ids'], ['tournament_synth_01'])
        self.assertEqual(world.step(dec, 'good', capabilities()), result)
        with self.assertRaises(ProtocolError):
            world.step(dec, 'changed', capabilities())

    def test_public_constraints_and_metrics(self):
        world = World(42, opaque=True)
        obs = world.observation('initial')
        move = {'agent_id': world.scout, 'action': 'move', 'parameters': {'target': [11., 10., 0.], '_peer': True}}
        dec = checker.make_clean_decision(world.id, 0, 0, 'select', ['coverage_search'], commands=[move])
        dec['_unknown'] = {'future': True}
        dec['constraint_checks'] = [{'constraint_id': 'peer', 'status': 'foreign_audit_status'}]
        result = world.step(dec, 'move', capabilities())
        self.assertEqual(result['time'], 1)
        self.assertEqual(result['metrics']['mission_completion'], 0.)
        self.assertEqual(result['metrics']['safety_violations'], 0)
        self.assertEqual(result['command_results'][0]['validation_status'], 'accepted')
        for row in result['observation']['agents']:
            for value in row['resources'].values():
                self.assertLessEqual(0, value['minimum'])
                self.assertLessEqual(value['minimum'], value['value'])
                self.assertLessEqual(value['value'], value['maximum'])
                self.assertLessEqual(value['maximum'], 1)
                self.assertEqual(value['unit'], 'normalized')


if __name__ == '__main__':
    import unittest
    unittest.main()
