import unittest
from controller.baselines import FixedWorkflow, Job
from controller.water_rescue import WaterRoute
from controller.water_survey import candidate_cells, water_survey_command

class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.map={'bounds':{'kind':'box','min':[-100,0,-12],'max':[250,250,90]},
                  'hydrology':{'basin':{'min':[-90,10,-7],'max':[-50,50,2]},'initial_stage_m':2.},
                  'base':{'kind':'box','min':[0.,90.,0.],'max':[20.,110.,1.]},
                  'navigation':{'kind':'street_surface_graph.v2','ground_clearance_m':.43,
                                'road_surface_z_m':.04,'base_surface_z_m':.11}}
        self.agent={'id':'drone','kind':'aerial','status':'active','position':[10.,100.,18.],
                    'capabilities':['sense','move'],'available_actions':[{'action':'follow_route'}],
                    'freshness':{'position':{'observed_at':10.}},
                    'physical':{'dock_position_m':[10.,100.,3.],'speed_limit_m_s':10.,'climb_rate_m_s':5.,
                                'descent_rate_m_s':2.5,'remaining_wh':120.,'reserve_wh':12.}}
        self.boat={'id':'boat','kind':'surface_water','status':'active','position':[-65.,30.,1.]}
        self.obs={'time':10.,'agents':[self.agent,self.boat],'zones':[],
                  'metaevolution':{'disaster_revision':1}}
        self.workflow=FixedWorkflow()
        self.workflow.water_survey_requests['boat']=(10.,(-65.,30.),(100.,100.),'patient')
        self.job=Job('scan')

    def test_survey_uses_only_unknown_or_stale_cells_and_cooldown(self):
        nav=WaterRoute(self.map,self.obs,[],.55)
        candidates=candidate_cells(nav,(-65.,30.),(100.,100.),self.map['bounds'],{},10.)
        self.assertTrue(candidates)
        self.assertTrue(all(not nav.deep(*xy) for xy in candidates.values()))
        command=water_survey_command(self.workflow,self.obs,self.map,self.agent,self.job,{})
        self.assertIsNotNone(command)
        self.assertTrue(self.job.inspection_route)
        self.assertEqual(command['action'],'follow_route')
        self.assertIsNone(water_survey_command(self.workflow,self.obs,self.map,self.agent,self.job,{}))

    def test_stale_agent_low_energy_legacy_and_evacuated_target(self):
        for altered in ('legacy','energy','stale','done','missing_navigation'):
            self.setUp()
            known={}
            if altered=='legacy':self.obs['metaevolution']={}
            if altered=='energy':self.agent['physical']['remaining_wh']=12.1
            if altered=='stale':self.agent['freshness']['position']['observed_at']=1.
            if altered=='done':known['patient']={'evacuation_state':'evacuated'}
            if altered=='missing_navigation':self.map.pop('navigation')
            with self.subTest(altered=altered):
                self.assertIsNone(water_survey_command(self.workflow,self.obs,self.map,self.agent,self.job,known))

    def test_reset_discards_all_survey_requests(self):
        self.workflow.reset_episode('next')
        self.assertFalse(self.workflow.water_survey_requests)
        self.assertFalse(self.workflow.water_survey_recent)
        self.assertEqual(self.workflow.water_survey_next,0.)
