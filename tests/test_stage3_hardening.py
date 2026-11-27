"""Regression tests for the portable public boundary and M5 evidence."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from conformance.reference_environment import Trial
from contracts.protocol import ProtocolError, validate_observation
from controller.client import EnvironmentClient, PreparedRequest, TransportFailure
from contracts.protocol import encode
from controller.public_core import PublicCore
from tools.goal_evidence import GoalEvidence


class ProfilingPrecision(TestCase):
    def test_clock_ticks_below_resolution_leave_cpu_speedup_unavailable(self):
        from tools.profile_protocol_validation import measure
        from tools.profile_public_geometry import benchmark

        with patch('time.process_time', return_value=0.):
            self.assertIsNone(benchmark(6, 10, 17)['cpu_speedup'])
            self.assertIsNone(measure(6, samples=2)['cpu_speedup'])


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
                         {'id':'other','minimum':0.,'maximum':1.,'value':1.5,'unit':'normalized'}):
            frame = self.frame()
            frame['infrastructure'][0]['resources'] = {'energy': resource}
            with self.subTest(resource=resource), self.assertRaises(ProtocolError):
                validate_observation(frame)

    def test_infrastructure_public_id_is_independent_of_map_key(self):
        frame=self.frame()
        frame['infrastructure'][0]['resources']={'energy':{'id':'resource','minimum':0.,'maximum':1.,
                                                          'value':.5,'unit':'normalized'}}
        self.assertIs(validate_observation(frame),frame)
        self.assertEqual(frame['infrastructure'][0]['resources']['energy']['id'],'resource')

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


    def test_self_consistent_wrong_reply_is_not_a_valid_step(self):
        frame = self.frame()
        wire = EnvironmentClient.prepare_step({'request_id':'sent','episode_id':frame['episode_id'],'time':0.})
        for request_id, episode_id, stamp, status in (
                ('wrong', frame['episode_id'], 1., 'running'),
                ('sent', 'wrong-episode', 1., 'running'),
                ('sent', frame['episode_id'], 0., 'running')):
            obs = {**frame,'request_id':request_id,'episode_id':episode_id,'time':stamp}
            response = {'protocol_version':'1.0','episode_id':episode_id,'request_id':request_id,
                        'time':stamp,'status':status,'observation':obs,
                        'command_results':[],'events':[],'metrics':{}}
            with self.subTest(request_id=request_id, episode_id=episode_id, stamp=stamp):
                with self.assertRaises(TransportFailure) as result:
                    EnvironmentClient().episode_response(response,step=True,request=wire)
                self.assertTrue(result.exception.state_may_have_changed)

    def test_matching_response_advances_time(self):
        frame = self.frame()
        wire = EnvironmentClient.prepare_step({'request_id':'sent','episode_id':frame['episode_id'],'time':0.})
        frame.update(time=1.,request_id='sent')
        response = {'protocol_version':'1.0','episode_id':frame['episode_id'],'request_id':'sent',
                    'time':1.,'status':'running','observation':frame,
                    'command_results':[],'events':[],'metrics':{}}
        self.assertEqual(EnvironmentClient().episode_response(response,step=True,request=wire),response)

    def test_reset_cannot_adopt_nonzero_or_uncorrelated_frame(self):
        frame = self.frame()
        wire = PreparedRequest('POST','/v1/environment/reset',encode({'request_id':'sent'}))
        for request_id, stamp in (('other',0.),('sent',1.)):
            obs = {**frame,'request_id':request_id,'time':stamp}
            response = {'protocol_version':'1.0','episode_id':frame['episode_id'],'request_id':request_id,
                        'time':stamp,'status':'running','observation':obs}
            with self.subTest(request_id=request_id,stamp=stamp),self.assertRaises(TransportFailure):
                EnvironmentClient().episode_response(response,request=wire)


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

class ReceiptBoundary(TestCase):
    def test_mismatched_action_does_not_confirm_custody(self):
        from controller.portable import PortablePolicy
        caps=Trial('A').capabilities()
        policy=PortablePolicy(caps,'nsga2_nsga2')
        decision={'decision_id':'d','episode_id':'e','commands':[{'agent_id':'a','action':'pickup','parameters':{'target_id':'p'}}],
                  'operation':'keep'}
        policy.proposed=(decision,[(('casualty','p'),('a','pickup',repr({'target_id':'p'})))])
        base={'decision_id':'d','command_index':0,'agent_id':'a','action':'handover',
              'execution_status':'completed','validation_status':'accepted','delivery_status':'delivered'}
        policy.observe_response({'time':1.,'command_results':[base]})
        self.assertNotIn('a',policy.cargo)
        self.assertIn('a',policy.pending)
        policy.proposed=({**decision,'decision_id':'next','commands':[]},[])
        policy.observe_response({'time':2.,'command_results':[{**base,'action':'pickup','delivery_status':'lost'}]})
        self.assertNotIn('a',policy.cargo)
        self.assertNotIn('a',policy.pending)


class DiagnosticBoundary(TestCase):
    def test_resource_failure_retains_machine_readable_details(self):
        frame=Trial('A').reset({'request_id':'reset','seed':17,
            'mission':{'id':'search_and_rescue','parameters':{}}})['observation']
        frame['agents'][0]['resources']['energy']['id']='wrong'
        result={'protocol_version':'1.0','episode_id':frame['episode_id'],
                'request_id':frame['request_id'],'time':frame['time'],
                'status':'running','observation':frame}
        with self.assertRaises(TransportFailure) as caught:
            EnvironmentClient().episode_response(result)
        self.assertEqual(caught.exception.code,'INVALID_REQUEST')
        self.assertEqual(caught.exception.details['rule'],'resource_id')
        self.assertEqual(caught.exception.details['resource_id'],'energy')
        self.assertEqual(caught.exception.details['declared_id'],'wrong')

    def test_offline_peer_ledger_and_startup_diagnostics(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from tools.peer_trial import offline_env, startup_diagnostic, summary
        with TemporaryDirectory() as path:
            work=Path(path)
            env=offline_env(work,18080)
            self.assertEqual(env['METARESCUE_LIVE_BUDGET'],str(work/'offline-ai-budget.json'))
            self.assertEqual(env['METARESCUE_OUTPUT'],str(work/'metarescue-output'))
            log=work/'peer.log'
            log.write_text("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'three' imported from /tmp/private.js\nsecret raw stdout",encoding='utf-8')
            self.assertEqual(startup_diagnostic(log),{'startup_error_codes':['ERR_MODULE_NOT_FOUND','MODULE_NOT_FOUND'],
                                                    'missing_dependency':'three'})
        result=summary({'verified_outcomes':[{'effect':'evacuated'}],'confirmed_command_receipts':[{},{}],
            'first_protocol_error':{'code':'INVALID_REQUEST','details':{'rule':'resource_id','resource_id':'energy','private':'do not copy'}}})
        self.assertEqual(result['verified_outcomes_count'],1)
        self.assertEqual(result['confirmed_command_receipts_count'],2)
        self.assertEqual(result['error_details'],{'rule':'resource_id','resource_id':'energy'})


class EvidenceContradictions(OutcomeBoundary):
    def test_terminal_failure_invalidates_conflicting_completion(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e',self.receipt)
        self.evidence.record_outcome('e',self.outcome)
        self.assertEqual(self.evidence.summary()['evacuated'],1)
        self.evidence.record_receipt('e',{**self.receipt,'execution_status':'failed'})
        self.assertEqual(self.evidence.summary()['evacuated'],0)

    def test_conflicting_non_goal_decision_content_invalidates_goal(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('e',self.receipt)
        self.evidence.record_outcome('e',self.outcome)
        self.evidence.record_decision({**self.decision,'operation':'safe_stop'})
        self.assertEqual(self.evidence.summary()['evacuated'],0)


class JournalScopeBoundary(TestCase):
    def collect(self, records):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from tools.run_interop import Evidence,public_logs
        from unittest.mock import Mock
        client=Mock()
        client.logs.return_value={'episode_id':'e','records':records,'next_cursor':None}
        evidence=Evidence()
        with TemporaryDirectory() as root:
            public_logs(client,'e',Path(root)/'journal.jsonl',evidence)
        return evidence.summary()

    def test_unscoped_metadata_not_evidence(self):
        report=self.collect([{'episode_id':None,'record_type':'startup','payload':{}}])
        self.assertEqual(report['journal_unscoped_metadata'],1)
        self.assertEqual(report['evacuated'],0)

    def test_foreign_payload_rejected(self):
        from contracts.protocol import ProtocolError
        with self.assertRaises(ProtocolError) as caught:
            self.collect([{'episode_id':'e','record_type':'decision','payload':{'episode_id':'other'}}])
        self.assertEqual(caught.exception.details['rule'],'journal_payload_scope')

    def test_unscoped_evidence_rejected(self):
        from contracts.protocol import ProtocolError
        with self.assertRaises(ProtocolError):
            self.collect([{'episode_id':None,'record_type':'physical_outcome','payload':{}}])
