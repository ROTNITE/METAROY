"""No private event model is needed by these marine planner regressions."""
from copy import deepcopy
import math
import unittest

from controller.water_rescue import WaterRoute, reserve_shore, water_command
from controller.baselines import FixedWorkflow, Job


def map_with_basin():
    return {'bounds': {'kind':'box','min':[-100,0,-12],'max':[1500,1500,90]},
            'hydrology': {'basin': {'min':[-90,10,-7],'max':[-50,50,2]},'initial_stage_m':2.}}


def reading(x,y,depth,seen=9.,received=10.):
    return {'id':f'surface-{x}-{y}','type':'water','observed_at':seen,'received_at':received,
            'geometry':{'type':'box','min':[x*5,y*5,-.15],'max':[x*5+5,y*5+5,3.]},
            'surface':{'depth_m':depth,'active':depth>0}}


def revised(zones=(),now=10.):
    return {'time':now,'zones':list(zones),'metaevolution':{'disaster_revision':1}}


class RevisedMarineTests(unittest.TestCase):
    def test_explicit_dry_and_stale_measurements_override_initial_basin(self):
        static=map_with_basin()
        self.assertTrue(WaterRoute(static,revised(),[],.35).deep(-72.5,22.5))
        dry=reading(-15,4,0.)
        self.assertFalse(WaterRoute(static,revised([dry]),[],.35).deep(-72.5,22.5))
        self.assertFalse(WaterRoute(static,revised([dry],now=35.),[],.35).deep(-72.5,22.5))
        wet=reading(-15,4,1.)
        self.assertFalse(WaterRoute(static,revised([wet],now=35.),[],.35).deep(-72.5,22.5))
        # The old profile retains exactly its former 20-second fallback.
        self.assertTrue(WaterRoute(static,{'time':35.,'zones':[dry]},[],.35).deep(-72.5,22.5))

    def test_future_and_conflicting_depths(self):
        static=map_with_basin()
        wet=reading(3,3,1.)
        dry=reading(3,3,0.)
        future=reading(3,3,2.,11.,11.)
        for rows in ([wet,dry,future],[future,dry,wet]):
            self.assertFalse(WaterRoute(static,revised(rows),[],.35).deep(17.5,17.5))
        self.assertTrue(WaterRoute(static,revised([dry,reading(3,3,1.,10.,10.)]),[],.35).deep(17.5,17.5))
        self.assertFalse(WaterRoute(static,revised([future]),[],.35).deep(17.5,17.5))

    def test_narrow_corner_and_debris(self):
        static=map_with_basin()
        wet=[reading(x,y,1.) for x in range(3,7) for y in range(3,5)]
        nav=WaterRoute(static,revised(wet),[],.35)
        self.assertTrue(nav.clear((17.5,17.5,1.),(32.5,17.5,1.)))
        self.assertFalse(nav.clear((17.5,15.2,1.),(32.5,15.2,1.)))
        obstacle={'id':'debris','type':'obstacle','received_at':10.,
                  'geometry':{'type':'box','min':[25.,15.,0.],'max':[30.,20.,2.2]},
                  'surface':{'active':True,'physical_debris':True}}
        blocked=WaterRoute(static,revised([*wet,obstacle]),[],.35)
        self.assertFalse(blocked.clear((17.5,17.5,1.),(32.5,17.5,1.)))
        future={**obstacle,'received_at':11.}
        self.assertTrue(WaterRoute(static,revised([*wet,future]),[],.35).clear((17.5,17.5,1.),(32.5,17.5,1.)))

    def test_diagonal_cannot_skip_a_thin_unknown_cell(self):
        static=map_with_basin()
        # A diagonal briefly passes through the (4,3) cell before crossing y=20.
        nav=WaterRoute(static,revised([reading(3,3,1.),reading(4,4,1.)]),[],.35)
        self.assertFalse(nav.clear((17.,17.,1.),(24.,23.,1.)))

    def test_long_serpentine_route_exceeds_legacy_search_budget(self):
        static=map_with_basin()
        cells={(x,y) for y in (5,10,15) for x in range(5,280)}
        cells.update((279,y) for y in range(5,11))
        cells.update((5,y) for y in range(10,16))
        rows=[reading(x,y,1.) for x,y in sorted(cells)]
        start,goal=(27.5,27.5,1.),(1397.5,77.5,1.)
        nav=WaterRoute(static,revised(rows),[],.35)
        path=nav.route(start,goal)
        self.assertIsNotNone(path)
        self.assertGreater(len(path),640)
        self.assertEqual(path[-1],list(goal))
        self.assertTrue(all(nav.clear(a,b) for a,b in zip([start,*path],path)))
        self.assertIsNone(WaterRoute(static,{'time':10.,'zones':rows},[],.35).route(start,goal))

    def test_missing_reserved_peer_does_not_crash(self):
        workflow=FixedWorkflow()
        workflow.shore_transfer={'boat_id':'gone','rover_id':'gone2','target_id':'person'}
        static={'watercraft':{'shore_station':{'ground_pickup':[0.,0.,0.],'boat_position_xy':[0.,0.]}},
                'base':{'min':[0,0,0],'max':[10,10,1]}}
        self.assertIsNone(reserve_shore(workflow,{'time':10.,'agents':[]},static,{},{}))

    def test_stale_water_target_is_inspected_not_lifted(self):
        workflow=FixedWorkflow()
        boat={'id':'reserve','kind':'surface_water','status':'active','position':[-76.,46.,.9],
              'physical':{'watercraft':{'moored':False},'rotation_degrees':[0.,0.,-90.]},
              'available_actions':[{'action':'inspect'},{'action':'pickup'}]}
        target={'id':'patient','position':[-76.,42.5,.6],'status':'confirmed','observed_at':1.,
                'evacuation_state':'waiting','access_medium':'surface_water'}
        job=Job('search_water',target['id']);workflow.jobs[boat['id']]=job
        static={**map_with_basin(),'watercraft':{'shore_station':{'id':'pier'}}}
        obs={**revised(),'agents':[boat]}
        command,_=water_command(workflow,obs,static,boat,job,{'patient':target})
        self.assertEqual(command['action'],'inspect')
        target['observed_at']=9.
        command,_=water_command(workflow,obs,static,boat,job,{'patient':target})
        self.assertEqual(command['action'],'pickup')
