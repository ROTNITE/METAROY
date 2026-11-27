from copy import deepcopy
from unittest import TestCase

from contracts.protocol import ProtocolError, validate, validate_observation
from conformance.reference_environment import Trial
from controller.public_core import PublicCore
from controller.public_geometry import geometry, segments
from tools.goal_evidence import GoalEvidence


class DeclaredWireProfile(TestCase):
    def test_strict_rejects_and_declared_preserves_raw_data_and_writes_wire_arguments(self):
        peer=Trial('J')
        original=peer.reset({'request_id':'new','seed':13,'mission':{'id':'search_and_rescue','parameters':{}}})['observation']
        reference=deepcopy(original)
        caps=peer.capabilities()
        with self.assertRaises(ProtocolError):
            validate_observation(PublicCore(caps,mode='strict').wire_observation(original))
        audit=[]
        adapter=PublicCore(caps,mode='declared',audit=audit.append)
        normalized=validate_observation(adapter.wire_observation(original))
        view=adapter.view(normalized)
        self.assertEqual(view['agents'][0]['position'],[8.,8.,14.])
        self.assertEqual(view['agents'][0]['resources']['energy']['id'],'energy')
        self.assertIn({'action':'move'},view['agents'][0]['available_actions'])
        self.assertEqual(adapter.wire_action('move'),'flypoint-45')
        self.assertEqual(adapter.command_parameters('move',{'target':[5.,7.,13.]}),{'destination':[500.,1300.,700.]})
        self.assertEqual(original,reference)
        self.assertTrue({'resource_id_from_key','short_available_actions','coordinate_view'}<={item['conversion'] for item in audit})

    def test_unresolved_resource_alias_remains_invalid(self):
        peer=Trial('J')
        frame=peer.reset({'request_id':'new','seed':13,'mission':{'id':'search_and_rescue','parameters':{}}})['observation']
        frame['agents'][0]['resources']['energy']['unit']='Wh'
        wire=PublicCore(peer.capabilities(),mode='declared').wire_observation(frame)
        with self.assertRaises(ProtocolError):
            validate_observation(wire)

    def test_local_schema_refs_and_structured_rejection(self):
        validate({'x':3},{'type':'object','properties':{'x':{'$ref':'#/$defs/number'}},
                          '$defs':{'number':{'type':'number'}}})
        with self.assertRaisesRegex(ProtocolError,'local JSON Schema reference'):
            validate({}, {'$dynamicRef':'https://example.invalid/core-schema.json'})


class VolumeGeometry(TestCase):
    def test_cylinder_distinguishes_identical_xy_at_two_heights(self):
        shape=geometry({'kind':'cylinder','center':[3.,3.,0.],'radius':1.,'z_min':0.,'z_max':2.})
        self.assertIsNotNone(shape.interval((0.,3.,1.),(6.,3.,1.)))
        self.assertIsNone(shape.interval((0.,3.,3.),(6.,3.,3.)))
        self.assertIsNotNone(shape.interval((0.,4.2,1.),(6.,4.2,1.),.25))

    def test_distinct_support_parts_leave_a_gap(self):
        shape=geometry({'kind':'union','parts':[
            {'kind':'box','min':[0,0,0],'max':[1,1,1]},
            {'kind':'box','min':[2,0,0],'max':[3,1,1]}
        ]})
        self.assertEqual(list(segments(shape,(0,.5,.5),(3,.5,.5))),[(0.,1/3),(2/3,1.)])


class GoalCorrelation(TestCase):
    def setUp(self):
        self.evidence=GoalEvidence({'extensions':['metaroy.action_semantics.v1'],
                                    'action_semantics':{'deliver-machine-07':{'effect':'evacuated'}}})
        self.decision={'episode_id':'episode-1','decision_id':'first','commands':[
            {'agent_id':'carrier','action':'deliver-machine-07'}]}
        self.receipt={'decision_id':'first','command_index':0,'agent_id':'carrier','action':'deliver-machine-07',
                      'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'}
        self.outcome={'decision_id':'first','command_index':0,'agent_id':'carrier','target_id':'person',
                      'effect':'evacuated','outcome_id':'physical-transfer'}

    def test_late_receipt_after_many_decisions_and_duplicates(self):
        self.evidence.record_decision(self.decision)
        for i in range(700):
            self.evidence.record_decision({**self.decision,'decision_id':str(i)})
        self.evidence.record_outcome('episode-1',self.outcome)
        self.evidence.record_receipt('episode-2',self.receipt)
        self.assertEqual(self.evidence.summary()['goal_status'],'unverified')
        self.evidence.record_receipt('episode-1',self.receipt)
        self.evidence.record_receipt('episode-1',self.receipt)
        self.evidence.record_outcome('episode-1',{**self.outcome,'outcome_id':'duplicate'})
        self.assertEqual(self.evidence.summary()['evacuated'],1)

    def test_receipt_without_physical_confirmation(self):
        self.evidence.record_decision(self.decision)
        self.evidence.record_receipt('episode-1',self.receipt)
        self.assertEqual(self.evidence.summary()['goal_status'],'unverified')
