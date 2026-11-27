"""Immutable per-observation broad phase for public zone geometry.

Bounding boxes only discard impossible intersections. Exact primitive checks,
access, risk and coverage remain the responsibility of the existing planner.
Unknown shapes are always returned. Ground support uses XY queries because
the planner projects each support segment onto its own surface height.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class _Node:
    low: tuple
    high: tuple
    indices: tuple=()
    left: object=None
    right: object=None


def _overlaps(low,high,a,b,dimensions):
    return all(low[i]<=b[i] and a[i]<=high[i] for i in range(dimensions))


class ZoneIndex:
    LEAF_SIZE=8
    MIN_INDEXED=24

    def __init__(self,zones):
        self.zones=tuple(zones)
        self.bounds={}
        unknown=[]
        for i,(_,shape) in enumerate(self.zones):
            try:
                lo,hi=tuple(shape.minimum),tuple(shape.maximum)
                if (len(lo)!=3 or len(hi)!=3 or
                        not all(math.isfinite(x) for x in (*lo,*hi)) or
                        any(a>b for a,b in zip(lo,hi))):
                    raise ValueError('Unbounded shape')
                self.bounds[i]=(lo,hi)
            except (AttributeError,TypeError,ValueError,OverflowError):
                unknown.append(i)
        self.unknown=tuple(unknown)
        self.root=self._build(tuple(self.bounds)) if len(self.bounds)>=self.MIN_INDEXED else None

    def __len__(self):
        return len(self.zones)

    def __iter__(self):
        return iter(self.zones)

    def _build(self,indices):
        lo=tuple(min(self.bounds[i][0][d] for i in indices) for d in range(3))
        hi=tuple(max(self.bounds[i][1][d] for i in indices) for d in range(3))
        if len(indices)<=self.LEAF_SIZE:
            return _Node(lo,hi,indices)
        axis=max(range(3),key=lambda d:hi[d]-lo[d])
        ordered=sorted(indices,key=lambda i:(self.bounds[i][0][axis]/2+self.bounds[i][1][axis]/2,i))
        middle=len(ordered)//2
        return _Node(lo,hi,left=self._build(ordered[:middle]),right=self._build(ordered[middle:]))

    def candidates(self,start,goal,*,ground=False):
        if self.root is None:
            return self.zones
        try:
            if len(start)!=3 or len(goal)!=3 or not all(math.isfinite(x) for x in (*start,*goal)):
                return self.zones
            a=tuple(min(x,y) for x,y in zip(start,goal))
            b=tuple(max(x,y) for x,y in zip(start,goal))
        except (TypeError,ValueError,OverflowError):
            return self.zones
        dimensions=2 if ground else 3
        hits=list(self.unknown)
        stack=[self.root]
        while stack:
            node=stack.pop()
            if not _overlaps(node.low,node.high,a,b,dimensions):
                continue
            if node.indices:
                for i in node.indices:
                    if _overlaps(*self.bounds[i],a,b,dimensions):
                        hits.append(i)
            else:
                stack.append(node.left)
                stack.append(node.right)
        # Preserve original ordering (including early conservative rejection).
        hits.sort()
        return tuple(self.zones[i] for i in hits)


def candidate_zones(zones,start,goal,kind):
    return zones.candidates(start,goal,ground=kind=='ground') if isinstance(zones,ZoneIndex) else zones
