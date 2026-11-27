"""Evidence must fail closed on explicit identity conflicts and bad journal records."""
from copy import deepcopy
import unittest

from tools.goal_evidence import GoalEvidence
from tools.run_interop import Evidence


class GoalScopeTests(unittest.TestCase):
    def setUp(self):
        self.caps={'extensions':['metaroy.action_semantics.v1'],
                   'action_semantics':{'rescue':{'semantic_id':'rescue','effect':'evacuated'}}}
        self.decision={'episode_id':'e','request_id':'r','decision_id':'d','time':0,
                       'commands':[{'agent_id':'a','action':'rescue','parameters':{'target_id':'p'}}]}
        self.receipt={'decision_id':'d','command_index':0,'agent_id':'a','action':'rescue',
                      'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'}
        self.outcome={'decision_id':'d','command_index':0,'agent_id':'a','effect':'evacuated',
                      'outcome_id':'out','target_id':'p'}

    def test_explicit_foreign_episode_cannot_be_relabelled(self):
        goals=GoalEvidence(self.caps)
        goals.record_decision(self.decision)
        goals.record_outcome('e',self.outcome)
        for receipt in ({**self.receipt,'episode_id':'other'},{**self.receipt,'episode_id':None},None):
            goals.record_receipt('e',receipt)
        self.assertEqual(goals.summary()['evacuated'],0)
        goals.record_receipt('e',self.receipt)
        self.assertEqual(goals.summary()['evacuated'],1)
        goals.record_receipt('e',{**self.receipt,'episode_id':'other','execution_status':'failed'})
        self.assertEqual(goals.summary()['evacuated'],1)

    def test_request_reuse_invalidates_previously_collected_goal(self):
        for evidence in (GoalEvidence(self.caps),Evidence(self.caps)):
            def decision(value):
                if isinstance(evidence,GoalEvidence):
                    evidence.record_decision(value)
                else:
                    evidence.record('decision',{'decision':value})
            decision(self.decision)
            if isinstance(evidence,GoalEvidence):
                evidence.record_receipt('e',self.receipt)
                evidence.record_outcome('e',self.outcome)
            else:
                evidence.record('step_response',{'episode_id':'e','request_id':'r','time':1,
                    'command_results':[self.receipt]})
                evidence.record('physical_outcome',{'episode_id':'e','payload':self.outcome})
            self.assertEqual(evidence.summary()['evacuated'],1)
            decision({**self.decision,'decision_id':'different'})
            self.assertEqual(evidence.summary()['evacuated'],0)
            decision(self.decision)
            self.assertEqual(evidence.summary()['evacuated'],0)

    def legacy(self,receipt):
        evidence=Evidence()
        evidence.record('observation',{'episode_id':'e','time':0,'targets':[{'id':'p','status':'confirmed'}]})
        evidence.record('decision',{'decision':self.decision})
        evidence.record('step_response',{'episode_id':'e','request_id':'r','time':1,
            'observation':{'episode_id':'e','request_id':'r','time':1,'targets':[{'id':'p','status':'rescued'}]},
            'command_results':[receipt]})
        return evidence.summary()

    def test_legacy_transition_requires_consistent_explicit_identifiers(self):
        legacy={'agent_id':'a','action':'rescue','status':'applied'}
        self.assertEqual(self.legacy(legacy)['correlated_public_target_count'],1)
        self.assertEqual(self.legacy(dict(legacy,decision_id='d',request_id='r',command_index=0))['correlated_public_target_count'],1)
        for change in ({'episode_id':'other'},{'decision_id':'other'},{'request_id':'other'},
                       {'command_index':1},{'command_index':False},{'delivery_status':'lost'},
                       {'execution_status':'failed'},{'validation_status':'rejected'}):
            with self.subTest(change=change):
                self.assertEqual(self.legacy(dict(legacy,**change))['correlated_public_target_count'],0)

    def test_bad_optional_journal_fields_do_not_manufacture_evidence(self):
        evidence=Evidence(self.caps)
        evidence.record('decision',{'decision':self.decision})
        base={'episode_id':'e','request_id':'r','time':1}
        for change in ({'time':'one'},{'observation':[]},{'events':None},{'command_results':{}},
                       {'events':[None,{'type':'target_state_changed','parameters':[]}]},
                       {'command_results':[None,{}, {'episode_id':'other',**self.receipt}]},
                       {'events':[{'type':'target_state_changed','source':'a','targets':['p'],
                           'parameters':{'evacuation_state':'evacuated','decision_id':'d','command_index':0}}]}):
            evidence.record('step_response',dict(base,**change))
        evidence.record('physical_outcome',{'episode_id':'e','payload':None})
        evidence.record('decision',{'decision':None})
        result=evidence.summary()
        self.assertEqual(result['evacuated'],0)
        self.assertEqual(result['confirmed_command_receipts'],[])
        self.assertEqual(result['correlated_public_target_count'],0)
