"""Delivered local road support must change the actual subsequent route."""
import math
import unittest

from controller.baselines import FixedWorkflow
from controller.navigation import Box, NavigationMap, ObservedSurface
from test_problem_builder import public_state


class RoadSupportRecovery(unittest.TestCase):
    def setUp(self):
        self.map=public_state()['metaevolution']['announced_map']
        self.workflow=FixedWorkflow()
        self.start=(76.13,10.,.47)
        self.goal=(134.21,10.,.47)
        self.agent={'id':'rover','kind':'ground','position':list(self.start),
                    'freshness':{'position':{'observed_at':10.}},
                    'physical':{'stop_reason':'hazard_on_route','stop_obstacle':'road_support',
                                'route_blocker':{'kind':'box','min':[81.,9.,-.53],'max':[83.,11.,1.47]}}}
        self.obs={'time':10.,'metaevolution':{'disaster_revision':1}}

    def test_delivered_road_stop_changes_next_route(self):
        before=self.workflow._planned_route(self.map,self.agent,[self.agent],self.goal)
        self.workflow._remember_road_support(self.obs,self.agent)
        after=self.workflow._planned_route(self.map,self.agent,[self.agent],self.goal)
        self.assertEqual(len(self.workflow.road_support_blockers),1)
        self.assertGreater(after.distance_m,before.distance_m+20)
        nav=NavigationMap(self.map,surfaces=self.workflow.road_support_blockers)
        self.assertTrue(all(nav.clear(a,b,1.45,1.1) for a,b in zip(after.points,after.points[1:])))
        self.assertTrue(nav.clear(self.start,self.goal,1.65,.4,aerial=True))
        self.workflow.reset_episode('next')
        self.assertFalse(self.workflow.road_support_blockers)

    def test_only_fresh_physical_road_support_evidence(self):
        for reason,stamp,revision in [('road_support',0.,1),('road_support',11.,1),
                                      ('restricted_volume',10.,1),('road_support',10.,0)]:
            self.agent['physical']['stop_obstacle']=reason
            self.agent['freshness']['position']['observed_at']=stamp
            self.obs['metaevolution']['disaster_revision']=revision
            self.workflow._remember_road_support(self.obs,self.agent)
            self.assertFalse(self.workflow.road_support_blockers)

    def test_margin_exit_cannot_cut_through_cavity(self):
        b=Box((81.,9.,-.53),(83.,11.,1.47))
        nav=NavigationMap(self.map,surfaces=[ObservedSurface(b,True,1.,1.,margin_egress=True)])
        pose=(79.8,10.,.47)
        self.assertTrue(nav.clear(pose,(76.,10.,.47),1.45,1.1))
        self.assertFalse(nav.clear(pose,(87.,10.,.47),1.45,1.1))
        self.assertFalse(nav.clear((82.,10.,.47),(76.,10.,.47),1.45,1.1))
        agent={**self.agent,'position':list(pose)}
        self.workflow.road_support_blockers=[ObservedSurface(b,True,1.,1.,margin_egress=True)]
        path=self.workflow._planned_route(self.map,agent,[agent],self.goal)
        self.assertGreater(path.distance_m,math.dist(pose,self.goal)+20)
        self.assertTrue(all(nav.clear(a,b2,1.45,1.1) for a,b2 in zip(path.points,path.points[1:])))
