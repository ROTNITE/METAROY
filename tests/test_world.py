"""Проверяем движок без браузера, сети и LLM."""
import unittest
from Starter import Starter
from World import World, Config
from World.core import Genome, BUILD_COST, STARVATION_DAMAGE


class WorldTests(unittest.TestCase):
    def test_initial_populations_and_bounds(self):
        w=World(Config())
        self.assertEqual((len(w.grid),len(w.grid[0])),(30,30))
        self.assertEqual([sum(a.species==sp for a in w.agents.values()) for sp in 'ABC'],[5,5,5])
        self.assertFalse(w.inside(-1,0))
        self.assertFalse(w.inside(30,0))
        self.assertEqual(len({a.position for a in w.agents.values()}),15)

    def test_genome_budget(self):
        c=Config()
        self.assertTrue(Genome(2,2,3,32).valid(c))
        self.assertFalse(Genome(1,4,10,70).valid(c))

    def test_spawn_reproducibility(self):
        w1=World(Config(seed=120))
        w2=World(Config(seed=120))
        self.assertEqual(w1.snapshot(),w2.snapshot())
        for _ in range(5):
            self.assertEqual(w1.step({}),w2.step({}))

    def test_visibility_enemy_privacy(self):
        w=World(Config())
        a=w.agents['A-01']; b=w.agents['B-01']
        b.x,b.y=a.x+1,a.y
        obs=w.observe(a)
        foes=[x for x in obs['visible_agents'] if x['species']=='B']
        self.assertEqual(len(foes),1)
        self.assertEqual(set(foes[0]),{'species','x','y','ally'})
        self.assertNotIn('thought',foes[0])
        self.assertLess(len(obs['visible_cells']), 49+1)

    def test_eight_direction_and_block(self):
        w=World(Config())
        a=w.agents['A-01']; b=w.agents['B-01']
        w.agents={a.id:a,b.id:b}
        a.x,a.y=10,10
        w.step({a.id:{'type':'MOVE','target':[11,11]}},spawn_first=False)
        self.assertEqual(a.position,(11,11))
        w.cell(12,11).block_hp=18
        w.cell(11,12).block_hp=18
        w.step({a.id:{'type':'MOVE','target':[12,12]}},spawn_first=False)
        self.assertEqual(a.position,(11,11))

    def test_simultaneous_friendly_fire(self):
        w=World(Config())
        a=w.agents['A-01'];b=w.agents['A-02']
        w.agents={a.id:a,b.id:b}
        a.x,a.y=9,9;b.x,b.y=10,9
        ahp,bhp=a.hp,b.hp
        w.step({a.id:{'type':'ATTACK','target':[10,9]},b.id:{'type':'ATTACK','target':[9,9]}},spawn_first=False)
        self.assertEqual(a.hp,ahp-b.genome.z)
        self.assertEqual(b.hp,bhp-a.genome.z)

    def test_pickup_drop_and_build(self):
        w=World(Config())
        a=w.agents['A-01']; b=w.agents['B-01']
        w.agents={a.id:a,b.id:b}
        a.x,a.y=10,10;a.material=10
        w.cell(10,10).food=7
        w.cell(11,10).food=0
        w.cell(11,10).material=0
        w.cell(11,10).block_hp=0
        f=a.food
        w.step({a.id:{'type':'PICKUP','resource':'food','amount':7}},spawn_first=False)
        self.assertEqual(a.food,f+7-a.genome.x)
        self.assertEqual(w.cell(10,10).food,0)
        w.step({a.id:{'type':'BUILD','target':[11,10]}},spawn_first=False)
        self.assertEqual(w.cell(11,10).block_hp,18)
        self.assertEqual(a.material,10-BUILD_COST)

    def test_starvation(self):
        w=World(Config());a=w.agents['A-01'];w.agents={a.id:a}
        a.food=0;a.hp=20
        w.step({a.id:{'type':'WAIT'}},spawn_first=False)
        self.assertEqual(a.hp,20-STARVATION_DAMAGE)

    def test_talk_message(self):
        w=World(Config());a=w.agents['A-01'];b=w.agents['A-02']
        w.agents={a.id:a,b.id:b};a.x,a.y=9,9;b.x,b.y=10,9
        w.step({a.id:{'type':'TALK','recipient_id':b.id,'message':'Еда на востоке'}},spawn_first=False)
        self.assertEqual(w.observe(b)['messages'][0]['text'],'Еда на востоке')

    def test_collision_winner_deterministic(self):
        w=World(Config());a=w.agents['A-01'];b=w.agents['A-02']
        w.agents={a.id:a,b.id:b};a.x,a.y=9,9;b.x,b.y=11,9
        w.step({a.id:{'type':'MOVE','target':[10,9]},b.id:{'type':'MOVE','target':[10,9]}},spawn_first=False)
        self.assertEqual(a.position,(10,9));self.assertEqual(b.position,(11,9))

    def test_end_to_end_starter(self):
        app=Starter(Config())
        before=app.state()
        for _ in range(20): s=app.step()
        self.assertEqual(s['tick'],20)
        self.assertTrue(any(a['last_action'] not in ('WAIT',) for a in s['agents']))
        self.assertNotEqual(before['cells'],s['cells'])
        self.assertEqual(app.reset()['tick'],0)


if __name__ == '__main__':
    unittest.main()
