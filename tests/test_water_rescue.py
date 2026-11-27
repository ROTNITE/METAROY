from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from controller.baselines import FixedWorkflow, Job
from controller.problem_builder import ProblemBuilder
from controller.water_rescue import (WaterRoute, failed_carriers, reserve_shore, water_command,
                                     water_intercept_opportunity, water_pickup_ready, water_route_changed)
from environment.config import Settings
from environment.layout import generate, public_map
from test_problem_builder import public_state


class WaterRescueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        city = generate(Settings.parse({'city_revision': '2.3', 'boats': 2, 'people': 4}))
        cls.announced = public_map(city)

    def test_water_search_launches_one_boat_from_its_declared_slip(self):
        state = public_state()
        state['metaevolution']['announced_map'] = self.announced
        state['targets'] = []
        sample = state['agents'][1]
        state['agents'] = []
        for spawn in self.announced['watercraft']['spawns']:
            boat = deepcopy(sample)
            boat.update(id=spawn['id'], kind='surface_water', position=spawn['position'])
            boat['physical']['watercraft'] = {'moored': True, 'hydrostatic_draft_m': .35}
            state['agents'].append(boat)
        request = FixedWorkflow().decide(state)
        self.assertEqual(len(request['commands']), 1)
        command = request['commands'][0]
        self.assertEqual(command['agent_id'], state['agents'][0]['id'])
        self.assertEqual(command['action'], 'follow_route')
        self.assertEqual(command['parameters']['waypoints'][0][:2], [-76., 55.])

    def test_wet_target_is_not_assigned_to_a_ground_team(self):
        state = public_state()
        state['targets'][0]['access_medium'] = 'surface_water'
        builder = ProblemBuilder(state['metaevolution']['announced_map'])
        self.assertEqual([target.id for target in builder.rescue(state, {}).targets], ['second:no-prefix'])

    def test_active_marine_route_changes_only_for_fresh_displacement(self):
        job = Job('search_water', 'drifting', water_intercept=(20., 21., (-76., 46.)))
        target = {'id': 'drifting', 'position': [-74., 44., 1.],
                  'access_medium': 'surface_water', 'evacuation_state': 'waiting'}
        known = {target['id']: target}
        target['observed_at'] = 20.
        self.assertFalse(water_route_changed(job, known, 25.))
        target['observed_at'] = 23.
        self.assertFalse(water_route_changed(job, known, 23.))
        self.assertFalse(water_route_changed(job, known, 30.))
        self.assertTrue(water_route_changed(job, known, 24.))
        target['access_medium'] = 'ground'
        self.assertFalse(water_route_changed(job, known, 24.))

    def test_water_path_rejects_unknown_shallow_ground(self):
        route = WaterRoute(self.announced, {'time': 0}, [], .35)
        self.assertEqual(route.route((-76., 45., .5), (-76., 38.5, .5)), [[-76., 38.5, .5]])
        self.assertIsNone(route.route((-76., 45., .5), (-45., 45., .5)))

    def test_intercept_can_interrupt_a_slip_route_after_launch(self):
        boat = {'position': [-76., 57., 1.], 'physical': {'watercraft': {'moored': False}}}
        job = Job('search_water')
        target = {'id': 'seen', 'position': [-75., 48., .6], 'observed_at': 8.,
                  'status': 'suspected', 'access_medium': 'surface_water', 'evacuation_state': 'waiting'}
        self.assertTrue(water_intercept_opportunity(boat, job, {'seen': target}, 9.))
        self.assertFalse(water_intercept_opportunity(boat, job, {'seen': target}, 12.))
        boat['physical']['watercraft']['moored'] = True
        self.assertFalse(water_intercept_opportunity(boat, job, {'seen': target}, 9.))

    def test_pickup_requires_a_settled_bow_and_safe_outreach(self):
        boat = {'position': [-76., 46., .9], 'physical': {'watercraft': {},
                'velocity_m_s': [.15,0,0], 'rotation_degrees': [0,0,-90]}}
        target = {'position': [-76.,42.5,.6], 'status': 'confirmed', 'velocity_m_s': [.2,0,0]}
        self.assertTrue(water_pickup_ready(boat, target))
        boat['physical']['rotation_degrees'][2]=0
        self.assertFalse(water_pickup_ready(boat, target))
        boat['physical']['rotation_degrees'][2]=-90
        boat['physical']['watercraft']['yaw_rate_degrees_s']=15
        self.assertFalse(water_pickup_ready(boat, target))

    def test_settled_pickup_from_a_carrier_uses_verified_hoist_clearance(self):
        boat = {'position': [-76., 46., .9], 'physical': {'watercraft': {},
                'velocity_m_s': [.02, 0., 0.], 'rotation_degrees': [0., 0., -90.]}}
        target = {'position': [-74.64, 42.5, .6], 'status': 'confirmed',
                  'evacuation_state': 'waiting', 'velocity_m_s': [.05, 0., 0.]}
        self.assertFalse(water_pickup_ready(boat, target))
        target['supported_by_agent'] = 'disabled-vessel'
        self.assertTrue(water_pickup_ready(boat, target))
        target['position'][0] = -74.54
        self.assertFalse(water_pickup_ready(boat, target))

    def test_drifting_carrier_requires_fresh_inspection_before_pickup(self):
        boat = {'id': 'reserve', 'kind': 'surface_water', 'status': 'active', 'position': [-76., 46., .9],
                'physical': {'watercraft': {'moored': False}, 'rotation_degrees': [0., 0., -90.]},
                'available_actions': [{'action': 'inspect'}, {'action': 'pickup'}]}
        target = {'id': 'patient', 'position': [-75.5, 42.5, .6], 'status': 'confirmed',
                  'observed_at': 5., 'evacuation_state': 'waiting', 'access_medium': 'surface_water',
                  'supported_by_agent': 'disabled-vessel'}
        workflow = FixedWorkflow()
        job = Job('search_water', target['id'])
        workflow.jobs[boat['id']] = job
        observation = {'time': 10., 'agents': [boat]}
        known = {target['id']: target}
        command, phase = water_command(workflow, observation, self.announced, boat, job, known)
        self.assertEqual((command['action'], phase), ('inspect', 'search_water'))
        workflow.failed_inspections[(boat['id'], target['id'])] = [job.inspection[1:]]
        self.assertEqual(water_command(workflow, observation, self.announced, boat, job, known), (None, None))
        target['observed_at'] = 9.5
        command, phase = water_command(workflow, observation, self.announced, boat, job, known)
        self.assertEqual((command['action'], phase), ('pickup', 'shore'))
        # The patient may have stayed in the water when the first boat failed.
        # The old pose stays eligible for inspection, but cannot author a lift.
        target.pop('supported_by_agent')
        workflow.failed_inspections.clear()
        workflow.reconfirm_after[target['id']] = 9.75
        command, phase = water_command(workflow, observation, self.announced, boat, job, known)
        self.assertEqual((command['action'], phase), ('inspect', 'search_water'))

    def test_shore_reservation_forecasts_both_legs_and_releases_failed_boat(self):
        state = public_state()
        state['time']=10.
        rover = next(a for a in state['agents'] if a['kind']=='ground')
        rover.update(status='active',position=[40.,10.,.44],communication={'reachable':True},
                     physical={'unlimited_energy':True})
        boat = {'id':'boat','kind':'surface_water','status':'busy','position':[-76.,43.,.65],
                'freshness':{'position':{'observed_at':9.5}},
                'physical':{'cargo_target_id':'patient','unlimited_energy':True,'watercraft':{'moored':False}}}
        state['agents']=[rover,boat]
        workflow=FixedWorkflow()
        workflow.jobs[rover['id']]=Job('choose')
        with patch.object(workflow,'_planned_route',return_value=SimpleNamespace(distance_m=73.)):
            reservation=reserve_shore(workflow,state,self.announced,{}, {})
        self.assertEqual((reservation['boat_id'],reservation['rover_id']),('boat',rover['id']))
        self.assertGreater(reservation['handover_eta_s'],reservation['rover_to_pier_s'])
        self.assertGreater(reservation['rover_estimated_wh'],0)
        workflow.shore_transfer=reservation
        boat['status']='failed'
        self.assertIsNone(reserve_shore(workflow,state,self.announced,{}, {}))

    def test_failed_boat_recovery_uses_delivered_pose_without_reassigning_passenger(self):
        boat={'id':'old','kind':'surface_water','status':'failed','position':[-76.,29.,.36]}
        patient={'evacuation_state':'loaded','supported_by_agent':'old','position':[-76.,44.,1.]}
        self.assertEqual(failed_carriers([boat],{'patient':patient},set()),[('patient',boat['position'])])
        patient['evacuation_state']='waiting'
        self.assertEqual(failed_carriers([boat],{'patient':patient},set()),[('patient',boat['position'])])
        self.assertFalse(failed_carriers([boat],{'patient':patient},{'patient'}))
        boat['status']='offline'
        self.assertFalse(failed_carriers([boat],{'patient':patient},set()))

    def test_recovery_route_uses_bow_outreach_before_touching_failed_hull(self):
        boat = {'id': 'reserve', 'kind': 'surface_water', 'position': [-68., 38., .36],
                'physical': {'watercraft': {'moored': False, 'hydrostatic_draft_m': .35}},
                'available_actions': [{'action': 'follow_route'}]}
        failed = {'id': 'carrier', 'kind': 'surface_water', 'status': 'failed',
                  'position': [-76., 38., .36]}
        target = {'id': 'patient', 'status': 'confirmed', 'position': [-76., 38., .9],
                  'observed_at': 9., 'velocity_m_s': [0., 0., 0.], 'supported_by_agent': failed['id'],
                  'access_medium': 'surface_water', 'evacuation_state': 'waiting'}
        job = Job('search_water', target['id'])
        workflow = FixedWorkflow()
        workflow.jobs = {boat['id']: job}
        observation = {'time': 10., 'agents': [boat, failed]}
        with patch('controller.water_rescue.WaterRoute') as water:
            planner = water.return_value
            planner.route.side_effect = lambda start, end: [list(end)]
            planner.clear.side_effect = lambda start, end: 4. <= abs(end[0] - failed['position'][0]) <= 4.4
            command, phase = water_command(workflow, observation, self.announced, boat, job, {'patient': target})
            self.assertIsNotNone(command)
            job.blocked_water_approaches[job.water_approach] = 55.
            blocker = {'kind': 'box', 'min': [-74., 34., 0.], 'max': [-71., 36., 1.]}
            workflow.local_obstacles[boat['id']] = [blocker]
            retry, _ = water_command(workflow, observation, self.announced, boat, job, {'patient': target})
            self.assertIn(blocker, water.call_args.args[2])
            boat['position'][0] = -72.8
            boat['physical']['rotation_degrees'] = [0., 0., 90.]
            planner.clear.side_effect = None
            planner.clear.return_value = True
            departure, _ = water_command(workflow, observation, self.announced, boat, job, {'patient': target})
            self.assertEqual(water.call_args.args[2], [blocker])
        self.assertEqual(phase, 'search_water')
        end = command['parameters']['waypoints'][-1]
        self.assertAlmostEqual(end[0], -71.88)
        self.assertEqual(end[1:], [38., .36])
        self.assertAlmostEqual(retry['parameters']['waypoints'][-1][0], -80.12)
        self.assertAlmostEqual(departure['parameters']['waypoints'][-1][0], -67.8)
        self.assertEqual(job.target, 'patient')

    def test_loaded_boat_leaves_disabled_hull_before_routing_to_shore(self):
        boat = {'id': 'reserve', 'kind': 'surface_water', 'status': 'active',
                'position': [-73., 43., .36], 'available_actions': [{'action': 'follow_route'}],
                'physical': {'carrying': True, 'cargo_target_id': 'patient',
                             'watercraft': {'moored': False, 'hydrostatic_draft_m': .35}}}
        carrier = {'id': 'disabled', 'kind': 'surface_water', 'status': 'failed', 'position': [-76., 43., .33]}
        workflow = FixedWorkflow()
        job = Job('shore', 'patient')
        observation = {'time': 175., 'agents': [boat, carrier]}
        with patch('controller.water_rescue.WaterRoute') as water:
            water.return_value.clear.return_value = True
            command, phase = water_command(workflow, observation, self.announced, boat, job, {})
            self.assertEqual(water.call_args.args[2], [])
            water.return_value.route.assert_not_called()
        self.assertEqual(command, {'agent_id': 'reserve', 'action': 'follow_route',
                                   'parameters': {'waypoints': [[-68., 43., .36]]}})
        self.assertEqual(phase, 'shore')

    def test_shore_report_correlates_unknown_disembark(self):
        state = public_state()
        workflow = FixedWorkflow()
        workflow.reset_episode(state['episode_id'])
        boat = state['agents'][1]
        boat['freshness'] = {'position': {'observed_at': 15}}
        target = state['targets'][0]
        job = Job('shore', target['id'], pending='dock', next_phase='search_water', action='disembark',
                  acknowledgement_unknown_at=12)
        self.assertIsNone(workflow._reconcile(boat, job, {target['id']: target}))
        target.update(support='shore_platform', evacuation_state='waiting')
        self.assertEqual(workflow._reconcile(boat, job, {target['id']: target})['outcome'], 'observed_complete')
        self.assertEqual(job.phase, 'search_water')
        self.assertIsNone(job.target)

    def test_a_boat_already_underway_keeps_the_nearby_job(self):
        state = public_state()
        state['metaevolution']['announced_map'] = self.announced
        state['targets'] = []
        sample = state['agents'][1]
        state['agents'] = []
        for spawn in self.announced['watercraft']['spawns']:
            boat = deepcopy(sample)
            boat.update(id=spawn['id'], kind='surface_water', position=spawn['position'])
            boat['physical']['watercraft'] = {'moored': True, 'hydrostatic_draft_m': .35}
            state['agents'].append(boat)
        policy = FixedWorkflow()
        first = policy.decide(state)
        self.assertEqual(first['commands'][0]['agent_id'], state['agents'][0]['id'])
        state['agents'][0]['position'] = [-76., 51., .36]
        state['agents'][0]['physical']['watercraft']['moored'] = False
        state['time'] = 10.
        state['targets'] = [{'id':'found-at-sea', 'position':[-76.,41.,.37], 'status':'suspected',
                             'access_medium':'surface_water', 'evacuation_state':'waiting'}]
        self.assertEqual(policy.decide(state)['commands'], [])
