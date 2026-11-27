from copy import deepcopy
import unittest
from environment.fire_failure import append_fire_failures, expected_failure, valid_fire_failure


class FireFailureTests(unittest.TestCase):
    def setUp(self):
        self.house = {'id':'building-7','variant':2,'floors':3}
        self.fire = {'id':'fire-1','kind':'fire','time':30.,'source':[50.,55.,1.05],
                     'source_object_id':'building-7','parent_id':None,'spread':False,
                     'cells':[[10,11],[11,11],[10,12],[13,13]],'expires_at':210.,
                     'visibility_expires_at':255.,'rejection':None,'smoke_emission_height_m':10.}

    def test_exact_consequence_and_unchanged_parent(self):
        parent = deepcopy(self.fire)
        tape = {'events':[self.fire]}
        append_fire_failures(tape, {'buildings':[self.house]}, 480.)
        self.assertEqual(tape['events'][0], parent)
        child = tape['events'][1]
        self.assertEqual(child['time'],95.)
        self.assertEqual(child['parent_id'], 'fire-1')
        self.assertEqual(child['cells'], [[10,11],[10,12],[11,11]])
        self.assertAlmostEqual(child['smoke_emission_height_m'],1.2)
        self.assertTrue(valid_fire_failure(child, parent, self.house, 480.))

    def test_not_a_second_trial_or_a_spread_event(self):
        spread = {**self.fire,'id':'spread','spread':True,'time':68.}
        repeat = {**self.fire,'id':'fire-2','time':45.}
        tape = {'events':[repeat, spread, self.fire]}
        append_fire_failures(tape, {'buildings':[self.house]}, 480.)
        self.assertEqual([x['parent_id'] for x in tape['events'] if x.get('cause')], ['fire-1'])

    def test_early_extinction_horizon_ruins_and_rejection(self):
        self.assertIsNone(expected_failure({**self.fire,'expires_at':95.}, self.house,480.))
        self.assertIsNone(expected_failure(self.fire,self.house,95.))
        self.assertIsNone(expected_failure(self.fire,{**self.house,'variant':4},480.))
        self.assertIsNone(expected_failure({**self.fire,'rejection':'no_surface'},self.house,480.))
        self.assertIsNone(expected_failure({**self.fire,'cells':[[13,13]]},self.house,480.))

    def test_rejects_tampered_consequences(self):
        child=expected_failure(self.fire,self.house,480.)
        for key,value in [('parent_id','bad'),('time',94.),('source_object_id','other'),
                          ('source',[70.,70.,0.]),('cells',[[0,0]]),('spread',True),('expires_at',100.)]:
            with self.subTest(key=key):
                self.assertFalse(valid_fire_failure({**child,key:value},self.fire,self.house,480.))
        self.assertTrue(valid_fire_failure({**child,'cells':list(reversed(child['cells']))},self.fire,self.house,480.))

    def test_reproducibility_and_damage_sensitive_delay(self):
        intact={**self.house,'variant':0}
        self.assertEqual(expected_failure(self.fire,intact,480.)['time'],140.)
        self.assertEqual(expected_failure(self.fire,self.house,480.), expected_failure(deepcopy(self.fire),deepcopy(self.house),480.))
