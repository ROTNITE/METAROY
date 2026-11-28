"""Organizer admission suite plus independent boundary regressions."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
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

    def test_concurrent_replay_of_retired_episode_does_not_restore_it(self):
        path = '/v1/controller/decide'
        old = checker.make_synthetic_obs(0, episode_id='retired-old')
        new = checker.make_synthetic_obs(0, episode_id='retired-new')
        _, first, _, _ = checker.http_json(self.ctrl_url, 'POST', path, old)
        code, _, _, _ = checker.http_json(self.ctrl_url, 'POST', path, new)
        self.assertEqual(code, 200)
        def send(obs):
            code, body, _, _ = checker.http_json(self.ctrl_url, 'POST', path, obs)
            return code, body
        with ThreadPoolExecutor(4) as workers:
            replay = list(workers.map(send, [old]*4))
        self.assertEqual(replay, [(200, first)]*4)
        changed = {**old, 'request_id': 'retired-delayed', 'time': 2.}
        code, error = send(changed)
        self.assertEqual((code, error['error']['code']), (422, 'UNKNOWN_EPISODE'))
        self.assertEqual(self.ctrl.policy.episode, new['episode_id'])


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


class TournamentAdversarialTests(TestCase):
    def test_explicit_custody_and_busy_service_not_cancelled(self):
        policy = TournamentPolicy()
        obs = checker.make_synthetic_obs(0, episode_id='custody')
        rover = obs['agents'][1]
        rover['status'] = 'busy'
        rover['available_actions'] = [{'action': 'hold'}]
        target = obs['targets'][0]
        target['evacuation_state'] = 'carried'
        policy._reset(obs['episode_id'])
        policy.pickup_attempts[target['id']] = rover['id']
        result = policy.decide(obs)
        self.assertNotIn(rover['id'], policy.cargo)
        self.assertFalse(any(c['agent_id'] == rover['id'] for c in result['commands']))
        obs = deepcopy(obs)
        obs['time'], obs['request_id'] = 1., 'custody-2'
        obs['targets'][0]['carrier_id'] = rover['id']
        result = policy.decide(obs)
        self.assertEqual(policy.cargo, {rover['id']: target['id']})
        self.assertFalse(any(c['agent_id'] == rover['id'] for c in result['commands']))

    def test_ground_support_and_overlapping_air_permissions(self):
        from controller.tournament import public_observation, _zone_shape
        obs = checker.make_synthetic_obs(0)
        rover = obs['agents'][1]
        rover['position'] = [40., 20., .43]
        for zone in obs['zones']:
            if zone['type'] == 'land':
                zone['geometry']['max'][2] = .08
                zone['access'] = {'allowed_kinds': ['ground']}
            elif zone['type'] == 'air':
                zone['access'] = {'allowed_kinds': ['aerial']}
        public = public_observation(obs)
        policy = TournamentPolicy()
        zones = [(z, _zone_shape(z)) for z in public['zones']]
        self.assertTrue(policy._clear(public, zones, rover, (40., 20., .43), (60., 45., .43)))
        self.assertFalse(policy._clear(public, zones, rover, (40., 20., .43), (90., 90., .43)))

    def test_unknown_hard_rule_does_not_dispatch_service(self):
        obs = checker.make_synthetic_obs(0)
        obs['agents'][1]['constraints'] = [{'id': 'mandatory', 'type': 'unrecognized', 'severity': 'hard'}]
        result = TournamentPolicy().decide(obs)
        self.assertFalse(any(c['agent_id'] == obs['agents'][1]['id'] and c['action'] != 'hold' for c in result['commands']))

    def test_perception_uses_report_source_and_waits_for_confirmation(self):
        obs = checker.make_synthetic_obs(0)
        observer = obs['agents'][0]
        target = obs['targets'][0]
        observer['available_actions'].append({'action': 'confirm'})
        obs['mission']['objectives'].append({'id': 'verify-targets', 'type': 'confirm', 'status': 'active'})
        obs['events'] = [{'id': 'discovery', 'type': 'target_detected', 'time': 0.,
                          'source': observer['id'], 'targets': [target['id']],
                          'parameters': {}, 'confidence': .9, 'status': 'new'}]
        policy = TournamentPolicy()
        result = policy.decide(obs)
        self.assertIn({'agent_id': observer['id'], 'action': 'confirm',
                       'parameters': {'target_id': target['id']}}, result['commands'])
        self.assertFalse(any(cmd['action'] == 'deliver' for cmd in result['commands']))
        self.assertEqual(policy.cargo, {})

    def test_search_waypoint_has_priority_over_generic_coverage(self):
        obs = checker.make_synthetic_obs(0)
        scout = obs['agents'][0]
        obs['targets'] = []
        aim = [68., 17., scout['position'][2]]
        obs['mission']['objectives'] = [{'id': 'public-search', 'type': 'search',
                                        'status': 'active', 'position': aim}]
        policy = TournamentPolicy()
        policy.decide(obs)
        next_obs = {**obs, 'request_id': 'public-search-next', 'time': 2.}
        decision = policy.decide(next_obs)
        self.assertIn({'agent_id': scout['id'], 'action': 'move',
                       'parameters': {'target': aim}}, decision['commands'])

    def test_malformed_nested_input_rejected_not_crash(self):
        for key, value in (('status', []), ('evacuation_state', {}), ('position', [1,2]), ('carrier_id', [])):
            obs = checker.make_synthetic_obs(0)
            obs['targets'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                TournamentPolicy().decide(obs)

    def test_synthesis_rejects_malformed_roles_before_mutation(self):
        for roles in ([{'id':'scout','required_capabilities':42}], ['scout', 'scout'],
                      [{'id':'scout','minimum':2,'maximum':1}], [{'id':'scout','minimum':1}]):
            world = World(42)
            synth = deepcopy(checker.MIN_SYNTH_SCENARIO)
            synth['roles'] = roles
            dec = checker.make_clean_decision(world.id, 0, 0, 'synthesize', [synth['id']], synth=synth)
            with self.subTest(roles=roles), self.assertRaises(ProtocolError) as failure:
                world.step(dec, 'malformed', capabilities())
            self.assertEqual(failure.exception.code, 'INVALID_SCENARIO')
            self.assertEqual(world.time, 0)
            self.assertFalse(world.synthesized)
            self.assertFalse(world.journal)

    def test_keep_cannot_change_parameters(self):
        world = World(42)
        select = checker.make_clean_decision(world.id, 0, 0, 'select', ['coverage_search'])
        world.step(select, 'select', capabilities())
        keep = checker.make_clean_decision(world.id, 1, 1, 'keep', ['coverage_search'], ['coverage_search'], params={'priority_bias': 1.2})
        with self.assertRaises(ProtocolError) as error:
            world.step(keep, 'keep', capabilities())
        self.assertEqual(error.exception.code, 'INVALID_SCENARIO')
        self.assertEqual(world.time, 1)

    def test_long_missions_with_normalization_and_physical_witness(self):
        for case in ('normal', 'late_target', 'link_loss', 'blocked_route'):
            for seed in (42, 777, 123456):
                with self.subTest(case=case, seed=seed):
                    world, policy = World(seed, case, opaque=True), TournamentPolicy()
                    norm = checker.PublicNormalizer(seed=seed+1)
                    obs = world.observation('initial')
                    completion = 0.
                    for tick in range(201):
                        if world.status != 'running':
                            break
                        proposed = policy.decide(norm.forward_obs(obs))
                        result = world.step(norm.backward_decision(proposed), str(tick), capabilities())
                        obs = result['observation']
                        self.assertGreaterEqual(obs['metrics']['mission_completion'], completion)
                        self.assertEqual(obs['metrics']['safety_violations'], 0)
                        completion = obs['metrics']['mission_completion']
                    if case == 'blocked_route':
                        self.assertEqual(world.status, 'time_limit')
                        self.assertEqual(completion, 0.)
                    else:
                        self.assertEqual(world.status, 'success')
                        self.assertEqual(completion, 1.)
                        self.assertTrue(all(person.custody == world.base for person in world.people.values()))


if __name__ == '__main__':
    import unittest
    unittest.main()
