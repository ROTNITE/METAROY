from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import math
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from contracts.protocol import ProtocolError, encode, validate_observation
from controller.client import EnvironmentClient
from controller.decisions import decision
from controller.net65 import Candidate, choose_sector, due
from controller.portable import PortablePolicy
from controller.public_core import PublicCore
from environment.world import World, capabilities
from tools.run_interop import Evidence, run_external


class RepeatPatrolTests(unittest.TestCase):
    def test_activation_needs_coverage_and_the_late_window(self):
        self.assertFalse(due(129, 0, 200, True))
        self.assertFalse(due(130, 0, 200, False))
        self.assertTrue(due(130, 0, 200, True))
        self.assertFalse(due(130, 0, None, True))

    def test_beam_waits_when_the_leg_or_return_is_unaffordable(self):
        start = (0., 0., 0.)
        site = Candidate('sector', (3., 0., 4.), 3.)
        params = dict(start=start, now=130., horizon=200., cycle=20., scan_s=2., scan_energy=.01,
                      energy_scale=.1, estimate=lambda a, b: (math.dist(a, b), .06))
        self.assertEqual(choose_sector([site], **params), 'sector')
        self.assertIsNone(choose_sector([site], **params, available_energy=.1, return_energy=lambda _: .05))
        self.assertIsNone(choose_sector([site], **{**params, 'estimate': lambda *_: None}))
        self.assertIsNone(choose_sector([site], **{**params, 'horizon': 136.}))

    def test_move_ack_is_not_a_scan_and_episode_resets_memory(self):
        world = World(17331, 'late_target')
        policy = PortablePolicy(capabilities(), 'nsga2_nsga2', scout_patrol='net65')
        while world.time < 6:
            request = policy.decide(world.observation(f'obs-{world.time}'))
            response = world.step(request, request['decision_id'], capabilities())
            policy.observe_response(response)
            if world.time == 2:
                self.assertFalse(policy.scanned_at)
        self.assertEqual(len(policy.scanned_at), 1)
        another = World(17332, 'link_loss', opaque=True)
        policy.decide(another.observation('fresh-episode'))
        self.assertEqual(policy.scanned_at, {})
        self.assertEqual(policy.net65_repeats, 0)


class PublicFrameTests(unittest.TestCase):
    def test_frame_converts_positions_and_move_once_preserving_ids(self):
        world = World(17331)
        observation = world.observation('snapshot')
        observation['agents'][0]['position'] = [100., 300., 200.]
        caps = capabilities()
        caps['coordinates'] = {'axes': ['x', 'up', 'z'], 'unit': 'cm'}
        frame = PublicCore(caps)
        reading = frame.view(observation)['agents'][0]
        self.assertEqual(reading['position'], [1., 2., 3.])
        self.assertEqual(reading['id'], observation['agents'][0]['id'])
        self.assertEqual(frame.parameters('move', {'target': reading['position']})['target'], [100., 300., 200.])
        self.assertEqual(observation['agents'][0]['position'], [100., 300., 200.])

    def test_optional_space_and_named_constraints_need_public_declarations(self):
        world = World(17331)
        view = world.observation('snapshot')
        view['agents'][0]['kind'] = 'space'
        view['constraints'].append({'id':'reserve', 'type':'resource_reserve', 'minimum':.1})
        view['mission']['constraints'] = ['reserve']
        caps = {'protocol_version':'1.0', 'agent_kinds':['space'],
                'extensions':['space_agent_kind','legacy_request_aliases'], 'actions':['move','rescue']}
        public = PublicCore(caps)
        normalized = public.wire_observation(view)
        validate_observation(normalized, allow_space=public.allow_space)
        with self.assertRaises(ProtocolError):
            validate_observation(normalized)
        self.assertEqual(view['mission']['constraints'], ['reserve'])
        self.assertEqual(public.actions['rescue']['parameters_schema']['required'], ['target_id'])


class PlanarCoreTests(unittest.TestCase):
    def test_steps_are_replayable_and_rejected_commands_do_not_advance(self):
        world = World(17331, 'blocked_route')
        first = world.observation('snapshot')
        blocked = decision(first, commands=[{'agent_id': world.scout, 'action': 'move',
                                             'parameters': {'target': [40, 40, 0]}}])
        with self.assertRaises(ProtocolError) as failure:
            world.step(blocked, 'blocked', capabilities())
        self.assertEqual(failure.exception.code, 'CONSTRAINT_VIOLATION')
        self.assertEqual(world.observation('snapshot'), first)
        move = decision(first, commands=[{'agent_id': world.scout, 'action': 'move',
                                          'parameters': {'target': [25, 10, 0]}}])
        moved = world.step(move, 'wire', capabilities())
        self.assertEqual(moved['observation']['agents'][0]['position'], [19., 10., 0.])
        self.assertEqual(world.step(move, 'wire', capabilities()), moved)
        self.assertEqual(world.time, 1)
        with self.assertRaises(ProtocolError):
            world.step(move, 'different wire', capabilities())

    def test_seed_determinism_and_noninterpreted_ids(self):
        first = World(17331, 'late_target', opaque=True)
        replay = World(17331, 'late_target', opaque=True)
        for sequence in range(5):
            observations = []
            for world in (first, replay):
                request = decision(world.observation('probe'))
                observation = deepcopy(world.step(request, str(sequence), capabilities())['observation'])
                observation.pop('episode_id')
                observation.pop('request_id')
                observations.append(observation)
            self.assertEqual(*observations)
            self.assertEqual(observations[0]['targets'], [])


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_driver_observation_uses_a_fresh_connection_after_readiness_wait(self):
        observation = World(17331).observation('terminal')
        observation['mission']['status'] = 'safe_stopped'
        responses = {
            'capabilities': capabilities(),
            'observation': observation,
            'logs': {'protocol_version': '1.0', 'episode_id': observation['episode_id'],
                     'records': [], 'next_cursor': None},
        }

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                endpoint = urlsplit(self.path).path.rsplit('/', 1)[-1]
                body = encode(responses[endpoint])
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if endpoint == 'capabilities':
                    self.close_connection = True

        health = {'protocol_version': '1.0', 'component': 'controller', 'status': 'ready'}
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            client = EnvironmentClient(f'http://127.0.0.1:{server.server_port}', timeout_s=2)
            args = SimpleNamespace(seed=17331, environment_port=server.server_port, mode='driver',
                                   controller='unused', timeout=2, engine='planar')
            try:
                with tempfile.TemporaryDirectory() as output, patch(
                        'tools.run_interop.controller_call', return_value=health):
                    report = run_external(args, client, Path(output), {})
            finally:
                client.disconnect()
                server.shutdown()
                worker.join(timeout=2)
        self.assertIsNone(report['first_protocol_error'])
        self.assertEqual(report['exchange'], 'passed')
        self.assertEqual(report['status'], 'safe_stopped')

    def test_confirmed_transfer_requires_the_submitted_agent_action_and_ack(self):
        evidence = Evidence()
        evidence.record('decision', {'decision': {'decision_id': 'mission-command',
                                                  'commands': [{'agent_id': 'carrier', 'action': 'handover'}]}})
        receipt = {'decision_id': 'mission-command', 'command_index': 0, 'agent_id': 'carrier',
                   'action': 'handover', 'validation_status': 'accepted', 'delivery_status': 'delivered',
                   'execution_status': 'completed'}
        for changes in ({'decision_id': 'unknown'}, {'command_index': -1}, {'agent_id': 'different'},
                        {'action': 'deliver'}, {'delivery_status': 'pending'}):
            evidence.record('step_response', {'command_results': [{**receipt, **changes}]})
        self.assertEqual(evidence.summary()['confirmed_command_receipts'], [])
        evidence.record('step_response', {'command_results': [receipt, receipt]})
        self.assertEqual(evidence.summary()['confirmed_command_receipts'], [receipt])

    def test_external_report_attributes_rejected_parameters_to_environment_step(self):
        observation = World(17331).observation('initial')
        request = decision(observation, operation='select', selected={
            'scenario_ids': ['coverage_search'], 'synthesized_scenario': None,
            'parameters': {'search_grid_resolution': 5},
        })
        caps = capabilities()
        caps['supported_scenarios'] = {'coverage_search': {'parameter_schema': {
            'type': 'object', 'properties': {'observation_interval': {'type': 'number'}},
            'additionalProperties': False,
        }}}
        client = Mock()
        client.capabilities.return_value = caps
        client.reset.return_value = {'observation': observation, 'episode_id': observation['episode_id'],
                                     'status': 'running'}
        client.step.side_effect = ProtocolError('INVALID_SCENARIO', 'Invalid scenario parameters', {})
        client.logs.return_value = {'episode_id': observation['episode_id'], 'records': [], 'next_cursor': None}
        client.close.return_value = {}
        health = {'status': 'ready', 'component': 'controller', 'protocol_version': '1.0'}
        args = SimpleNamespace(seed=17331, environment_port=12345, mode='decide',
                               controller='unused', timeout=1, decide_profile='core',
                               decide_path='decide', decision_timeout=1, engine='planar')
        with tempfile.TemporaryDirectory() as output, patch(
                'tools.run_interop.controller_call', side_effect=[health, request]):
            report = run_external(args, client, Path(output), {})
        error = report['first_protocol_error']
        self.assertEqual(report['exchange'], 'failed')
        self.assertEqual(error['kind'], 'environment_rejection')
        self.assertEqual(error['stage'], 'step')
        self.assertEqual(error['code'], 'INVALID_SCENARIO')
        self.assertEqual(error['decision_id'], request['decision_id'])
        self.assertEqual(error['scenario_parameters'], {
            'scenario_id': 'coverage_search', 'path': ['selected_configuration', 'parameters'],
            'rule': 'additionalProperties', 'unexpected_keys': ['search_grid_resolution'],
        })
        client.step.assert_called_once_with(request)
        client.close.assert_called_once_with(observation['episode_id'])
