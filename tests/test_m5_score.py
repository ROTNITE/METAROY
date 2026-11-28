from unittest import TestCase
from tools.m5_score import aggregate_m5, aggregate_tournament_m5


class M5Score(TestCase):
    controllers={'our-c':'ours','other-c':'other'}
    environments={'our-e':'ours','other-e':'other'}

    def assessment(self,c='our-c',e='other-e',q=.8,**extra):
        return {'controller_id':c,'environment_id':e,'q':q,
                'method_id':'agreed-rubric','evidence':['archive:abc'],**extra}

    def calculate(self,rows,method='agreed-rubric'):
        return aggregate_m5(self.controllers,self.environments,rows,method_id=method)

    def test_full_denominator_includes_diagonal(self):
        result=self.calculate([self.assessment(),self.assessment('other-c','our-e',.6)])
        self.assertAlmostEqual(result['value'],.35)
        self.assertEqual(result['denominator'],4)
        self.assertEqual(result['required_pairs'],2)
        self.assertFalse(result['official'])

    def test_missing_is_unknown_not_zero(self):
        result=self.calculate([self.assessment()])
        self.assertIsNone(result['value'])
        self.assertEqual(result['assessed_pairs'],1)
        self.assertEqual(result['missing_pairs'],[{'controller_id':'other-c','environment_id':'our-e'}])

    def test_real_zero_requires_evidence(self):
        result=self.calculate([self.assessment(q=0),self.assessment('other-c','our-e',0)])
        self.assertEqual(result['value'],0)
        self.assertEqual(result['status'],'calculated_from_supplied_q')

    def test_invalid_assessments(self):
        for row in (self.assessment(q=True),self.assessment(q=float('nan')),self.assessment(q=1.1),
                    self.assessment(e='our-e'),self.assessment(evidence=[]),self.assessment(method_id='different')):
            with self.subTest(row=row),self.assertRaises(ValueError):
                self.calculate([row])
        with self.assertRaises(ValueError):
            self.calculate([self.assessment(),self.assessment()])
        with self.assertRaises(ValueError):
            self.calculate([self.assessment()],None)

    def test_no_external_pair_cannot_verify(self):
        result=aggregate_m5({'c':'ours'},{'e':'ours'},[],method_id='agreed-rubric')
        self.assertIsNone(result['value'])
        self.assertEqual(result['required_pairs'],0)


    def test_tournament_directional_weights(self):
        rows=[self.assessment(q=.8),self.assessment('other-c','our-e',.2)]
        result=aggregate_tournament_m5(self.controllers,self.environments,rows,team_id='ours',method_id='agreed-rubric')
        self.assertAlmostEqual(result['value'],.62)
        self.assertEqual(result['outbound']['value'],.8)
        self.assertEqual(result['inbound']['value'],.2)
        self.assertFalse(result['official'])

    def test_tournament_missing_direction_is_unknown(self):
        result=aggregate_tournament_m5(self.controllers,self.environments,[self.assessment()],team_id='ours',method_id='agreed-rubric')
        self.assertIsNone(result['value'])
        self.assertIsNone(result['inbound']['value'])
        self.assertEqual(result['inbound']['required_pairs'],1)

    def test_tournament_unequal_direction_denominators(self):
        envs={**self.environments,'extra-e':'third'}
        rows=[self.assessment(q=.8),self.assessment(e='extra-e',q=.2),self.assessment('other-c','our-e',.6)]
        result=aggregate_tournament_m5(self.controllers,envs,rows,team_id='ours',method_id='agreed-rubric')
        self.assertAlmostEqual(result['value'],.53)
        self.assertEqual(result['outbound']['required_pairs'],2)
        self.assertEqual(result['inbound']['required_pairs'],1)

    def test_tournament_rejects_bad_provenance(self):
        with self.assertRaises(ValueError):
            aggregate_tournament_m5(self.controllers,self.environments,[self.assessment(evidence=[])],team_id='ours',method_id='agreed-rubric')
