"""Regression tests for the Core validation hot path and malformed extensions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from contracts import protocol
from environment.world import World, capabilities


class CoreValidationRegression(unittest.TestCase):
    def setUp(self):
        self.world = World(7)
        self.response = self.world.response('request', step=True)
        self.observation = self.response['observation']

    def test_response_schema_checked_once_including_large_geometry(self):
        zone = self.observation['zones'][0]
        self.observation['zones'] = [
            dict(zone, id=f'zone-{i}') for i in range(600)
        ]
        for step in (True, False):
            with self.subTest(step=step), patch.object(
                    protocol, 'validate', wraps=protocol.validate) as validate:
                self.assertIs(protocol.validate_episode_response(self.response, step=step),
                              self.response)
                self.assertEqual(validate.call_count, 1)

    def test_semantics_and_nested_schema_still_checked(self):
        def duplicate(obs):
            obs['zones'].append(deepcopy(obs['zones'][0]))
        def bounds(obs):
            resource = obs['agents'][0]['resources']['energy']
            resource.update(minimum=.5, value=.1)
        def freshness(obs):
            obs['agents'][0]['freshness'] = {'position': {'observed_at': 1}}
        def invalid_position(obs):
            obs['agents'][0]['position'] = None
        def wrong_kind(obs):
            obs['agents'][0]['kind'] = 'unexpected'
        def invalid_nested_schema(obs):
            del obs['network']['known_links']
        for mutate in (duplicate, bounds, freshness, invalid_position,
                       wrong_kind, invalid_nested_schema):
            with self.subTest(mutation=mutate.__name__):
                response = deepcopy(self.response)
                mutate(response['observation'])
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_episode_response(response, step=True)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.validate_observation(response['observation'])

    def test_space_profile_is_not_enabled_by_optimization(self):
        response = deepcopy(self.response)
        response['observation']['agents'][0]['kind'] = 'space'
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_episode_response(response, step=True)
        self.assertIs(protocol.validate_episode_response(response, step=True, allow_space=True),
                      response)

    def test_malformed_known_extension_has_normative_error_without_state_change(self):
        examples = json.loads((Path(__file__).parents[1] /
                               'contracts/examples/interaction-v1.json').read_text(encoding='utf-8'))
        decision = deepcopy(examples['decisions']['select'])
        decision.update(episode_id=self.world.id, request_id='request',
                        time=self.world.time, previous_configuration=deepcopy(self.world.configuration),
                        selected_configuration={'scenario_ids':['coverage_search'],
                                                'parameters':{}, 'synthesized_scenario':None},
                        role_assignments={}, commands=[])
        malformed = (None, [], 'invalid', {'command_groups': None},
                     {'command_groups': {}}, {'command_groups': 'x'},
                     {'command_groups': [None]}, {'command_groups': ['x']},
                     {'command_groups': [{}]}, {'command_groups': [{'command_indices': 1}]},
                     {'command_groups': [{'command_indices': '0'}]},
                     {'command_groups': [{'command_indices': []}]},
                     {'command_groups': [{'command_indices': [True]}]},
                     {'command_groups': [{'command_indices': [0]}]})
        for extension in malformed:
            with self.subTest(extension=extension):
                candidate = dict(decision, metaevolution=extension)
                with self.assertRaises(protocol.ProtocolError) as error:
                    self.world.step(candidate, 'fingerprint', capabilities())
                self.assertEqual(error.exception.code, 'INVALID_REQUEST')
                self.assertEqual(self.world.time, 0)
                self.assertEqual(self.world.journal, [])
                self.assertEqual(self.world.replays, {})
                self.assertEqual(self.world.configuration['scenario_ids'], [])
        # Unknown optional keys stay ignorable; the known namespace may
        # contain arbitrary future keys without making them required.
        valid = dict(decision, metaevolution={'future_extension': {'x': 1}},
                     unrelated_extension=['anything'])
        self.world.step(valid, 'fingerprint', capabilities())
        self.assertEqual(self.world.time, 1)

    def test_bad_peer_parameter_schema_is_rejected_as_normative_error(self):
        for schema in ({'$ref':'#/$defs/missing'}, {'$ref':'#'},
                       {'type':'no-such-type'}, {'type':7}, {'pattern':'['},
                       {'type':'object','properties':[]}):
            for code in ('INVALID_COMMAND','INVALID_SCENARIO'):
                with self.subTest(schema=schema,code=code),self.assertRaises(protocol.ProtocolError) as error:
                    protocol.validate({},schema,code)
                self.assertEqual(error.exception.code,code)
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate({}, {'$ref':'#/missing'})

    def test_checked_schema_cache_does_not_reuse_mutated_descriptor(self):
        schema={'type':'object','properties':{'x':{'type':'integer'}},'required':['x']}
        for _ in range(3):
            self.assertEqual(protocol.validate({'x':1},schema,'INVALID_COMMAND'),{'x':1})
        schema['properties']['x']['type']='string'
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate({'x':1},schema,'INVALID_COMMAND')
        self.assertEqual(protocol.validate({'x':'yes'},schema,'INVALID_COMMAND'),{'x':'yes'})
