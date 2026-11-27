"""Broad phase must match the old exact scan, including unknown and stacked geometry."""
from random import Random
from unittest import TestCase
from controller.navigation import Box
from controller.public_geometry import Cylinder,Union
from controller.portable import _path_clear
from controller.zone_index import ZoneIndex


def zone(kind,shape,**changes):
    return {'type':kind,'access':{},'risk':0.,'constraints':[],**changes},shape


def fixture(seed=77):
    rng=Random(seed)
    zones=[zone('land',Box((-500,-500,0),(500,500,0))),
           zone('air',Box((-500,-500,1),(500,500,100)))]
    for i in range(600):
        x,y,z=(rng.uniform(-450,450),rng.uniform(-450,450),rng.uniform(0,40))
        shape=Box((x,y,z),(x+8,y+12,z+8)) if i%3==0 else Cylinder((x,y),4,z,z+8)
        if i%5==0:
            shape=Union((shape,Box((x+15,y+15,z),(x+19,y+18,z+5))))
        zones.append(zone(('obstacle','restricted','hazard')[i%3],shape,
                          access={'allowed_kinds':['aerial']} if i%7==0 else {}))
    return zones


class SpatialParity(TestCase):
    def test_seeded_randomized_path_equivalence(self):
        for seed in (17,71,314):
            zones=fixture(seed);index=ZoneIndex(zones);rng=Random(seed)
            for i in range(800):
                kind=('ground','aerial')[i%2]
                z=0 if kind=='ground' else rng.uniform(1,99)
                a=(rng.uniform(-490,490),rng.uniform(-490,490),z)
                b=(a[0]+rng.uniform(-100,100),a[1]+rng.uniform(-100,100),z if kind=='ground' else rng.uniform(1,99))
                agent={'kind':kind,'position':a}
                for public_media in (False,True):
                    self.assertEqual(_path_clear(zones,agent,b,public_media=public_media),
                                     _path_clear(index,agent,b,public_media=public_media),
                                     (seed,i,kind,a,b))

    def test_ground_projection_and_union_gap(self):
        zones=fixture()
        zones=[zone('land',Union((Box((0,0,9),(3,3,9)),Box((5,0,9),(8,3,9))))),
               *zones[2:]]
        index=ZoneIndex(zones)
        agent={'kind':'ground','position':(1,1,0)}
        self.assertEqual(_path_clear(index,agent,(2,2,0)),_path_clear(zones,agent,(2,2,0)))
        self.assertTrue(_path_clear(index,agent,(2,2,0)))
        self.assertFalse(_path_clear(index,agent,(7,2,0)))

    def test_unknown_geometry_is_not_silently_discarded(self):
        zones=fixture()+[zone('obstacle',None)]
        index=ZoneIndex(zones)
        self.assertFalse(_path_clear(index,{'kind':'aerial','position':(0,0,40)},(1,1,40)))
        self.assertIn(zones[-1],index.candidates((0,0,40),(1,1,40)))

    def test_touching_and_stacked_bounds(self):
        obstacle=zone('obstacle',Box((2,2,2),(4,4,4)))
        zones=fixture()+[obstacle]
        index=ZoneIndex(zones)
        self.assertIn(obstacle,index.candidates((1,2,2),(2,2,2)))
        self.assertNotIn(obstacle,index.candidates((1,2,6),(2,2,6)))
        self.assertIn(obstacle,index.candidates((1,2,6),(2,2,6),ground=True))
        self.assertEqual(tuple(index),tuple(zones))

    def test_no_stale_index_across_observations(self):
        zones=fixture()
        a=(0,0,99);b=(5,0,99);agent={'kind':'aerial','position':a}
        old=ZoneIndex(zones)
        fresh=ZoneIndex(zones+[zone('obstacle',Box((2,-1,98),(3,1,100)))])
        self.assertTrue(_path_clear(old,agent,b))
        self.assertFalse(_path_clear(fresh,agent,b))

    def test_local_queries_prune_most_of_large_world(self):
        zones=fixture();index=ZoneIndex(zones)
        self.assertLess(len(index.candidates((0,0,50),(5,5,50))),len(zones)//10)
