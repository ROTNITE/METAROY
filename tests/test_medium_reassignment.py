"""Rescue reservations follow delivered access-medium changes, not private floods."""
from copy import deepcopy
import unittest

from controller.baselines import FixedWorkflow, Job
from test_problem_builder import public_state


class MediumReassignmentTests(unittest.TestCase):
    def fixture(self):
        state=public_state()
        state['time']=10.
        state['targets']=state['targets'][:1]
        state['metaevolution']['disaster_revision']=1
        target=state['targets'][0]
        target.update(access_medium='surface_water',observed_at=9.)
        rover=state['agents'][0]
        rover.update(position=[76.,10.,.47],freshness={'position':{'observed_at':9.5}})
        rover['physical'].update(at_dock=False,state='external_route',route_finished=False,active_command_id='old:0')
        policy=FixedWorkflow()
        policy.reset_episode(state['episode_id'])
        policy.jobs[rover['id']]=Job('approach',target['id'],pending='old',pending_index=0,action='follow_route')
        return state,target,rover,policy

    def test_inflight_ground_claim_waits_for_hold_ack(self):
        state,target,rover,policy=self.fixture()
        aid=rover['id'];job=policy.jobs[aid]
        decision=policy.decide(state)
        idx=next(i for i,c in enumerate(decision['commands']) if c['agent_id']==aid)
        self.assertEqual(decision['commands'][idx]['action'],'hold')
        self.assertEqual(job.target,target['id'])
        self.assertEqual(job.next_phase,'medium_recheck')
        self.assertFalse(any(c['agent_id']==aid for c in policy.decide(state)['commands']))
        policy.observe_response({'status':'running','command_results':[{
            'decision_id':decision['decision_id'],'agent_id':aid,'command_index':idx,
            'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'}]})
        rover['physical'].update(route_finished=True,state='idle')
        policy.decide(state)
        self.assertIsNone(job.target)
        self.assertEqual(job.phase,'choose')

    def test_no_switch_on_stale_future_or_legacy_report(self):
        state,target,rover,policy=self.fixture()
        job=policy.jobs[rover['id']]
        self.assertTrue(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))
        for stamp in (0.,11.):
            target['observed_at']=stamp
            self.assertFalse(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))
        target['observed_at']=9.
        rover['freshness']['position']['observed_at']=0.
        self.assertFalse(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))
        rover['freshness']['position']['observed_at']=9.
        state['metaevolution'].pop('disaster_revision')
        self.assertFalse(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))

    def test_never_reassign_actual_cargo_or_inflight_pickup(self):
        state,target,rover,policy=self.fixture()
        job=policy.jobs[rover['id']]
        for fields in ({'carrying':True},{'cargo_target_id':target['id']},{'state':'loading'},{'state':'unloading'}):
            other=deepcopy(rover);other['physical'].update(fields)
            self.assertFalse(policy._changed_rescue_medium(state,other,job,{target['id']:target}))
        job.action='pickup'
        self.assertFalse(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))

    def test_drained_target_releases_idle_boat_claim(self):
        state,target,rover,policy=self.fixture()
        target['access_medium']='ground'
        boat=deepcopy(rover);boat.update(id='boat',kind='surface_water')
        boat['physical'].update(route_finished=True,state='idle')
        job=Job('search_water',target['id'])
        policy.water_survey_requests['boat']=(10.,(0.,0.),(5.,5.),target['id'])
        self.assertTrue(policy._changed_rescue_medium(state,boat,job,{target['id']:target}))
        policy._release_medium_reservation(boat,job)
        self.assertIsNone(job.target)
        self.assertEqual(job.phase,'search_water')
        self.assertNotIn('boat',policy.water_survey_requests)

    def test_shore_coalition_keeps_intentional_waiting_claim(self):
        state,target,rover,policy=self.fixture()
        job=policy.jobs[rover['id']]
        job.phase='shore_stage'
        policy.shore_transfer={'rover_id':rover['id'],'boat_id':'carrier','target_id':target['id']}
        self.assertFalse(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))
        policy.shore_transfer=None
        self.assertTrue(policy._changed_rescue_medium(state,rover,job,{target['id']:target}))

    def test_lost_handover_ack_requires_the_matching_receipt(self):
        state,target,rover,policy=self.fixture()
        rover['physical'].update(state='idle',carrying=False)
        target['evacuation_state']='evacuated'
        for receipt,expected in (('medical:0','observed_complete'),
                                 ('other:0','target_resolved_without_matching_receipt'),
                                 (None,'target_resolved_without_matching_receipt')):
            target['medical_handover_command_id']=receipt
            job=Job('handover',target['id'],pending='medical',next_phase='park',
                    action='handover',acknowledgement_unknown_at=9.)
            evidence=policy._reconcile(rover,job,{target['id']:target},revised=True)
            self.assertEqual(evidence['outcome'],expected)
            self.assertEqual(job.phase,'park')
            self.assertIsNone(job.target)
        rover['physical']['carrying']=True
        job=Job('handover',target['id'],pending='medical',action='handover',acknowledgement_unknown_at=9.)
        self.assertIsNone(policy._reconcile(rover,job,{target['id']:target},revised=True))
        self.assertEqual(job.target,target['id'])

    def test_failed_cancel_retains_claim_and_retries(self):
        state,target,rover,policy=self.fixture()
        aid=rover['id'];job=policy.jobs[aid]
        first=policy.decide(state)
        idx=next(i for i,c in enumerate(first['commands']) if c['agent_id']==aid)
        policy.observe_response({'status':'running','command_results':[{
            'decision_id':first['decision_id'],'agent_id':aid,'command_index':idx,
            'validation_status':'accepted','delivery_status':'lost','execution_status':'failed'}]})
        self.assertEqual(job.target,target['id'])
        retry=policy.decide(state)
        self.assertEqual(next(c for c in retry['commands'] if c['agent_id']==aid)['action'],'hold')
        self.assertEqual(job.target,target['id'])
