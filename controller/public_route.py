"""Bounded detours using only published XYZ volumes and an admissibility oracle.

A direct admissible segment costs one check. For a blocked segment, search a
small visibility graph of conservative obstacle-envelope waypoints. Every edge
must pass the existing public safety oracle. This is an incomplete planner:
unknown geometry, exhausted budget or disconnected media return no route.
"""
from heapq import heappop, heappush
from itertools import product
from math import dist, inf


MAX_OBSTACLES = 12
MAX_NODES = 72
MAX_EDGE_CHECKS = 384


class RouteBudget:
    def __init__(self, zone_count):
        # Shared by all detours in a decision, not multiplied by agent count.
        self.remaining = min(MAX_EDGE_CHECKS, 120_000 // max(1, zone_count))

    def test(self, clear, a, b):
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return clear(a, b)


def detour(start, goal, zones, kind, clear, budget, *, margin=.5):
    """Return a checked first waypoint, or None without issuing an unsafe move."""
    start, goal = tuple(start), tuple(goal)
    if start == goal or budget.remaining <= 0:
        return None
    blocked = []
    for zone, shape in zones:
        if zone['type'] not in ('obstacle', 'restricted', 'hazard'):
            continue
        if zone['type'] != 'obstacle':
            allowed = zone['access'].get('allowed_kinds', zone['access'].get('allowed_agent_kinds'))
            if isinstance(allowed, list) and kind in allowed:
                continue
        if shape is None:
            return None
        lo, hi = shape.minimum, shape.maximum
        center = tuple((a+b)/2 for a,b in zip(lo,hi))
        blocked.append((shape.interval(start,goal) is None, dist(start,center)+dist(goal,center), lo,hi))
    if not blocked:
        return None
    candidates = set()
    for _, _, lo, hi in sorted(blocked)[:MAX_OBSTACLES]:
        xs, ys = (lo[0]-margin,hi[0]+margin),(lo[1]-margin,hi[1]+margin)
        if kind in ('aerial','underwater','underground'):
            zs = {start[2],goal[2],lo[2]-margin,hi[2]+margin}
            candidates.update(product(xs,ys,zs))
            candidates.update((p[0],p[1],z) for p in (start,goal) for z in (lo[2]-margin,hi[2]+margin))
        else:
            candidates.update((x,y,start[2]) for x,y in product(xs,ys))
    candidates.discard(start); candidates.discard(goal)
    nodes = [start,goal,*sorted(candidates,key=lambda p:(dist(start,p)+dist(p,goal),p))[:MAX_NODES-2]]
    distances = {0:0.}
    previous = {}
    tested = {}
    queue = [(dist(start,goal),0.,0)]
    while queue and budget.remaining>0:
        _,cost,i=heappop(queue)
        if cost!=distances.get(i):
            continue
        if i==1:
            while previous[i]!=0:
                i=previous[i]
            return nodes[i]
        # Directed cache: a public constraint can distinguish direction.
        for j in sorted(range(1,len(nodes)),key=lambda j:(dist(nodes[i],nodes[j])+dist(nodes[j],goal),j)):
            if j==i:
                continue
            next_cost=cost+dist(nodes[i],nodes[j])
            if next_cost>=distances.get(j,inf):
                continue
            key=(i,j)
            if key not in tested:
                if budget.remaining<=0:
                    break
                tested[key]=budget.test(clear,nodes[i],nodes[j])
            if tested[key]:
                previous[j]=i;distances[j]=next_cost
                heappush(queue,(next_cost+dist(nodes[j],goal),next_cost,j))
        # A checked goal path is usable even if the remaining search budget
        # expires; do not claim globally shortest routing.
        if budget.remaining<=0 and 1 in previous:
            i=1
            while previous[i]!=0:
                i=previous[i]
            return nodes[i]
    return None
