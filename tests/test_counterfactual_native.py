from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from controller.baselines import Job
from controller.counterfactual_native import NativeCounterfactual
from controller.optimization import Trip
from controller.profiles import COUNTERFACTUAL, STANDARD
from tools.product_session import REQUEST_FORMAT, parse_request


def mobile(aid, kind, xy=(0., 0.), stamp=17.):
    return {
        'id': aid, 'kind': kind, 'position': [*xy, 0.3], 'status': 'active',
        'capabilities': ['sense'] if kind == 'aerial' else ['carry'],
        'constraints': [], 'communication': {'reachable': True},
        'available_actions': [{'action': action} for action in ('follow_route', 'pickup')],
        'freshness': {'position': {'observed_at': stamp}},
        'physical': {'capacity_wh': 100., 'remaining_wh': 95., 'reserve_wh': 10.,
                     'consumed_wh': 4., 'speed_limit_m_s': 5., 'climb_rate_m_s': 5.,
                     'descent_rate_m_s': 2.5, 'dock_position_m': [0., 0., 0.3]},
    }


def observation(now, *agents):
    return {'mission': {'termination': {'horizon': 100.}, 'constraints': [], 'directives': []},
            'request_id': 'observation', 'time': now, 'agents': list(agents), 'targets': [], 'constraints': []}


class ProfileSelection(unittest.TestCase):
    def test_existing_presets_and_new_profile(self):
        request = {'format': REQUEST_FORMAT, 'settings': {}, 'controller': {'scout_patrol': 'net65'}}
        self.assertEqual(parse_request(request)['controller']['controller_profile'], STANDARD)
        request['controller'] = {'controller_profile': COUNTERFACTUAL}
        config = parse_request(request)['controller']
        self.assertEqual(config['scout_patrol'], 'net65')
        self.assertEqual(config['policy'], 'nsga2_nsga2')
        request['controller']['scout_patrol'] = 'core'
        with self.assertRaisesRegex(ValueError, 'Net65'):
            parse_request(request)


class NativeCounterfactualTests(unittest.TestCase):
    def setUp(self):
        self.scout = mobile('drone', 'aerial')
        self.rover = mobile('rover', 'ground')
        data = {'old': (45., 20.), 'new': (4., 3.)}
        nav = SimpleNamespace(sector_visibility=lambda xy: 1., ground_z=lambda x, y: 0.3)
        self.builder = SimpleNamespace(positions=data, nav=nav, altitudes={'drone': 20.},
                                       survey_rescue_eta=lambda agent, target, remaining: 10.,
                                       observed_rescue_trip=lambda agent, target: Trip(25., 8.))
        self.policy = NativeCounterfactual()

    @patch('controller.counterfactual_native.flight_estimator')
    def test_option_preserves_coverage_and_uses_feasible_native_leg(self, estimate):
        def leg(origin, goal):
            return (2., 1.) if goal[0] <= 4. else (4., 9.)
        estimate.return_value = leg
        frame = observation(17., self.scout, self.rover)
        choice, entry = self.policy.survey(frame,self.builder,self.scout,{'old': 3.},set(),'old',0.)
        self.assertEqual(choice[0], 'new')
        self.assertIn(choice[1], ('high', 'wave', 'economy'))
        self.assertNotIn('pause', entry['alternatives'])
        self.assertEqual(entry['until'], 57.)
        self.assertEqual(entry['observation_id'], frame['request_id'])
        self.assertEqual(len(self.policy.phases), 1)
        frame['mission']['directives'] = [{'action': 'hold'}]
        self.assertEqual(self.policy.survey(frame,self.builder,self.scout,{'old': 3.},set(),'old',0.),(None,None))
        self.assertEqual(self.policy.options,{})

    @patch('controller.counterfactual_native.flight_estimator')
    def test_option_needs_return_energy(self, estimate):
        def leg(origin, goal):
            return (3., 90.) if origin[0] == 4. else (3., 3.)
        estimate.return_value = leg
        choice, entry = self.policy.survey(observation(17., self.scout, self.rover), self.builder,
                                          self.scout, {'old': 3.}, set(), 'old', 0.)
        self.assertIsNone(choice)
        self.assertEqual(set(entry['alternatives']), {'continue'})

    def test_defer_only_initial_expensive_trip_with_fresh_scout(self):
        self.scout['freshness']['position']['observed_at'] = 7.
        self.rover['freshness']['position']['observed_at'] = 7.
        self.rover['physical']['remaining_wh'] = 23.
        frame = observation(7., self.scout, self.rover)
        target = {'position': [18., 3., .3]}
        self.assertTrue(self.policy.defer(frame,self.builder,self.rover,target,0.,{}))
        self.assertFalse(self.policy.defer(frame,self.builder,self.rover,target,0.,{'old': 5.}))
        frame['time'] = 12.
        self.assertFalse(self.policy.defer(frame,self.builder,self.rover,target,0.,{}))

    def test_reconcile_exact_arrival_keep_pickup_and_old_receipt_distinct(self):
        frame = observation(17., self.rover)
        job = Job('approach',target='person-1',pending='decision-a',pending_index=2,
                  action='follow_route',next_phase='pickup')
        jobs = {'rover': job}
        result = {'agent_id':'rover','decision_id':'decision-a','command_index':2,'execution_status':'in_progress',
                  'validation_status':'accepted','delivery_status':'delivered','updated_at':16.}
        self.rover['physical'].update(state='idle',route_finished=True,active_command_id='decision-b:2')
        self.assertEqual(list(self.policy.reconcile_motion({'observation':frame,'command_results':[result]},jobs)),[])
        self.assertEqual(job.phase,'approach')
        self.rover['physical']['active_command_id'] = 'decision-a:2'
        self.rover['freshness']['position']['observed_at'] = 15.5
        self.assertEqual(list(self.policy.reconcile_motion({'observation':frame},jobs)),[])
        self.rover['freshness']['position']['observed_at'] = 17.
        events = list(self.policy.reconcile_motion({'observation':frame},jobs))
        self.assertEqual([item['type'] for item in events], ['native_counterfactual_arrival'])
        self.assertEqual((job.phase,job.pending,job.target),('pickup',None,'person-1'))
        job.pending, job.action, job.next_phase = 'decision-c', 'pickup', 'return'
        completed = {**result, 'execution_status':'completed','updated_at':18.}
        events = list(self.policy.reconcile_motion({'observation':frame,'command_results':[completed]},jobs))
        self.assertEqual([item['type'] for item in events], ['native_counterfactual_arrival_receipt'])
        self.assertEqual((job.phase,job.pending,job.target),('pickup','decision-c','person-1'))
