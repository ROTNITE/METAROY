"""Regression tests for the portable public boundary and M5 evidence."""
from copy import deepcopy
from unittest import TestCase

from conformance.reference_environment import Trial
from contracts.protocol import ProtocolError, validate_observation
from controller.client import EnvironmentClient, TransportFailure
from controller.public_core import PublicCore
from tools.goal_evidence import GoalEvidence


class PublicBoundary(TestCase):
    def frame(self):
        trial = Trial('A')
        return trial.reset({'request_id': 'new', 'seed': 17,
                            'mission': {'id': 'search_and_rescue', 'parameters': {}}})['observation']

    def test_explicit_unknown_semantic_cannot_shadow_mapped_move(self):
        frame = self.frame()
        trial = Trial('A')
        caps = trial.capabilities()
        caps['extensions'] = ['metaroy.action_semantics.v1']
        caps['supported_actions']['navigate'] = deepcopy(caps['supported_actions']['move'])
        caps['action_semantics'] = {'move': {'semantic_id': 'vendor.other'},
                                    'navigate': {'semantic_id': 'move'}}
        core = PublicCore(caps, mode='strict')
        frame['agents'][0]['available_actions'] = [{'action': 'move'}]
        self.assertEqual(core.view(frame)['agents'][0]['available_actions'], [])
        self.assertEqual(frame['agents'][0]['available_actions'], [{'action': 'move'}])
        frame['agents'][0]['available_actions'] = [{'action': 'navigate'}]
        self.assertEqual(core.view(frame)['agents'][0]['available_actions'], [{'action': 'move'}])
        self.assertEqual(core.wire_action('move'), 'navigate')

    def test_unknown_semantic_without_alias_is_not_exposed(self):
        caps = Trial('A').capabilities()
        caps['extensions'] = ['metaroy.action_semantics.v1']
        caps['action_semantics'] = {'move': {'semantic_id': 'vendor.other'}}
        self.assertNotIn('move', [a['action'] for a in
                         PublicCore(caps).view(self.frame())['agents'][0]['available_actions']])

    def test_infrastructure_resource_bounds(self):
        for resource in ({'id':'energy','minimum':.7,'maximum':1.,'value':.1,'unit':'normalized'},
                         {'id':'other','minimum':0.,'maximum':1.,'value':.5,'unit':'normalized'}):
            frame = self.frame()
            frame['infrastructure'][0]['resources'] = {'energy': resource}
            with self.subTest(resource=resource), self.assertRaises(ProtocolError):
                validate_observation(frame)

    def test_malformed_or_future_freshness_is_normative_error(self):
        for value in ([], {'position': 1}, {'position': {'observed_at': 1}},
                      {'position': {'received_at': 1}}, {'position': {'observed_at': True}}):
            frame = self.frame()
            frame['agents'][0]['freshness'] = value
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_observation(frame)

    def test_unknown_extension_does_not_change_base_frame(self):
        frame = self.frame()
        frame['future_extension'] = {'arbitrary': ['value']}
        self.assertIs(validate_observation(frame), frame)

    def test_episode_error_without_details_stays_transport_failure(self):
        frame = self.frame()
        response = {'protocol_version':'1.0','episode_id':'different','request_id':frame['request_id'],
                    'time':frame['time'],'status':'running','observation':frame}
        with self.assertRaises(TransportFailure) as result:
            EnvironmentClient().episode_response(response)
        self.assertTrue(result.exception.state_may_have_changed)


class OutcomeBoundary(TestCase):
    def setUp(self):
        self.evidence = GoalEvidence({'extensions':['metaroy.action_semantics.v1'],
            'action_semantics': {'transfer': {'semantic_id':'rescue','effect':'evacuated',
                                               'bindings':{'target_id':'person'}}}})
        self.decision = {'episode_id':'e','decision_id':'d','commands':[
            {'agent_id':'a','action':'transfer','parameters':{'person':'p'}}]}
        self.receipt = {'decision_id':'d','command_index':0,'agent_id':'a','action':'transfer',
                        'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'}
        self.outcome = {'decision_id':'d','command_index':0,'agent_id':'a',
                        'effect':'evacuated','outcome_id':'out','target_id':'p'}

    def test_wrong_target_never_counts(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e', self.receipt)
        self.evidence.record_outcome('e', {**self.outcome, 'target_id':'different'})
        self.assertEqual(self.evidence.summary()['evacuated'], 0)

    def test_out_of_order_records_and_replay(self):
        self.evidence.record_outcome('e', self.outcome)
        self.evidence.record_receipt('e', self.receipt)
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e', self.receipt)
        self.evidence.record_outcome('e', self.outcome)
        self.assertEqual(self.evidence.summary()['evacuated'], 1)

    def test_conflicting_decision_replay_is_not_evidence(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e', self.receipt)
        self.evidence.record_outcome('e', self.outcome)
        changed = deepcopy(self.decision)
        changed['commands'][0]['parameters']['person'] = 'another'
        self.evidence.record_decision(changed)
        self.assertEqual(self.evidence.summary()['evacuated'], 0)

    def test_conflicting_outcome_replay_is_not_evidence(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e', self.receipt)
        self.evidence.record_outcome('e', self.outcome)
        self.evidence.record_outcome('e', {**self.outcome, 'target_id':'another'})
        self.assertEqual(self.evidence.summary()['evacuated'], 0)

    def test_cross_episode_does_not_match(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('other', self.receipt)
        self.evidence.record_outcome('e', self.outcome)
        self.assertEqual(self.evidence.summary()['evacuated'], 0)
