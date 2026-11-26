import unittest
from controller.information_gain import InformationPolicy
from controller.adaptive_survey import AdaptiveSurveyPolicy
from environment.world import World,capabilities
from experiments.mission_flow.auto_information import propose,compare,SUITES
from experiments.mission_flow.benchmark_information import physical

class InformationSearchTests(unittest.TestCase):
    def test_proposals_are_reproducible_unique_and_new(self):
        a=propose([], 'range-late', 42,12)
        self.assertEqual(a,propose([], 'range-late',42,12))
        self.assertEqual(len(set(a)),12)
        self.assertFalse(set(a)&set(propose(a,'range-late',42,12)))
    def test_reject_energy_and_suite_regression(self):
        base={(s,0):dict(m1=.5,saved=5,energy=1,clipped=100,error=None,reserve_violations=0,rejections=0) for s in SUITES}
        win={k:{**v,'m1':.6,'saved':6} for k,v in base.items()}
        self.assertTrue(compare(base,win,bootstrap=True)['passed'])
        bad={k:{**v,'energy':1.001} for k,v in win.items()}
        self.assertFalse(compare(base,bad)['passed'])
        bad=dict(win);bad[(SUITES[0],0)]={**win[(SUITES[0],0)],'m1':.48}
        self.assertFalse(compare(base,bad)['passed'])
    def test_single_carrier_preserves_commands(self):
        w=World(27,'late_target');obs=w.observation('first')
        a=AdaptiveSurveyPolicy(capabilities(),'nsga2_nsga2').decide(obs)
        for variant in ('split','cover','repeat-rescue','repeat-tune-1-2'):
            b=InformationPolicy(capabilities(),'nsga2_nsga2',grid=variant).decide(obs)
            self.assertEqual(a['commands'],b['commands'])
    def test_stress_fallback_and_pair(self):
        a=physical(151200,'range-late',True,True,True)
        b=physical(151200,'info-repeat-rescue',True,True,True)
        for k in ('initial','saved','m1','energy','clipped','error','rejections','reserve_violations'):
            self.assertEqual(a[k],b[k])
    def test_no_fabricated_scan(self):
        p=InformationPolicy(capabilities(),'nsga2_nsga2',grid='cover')
        w=World(27);p.decide(w.observation('first'))
        self.assertFalse(p.scanned_at)

if __name__=='__main__':unittest.main()
