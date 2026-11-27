"""Independent geometry and bounded-search checks for portable detours."""
from unittest import TestCase
from controller.navigation import Box
from controller.portable import _path_clear
from controller.public_route import RouteBudget, detour


def zone(kind,lo,hi):
    return {'type':kind,'risk':0.,'access':{},'constraints':[]},Box(lo,hi)


class DetourTests(TestCase):
    def route(self, start, goal, zones, kind='ground'):
        agent={'kind':kind,'position':start}
        clear=lambda a,b:_path_clear(zones,{**agent,'position':a},b)
        budget=RouteBudget(len(zones))
        self.assertFalse(clear(start,goal))
        first=detour(start,goal,zones,kind,clear,budget)
        return first,clear,budget

    def test_ground_detours_around_blocked_direct_route(self):
        zones=[zone('land',(0,0,0),(44,44,0)),zone('obstacle',(12,0,0),(15,28,6))]
        start=(3.,3.,0.);goal=(26.,23.,0.)
        first,clear,budget=self.route(start,goal,zones)
        self.assertIsNotNone(first)
        self.assertTrue(clear(start,first))
        points=[start,first]
        for _ in range(8):
            if clear(points[-1],goal):
                points.append(goal)
                break
            following=detour(points[-1],goal,zones,'ground',clear,RouteBudget(len(zones)))
            self.assertIsNotNone(following)
            self.assertNotIn(following,points)
            self.assertTrue(clear(points[-1],following))
            points.append(following)
        self.assertEqual(points[-1],goal)
        self.assertGreaterEqual(budget.remaining,0)

    def test_full_height_barrier_has_no_route(self):
        zones=[zone('land',(0,0,0),(44,44,0)),zone('obstacle',(12,0,-1),(15,44,9))]
        first,_,budget=self.route((3,3,0),(30,30,0),zones)
        self.assertIsNone(first)
        self.assertGreaterEqual(budget.remaining,0)

    def test_aircraft_can_use_vertical_clearance(self):
        zones=[zone('air',(0,0,1),(44,44,20)),zone('obstacle',(12,0,0),(15,44,6))]
        first,clear,_=self.route((3,3,3),(30,30,3),zones,'aerial')
        self.assertIsNotNone(first)
        self.assertTrue(clear((3,3,3),first))
        self.assertGreater(first[2],6)

    def test_shared_budget_is_never_exceeded(self):
        budget=RouteBudget(1)
        budget.remaining=2
        seen=[]
        zones=[zone('land',(0,0,0),(44,44,0)),zone('obstacle',(12,0,0),(15,28,6))]
        for _ in range(10):
            detour((3,3,0),(30,30,0),zones,'ground',lambda a,b:seen.append((a,b)) or False,budget)
        self.assertEqual(len(seen),2)
        self.assertEqual(budget.remaining,0)
