"""FullFleet release gates and native forecast regression."""
from copy import deepcopy
import random
import unittest

from controller.fullfleet.bounds import supported_bounds
from controller.fullfleet.integration import make_portable
from controller.fullfleet.native import acceptable, delayed_forecast, evaluate, refine_plan
from controller.optimization import Crew, RescueProblem, Target, Trip
from environment.world import World, capabilities
from contracts.protocol import validate_decision


def canonical_frame():
    frame=World(17).observation('test')
    for agent in frame['agents']:
        if agent['constraints']:
            continue
        lo,hi=[0,0,0],[50,50,0]
        agent['constraints']=[{'id':'domain-'+agent['id'],'type':'position_bounds','severity':'hard',
            'scope':{'kind':'agent','agent_id':agent['id']},'min':lo,'max':hi,
            'predicate':{'op':'and','args':[{'op':op,'left':{'path':'position.'+str(i)},'right':{'value':v}}
                for i in range(3) for op,v in (('ge',lo[i]),('le',hi[i]))]}}]
    return frame


def plan_for(problem, genome):
    score,tasks=problem.simulate(genome)
    return {'genome':genome,'selected_objectives':list(score),'stats':{'evaluations':1},'pareto':[],
        'routes':{c.id:[problem.targets[j].id for j,_,_,_ in tasks[r]] for r,c in enumerate(problem.crew)}}


class BoundsTests(unittest.TestCase):
    def test_exact_domain_constraints_are_preserved(self):
        frame=canonical_frame();original=deepcopy(frame)
        self.assertTrue(supported_bounds(frame))
        policy=make_portable(capabilities(),'nsga2_nsga2',scout_patrol='net65')
        result=policy.decide(frame)
        self.assertTrue(policy.route_active)
        self.assertEqual(frame,original)
        self.assertTrue(result['constraint_checks'])
        self.assertTrue(all(c['status']=='passed' for c in result['constraint_checks']))
        validate_decision(result,frame,capabilities())

    def test_stricter_unknown_and_malformed_rules_fall_back(self):
        for mutation in ('narrow','unknown','global','predicate','scope','missing'):
            with self.subTest(mutation=mutation):
                frame=canonical_frame();rule=frame['agents'][0]['constraints'][0]
                if mutation=='narrow':rule['max'][0]=40;rule['predicate']['args'][1]['right']['value']=40
                elif mutation=='unknown':rule['type']='unknown'
                elif mutation=='global':frame['constraints'].append(deepcopy(rule))
                elif mutation=='predicate':rule['predicate']['args'].pop()
                elif mutation=='scope':rule['scope']['agent_id']='wrong'
                else:del rule['min']
                self.assertFalse(supported_bounds(frame))

    def test_portable_factory_frozen_flags_and_cache_scope(self):
        policy=make_portable(capabilities(),'nsga2_nsga2',scout_patrol='net65')
        self.assertEqual(policy.rollout_depth,2)
        self.assertTrue(policy.per_rescue and policy.all_routes and policy.track_speed and
                        policy.service_estimate and policy.tick_service)
        policy.decide(canonical_frame())
        self.assertIsNone(policy._fast_geometry)
        self.assertIsNone(policy._fast_trip_estimate)


class NativeRepairTests(unittest.TestCase):
    def test_repair_reassigns_to_save_time_and_energy(self):
        problem=RescueProblem([Target('a',1),Target('b',1)],[Crew('r1'),Crew('r2')],
            [[Trip(30,8),Trip(10,2)],[Trip(10,2),Trip(30,8)]],horizon=80)
        original=plan_for(problem,([0,1],[0,1]));snapshot=deepcopy(original)
        repaired=refine_plan(problem,original)
        self.assertTrue(repaired['fullfleet']['changed'])
        self.assertEqual(repaired['routes'],{'r1':['b'],'r2':['a']})
        self.assertEqual(repaired['selected_objectives'],[0.,20.,4.])
        self.assertEqual(original,snapshot)
        self.assertEqual(repaired,refine_plan(problem,original))

    def test_occupied_crew_requires_known_release(self):
        problem=RescueProblem([Target('a',1),Target('b',1)],[Crew('r',ready_at=0)],
            [[Trip(10,2),Trip(10,2)]],horizon=80,available_now=[False])
        original=plan_for(problem,([0,1],[0,0]))
        self.assertEqual(refine_plan(problem,original)['fullfleet']['reason'],'unknown_crew_release')
        known=RescueProblem(problem.targets,[Crew('r',ready_at=15)],problem.trips,horizon=80,available_now=[False])
        _,tasks=known.simulate(([0,1],[0,0]))
        self.assertEqual(tasks[0][0][1],15)
        self.assertGreater(refine_plan(known,plan_for(known,([0,1],[0,0])))['fullfleet']['evaluated'],0)

    def test_delays_recheck_deadline_and_remaining_energy(self):
        problem=RescueProblem([Target('a',3,deadline=100),Target('b',2,deadline=115)],
            [Crew('r',ready_at=0,usable_wh=10)],[[Trip(95,5),Trip(10,5)]],horizon=120)
        _,tasks=problem.simulate(([0,1],[0,0]))
        cases,_=delayed_forecast(problem,tasks)
        self.assertEqual([x['saved'] for x in cases],[2,1,1])
        self.assertEqual(cases[1]['priority_saved'],2.)
        tight=RescueProblem(problem.targets,[Crew('r',usable_wh=10)],[[Trip(10,5),Trip(10,5)]],horizon=120)
        _,tasks=tight.simulate(([0,1],[0,0]))
        cases,_=delayed_forecast(tight,tasks)
        self.assertEqual([x['saved'] for x in cases],[2,1,1])
        self.assertLessEqual(max(x['energy_Wh'] for x in cases),10.)

    def test_skipped_first_uses_actual_release_matrix(self):
        problem=RescueProblem([Target('a',1,deadline=10),Target('b',1)], [Crew('r',usable_wh=6)],
            [[Trip(10,2),Trip(10,2)]],first_trips=[[Trip(10,2),Trip(50,9)]],horizon=100)
        _,tasks=problem.simulate(([0,1],[0,0]))
        self.assertEqual(len(tasks[0]),2)
        cases,_=delayed_forecast(problem,tasks)
        self.assertEqual([c['saved'] for c in cases],[2,0,0])

    def test_randomized_matrix_invariants(self):
        rng=random.Random(5841128)
        for i in range(500):
            n=rng.randrange(2,21);m=rng.randrange(1,7)
            targets=[Target(str(j),rng.randrange(1,4),deadline=rng.randrange(30,200)) for j in range(n)]
            crew=[Crew(str(r),ready_at=rng.randrange(0,20),usable_wh=rng.randrange(5,70)) for r in range(m)]
            def matrix():
                return [[None if rng.random()<.1 else Trip(rng.randrange(1,40),rng.randrange(1,15))
                         for _ in range(n)] for _ in range(m)]
            problem=RescueProblem(targets,crew,matrix(),first_trips=matrix(),horizon=200)
            order=list(range(n));rng.shuffle(order);genome=(order,[rng.randrange(m) for _ in range(n)])
            original=plan_for(problem,genome);repaired=refine_plan(problem,original)
            base,_=evaluate(problem,genome);new,tasks=evaluate(problem,repaired['genome'])
            self.assertTrue(new==base or acceptable(new,base),i)
            self.assertLessEqual(repaired['fullfleet']['evaluated'],32)
            self.assertEqual(repaired['selected_objectives'],list(new[:3]))
            nominal,_=delayed_forecast(problem,tasks)
            self.assertEqual(nominal[0]['saved'],new[3])
            self.assertAlmostEqual(nominal[0]['energy_Wh'],new[2],places=2)
            self.assertAlmostEqual(nominal[0]['weighted_completion'],new[1],places=2)

    def test_search_budget(self):
        targets=[Target(str(j),1) for j in range(21)]
        problem=RescueProblem(targets,[Crew('r')],[[Trip(1,1)]*21],horizon=100)
        original=plan_for(problem,(list(range(21)),[0]*21))
        self.assertEqual(refine_plan(problem,original)['fullfleet']['reason'],'outside_search_budget')


class EntrypointTests(unittest.TestCase):
    def test_product_profile_validation_preserves_default(self):
        from controller.profiles import FULLFLEET, COUNTERFACTUAL
        from tools.product_session import REQUEST_FORMAT, default_product_request, parse_request
        self.assertEqual(default_product_request()['controller']['controller_profile'],COUNTERFACTUAL)
        request={'format':REQUEST_FORMAT,'settings':{},'controller':{'controller_profile':FULLFLEET}}
        self.assertEqual(parse_request(request)['controller']['scout_patrol'],'net65')
        request['controller']['scout_patrol']='core'
        with self.assertRaises(ValueError):parse_request(request)

    def test_native_solver_activates_and_global_candidates_use_repaired_forecast(self):
        from controller.policy import EvolutionPolicy
        from controller.profiles import FULLFLEET
        from controller.problem_builder import ProblemBuilder
        from test_problem_builder import public_state
        state=public_state()
        builder=ProblemBuilder(state['metaevolution']['announced_map'])
        for enabled in (False,True):
            with self.subTest(global_candidates=enabled):
                policy=EvolutionPolicy(controller_profile=FULLFLEET,strategy_candidates=enabled,
                    population_rescue=8,generations_rescue=2)
                try:
                    result=policy._solve_rescue(builder,state,{})
                    self.assertTrue(policy.fullfleet_enabled)
                    self.assertIn('fullfleet',result['plan'])
                    self.assertGreater(result['plan']['fullfleet']['evaluated'],0)
                    self.assertIn('continuations',result['plan'])
                    if enabled:
                        self.assertTrue(result['strategy_candidates'])
                        self.assertTrue(all('fullfleet' in c for c in result['strategy_candidates']))
                    blocked=deepcopy(state);blocked['mission']['directives']=[{'action':'hold'}]
                    result=policy._solve_rescue(builder,blocked,{})
                    self.assertEqual(result['plan']['fullfleet']['reason'],'public_constraints_or_directives')
                finally:
                    policy.close()


if __name__=='__main__':unittest.main()
