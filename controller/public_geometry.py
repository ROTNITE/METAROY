"""Bounded geometric primitives for the public XYZ planner."""
from dataclasses import dataclass
import math

from controller.navigation import Box, point


@dataclass(frozen=True)
class Cylinder:
    xy: tuple
    radius: float
    z_min: float
    z_max: float

    @property
    def minimum(self):
        return (self.xy[0]-self.radius,self.xy[1]-self.radius,self.z_min)

    @property
    def maximum(self):
        return (self.xy[0]+self.radius,self.xy[1]+self.radius,self.z_max)

    def interval(self, start, goal, radius=0., half_height=None):
        vertical = Box((-math.inf,-math.inf,self.z_min),(math.inf,math.inf,self.z_max))
        domain = vertical.interval(start,goal,radius,half_height)
        if domain is None:
            return None
        x,y = (start[i]-self.xy[i] for i in range(2))
        dx,dy = (goal[i]-start[i] for i in range(2))
        a = dx*dx+dy*dy
        b = 2*(x*dx+y*dy)
        c = x*x+y*y-(self.radius+radius)**2
        if a <= 1e-18:
            return domain if c<=0 else None
        discriminant=b*b-4*a*c
        if discriminant<0:
            return None
        root=math.sqrt(discriminant)
        low,high=max(domain[0],(-b-root)/(2*a)),min(domain[1],(-b+root)/(2*a))
        return (low,high) if low<=high else None


@dataclass(frozen=True)
class Union:
    parts: tuple

    @property
    def minimum(self):
        return tuple(min(part.minimum[i] for part in self.parts) for i in range(3))

    @property
    def maximum(self):
        return tuple(max(part.maximum[i] for part in self.parts) for i in range(3))

    def interval(self, start, goal, radius=0., half_height=None):
        """Only used by obstacle checks, which need any intersection."""
        return next((segment for segment in segments(self,start,goal,radius,half_height)),None)


def segments(shape, start, goal, radius=0., half_height=None):
    if isinstance(shape,Union):
        for part in shape.parts:
            yield from segments(part,start,goal,radius,half_height)
    elif (interval:=shape.interval(start,goal,radius,half_height)) is not None:
        yield interval


def geometry(item, depth=0):
    if not isinstance(item,dict) or depth>5:
        raise ValueError('Unsupported public geometry')
    kind=item.get('type',item.get('kind',item.get('shape')))
    if kind in ('box','aabb'):
        return Box.parse({**item,'kind':'box'})
    if kind=='cylinder':
        center=point(item['center'])
        radius,low,high=(item[k] for k in ('radius','z_min','z_max'))
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (radius,low,high)) or radius<=0 or low>high:
            raise ValueError('Invalid public cylinder geometry')
        return Cylinder(center[:2],radius,low,high)
    if kind=='union' and isinstance(item.get('parts'),list) and 1<=len(item['parts'])<=32:
        return Union(tuple(geometry(part,depth+1) for part in item['parts']))
    raise ValueError('Unsupported public geometry')
