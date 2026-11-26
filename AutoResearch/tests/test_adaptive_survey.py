import unittest
from copy import deepcopy
from unittest.mock import patch
from controller.adaptive_survey import AdaptiveSurveyPolicy
from controller.resource_predictor import ResourcePolicy
from environment.world import World, capabilities
from experiments.mission_flow.logistics_trial import SurveyPolicy
from experiments.mission_flow.benchmark_logistics import physical

class SurveyTests(unittest.TestCase):
    def policy(self,caps=None):
        return AdaptiveSurveyPolicy(caps or capabilities(),'nsga2_nsga2')
    def test_radius_uses_advertised_bound(self):
        self.assertEqual(self.policy()._observe_parameters(),{'radius':15})
        caps=deepcopy(capabilities());caps['supported_actions']['observe']['parameters_schema']['properties']['radius']['maximum']=12
        self.assertEqual(self.policy(caps)._observe_parameters(),{'radius':12})
    def test_no_expansion_without_declaration(self):
        for upper in (8,10,101,None):
            caps=deepcopy(capabilities());schema=caps['supported_actions']['observe']['parameters_schema']['properties']['radius']
            if upper is None:schema.pop('maximum')
            else:schema['maximum']=upper
            self.assertEqual(self.policy(caps)._observe_parameters(),ResourcePolicy(caps,'nsga2_nsga2')._observe_parameters())
    def test_no_extra_patrol_logic_without_wider_radius(self):
        caps=deepcopy(capabilities());caps['supported_actions']['observe']['parameters_schema']['properties']['radius']['maximum']=10
        p=self.policy(caps);p.scout_enabled=True
        with patch.object(ResourcePolicy,'_repeat_sector',return_value=('baseline','baseline')) as original:
            self.assertEqual(p._repeat_sector({},[],{},[],set(),[]),('baseline','baseline'));original.assert_called_once()
    def test_issued_observe_command_auditable(self):
        w=World(27);p=self.policy();d=p.decide(w.observation('first'))
        self.assertTrue(any(c['action']=='observe' and c['parameters']['radius']==15 for c in d['commands']))
        traces=[t for t in p.drain_traces() if t.get('kind')=='adaptive_survey']
        self.assertEqual(len(traces),1);self.assertEqual(traces[0]['decision_id'],d['decision_id'])
    def test_production_and_trial_same_outcome(self):
        for seed,stress,mixed,variable in ((112000,False,False,False),(112100,False,True,False),(112200,True,True,False),(112300,True,True,True)):
            actual=physical(seed,'range-late',stress,mixed,variable)
            with patch('experiments.mission_flow.benchmark_logistics.AdaptiveSurveyPolicy',lambda *a,**kw:SurveyPolicy(*a,survey='range-late',**kw)):
                original=physical(seed,'range-late',stress,mixed,variable)
            for k in ('saved','m1','energy','time','clipped','found','initial','error','rejections','reserve_violations'):
                self.assertEqual(actual[k],original[k],(seed,k))

    def test_single_carrier_late_target_preserves_baseline(self):
        from contracts.protocol import encode
        from hashlib import sha256
        for seed in (17331,17332,17333):
            outcomes=[]
            for cls in (ResourcePolicy,AdaptiveSurveyPolicy):
                w=World(seed,'late_target',opaque=True);p=cls(capabilities(),'nsga2_nsga2')
                while w.status=='running':
                    d=p.decide(w.observation(str(w.time)))
                    p.observe_response(w.step(d,sha256(encode(d)).hexdigest(),capabilities()))
                outcomes.append((w.status,w.time,sum(t.custody==w.base for t in w.people.values())))
            self.assertEqual(outcomes[0][0],outcomes[1][0]);self.assertLessEqual(outcomes[1][1],outcomes[0][1]);self.assertEqual(outcomes[1][2],2)

    def test_inclusive_maximum_must_also_pass_full_schema(self):
        caps=deepcopy(capabilities());schema=caps['supported_actions']['observe']['parameters_schema']['properties']['radius']
        schema['enum']=[10,12]
        self.assertEqual(self.policy(caps)._observe_parameters(),{'radius':10.})
