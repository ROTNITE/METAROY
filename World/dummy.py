"""Временные агенты-заглушки. Позже заменяются на Agent/*.py через decide(observation)."""
from __future__ import annotations
import random

DIRECTIONS = [(dx,dy) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy]


class DummyAgent:
    """Простая реактивная политика с индивидуальными фразами вместо LLM."""

    def __init__(self, agent_id: str, seed: int):
        self.agent_id = agent_id
        self.rng = random.Random(f'{seed}:{agent_id}')
        self.last_think = -100
        self.last_goal = ''

    def decide(self, observation: dict) -> dict:
        me = observation['self']
        x, y = me['x'], me['y']
        genes = me['genome']
        nearby = observation['visible_agents']
        cells = observation['visible_cells']
        walkable = {(c['x'],c['y']): c for c in cells if c['block_hp'] == 0}
        neighbors = [a for a in nearby if max(abs(a['x']-x),abs(a['y']-y)) <= 1]
        foes = [a for a in neighbors if not a['ally']]
        friends = [a for a in neighbors if a['ally']]
        here = walkable.get((x,y))

        def act(typ, why, **fields):
            return {'type': typ, 'thought': why, **fields}

        # THINK занимает ход и оставляет осмысленный «след мысли» в UI.
        if observation['tick'] - self.last_think >= self.rng.randint(12, 20):
            self.last_think = observation['tick']
            return act('THINK', self.rng.choice([
                'Переоцениваю обстановку: хочу сохранить пищу и найти ресурсы.',
                'Сравниваю риски: враги поблизости или безопасный маршрут?',
                'План: разведать ближайшие клетки, собрать еду и избегать голода.',
                'Думаю о соседях: стоит ли обменяться информацией?',
            ]))
        if foes and me['hp'] > genes['k'] * .25:
            enemy = min(foes, key=lambda a: (a['x']-x)**2+(a['y']-y)**2)
            return act('ATTACK', f'Рядом чужой вид {enemy["species"]}. Защищаю территорию!',
                       target=[enemy['x'],enemy['y']])
        if friends and self.rng.random() < .14:
            other = self.rng.choice(friends)
            interesting = [c for c in cells if c['food'] >= 3]
            info = (f'Еда у ({interesting[0]["x"]},{interesting[0]["y"]})'
                    if interesting else 'Проверяй окрестности; экономь еду')
            return act('TALK', f'Делюсь наблюдением с {other["id"]}',
                       recipient_id=other['id'], message=info)
        if here:
            if here['food'] and (me['food'] < 55 or self.rng.random() < .5):
                return act('PICKUP', f'Забираю {here["food"]} еды. Запасы надо пополнить.',
                           resource='food', amount=max(1, here['food']))
            if here['material'] and me['material'] < 12:
                return act('PICKUP', 'Беру материал для будущего строительства.',
                           resource='material', amount=here['material'])
        if me['material'] >= 5 and self.rng.random() < .022:
            occupied = {(a['x'], a['y']) for a in nearby}
            targets = [(x+dx,y+dy) for dx,dy in DIRECTIONS if (x+dx,y+dy) in walkable
                       and (x+dx,y+dy) not in occupied and
                       not walkable[(x+dx,y+dy)]['food'] and
                       not walkable[(x+dx,y+dy)]['material']]
            if targets:
                tx, ty = self.rng.choice(targets)
                return act('BUILD', 'Ставлю защитный блок из собранного материала.', target=[tx,ty])

        # Выбираем видимый ресурс — еду при низком запасе, иначе сырьё.
        occupied = {(a['x'], a['y']) for a in nearby}
        priority = 'food' if me['food'] < 45 else 'material'
        targets = [c for c in cells if c[priority] > 0 and (c['x'],c['y']) != (x,y)]
        if not targets and priority == 'material':
            targets = [c for c in cells if c['food'] > 0 and (c['x'],c['y']) != (x,y)]
        targets.sort(key=lambda c: (max(abs(c['x']-x),abs(c['y']-y)), -c.get(priority,0)))

        def available_step(tx,ty):
            # Ровно одно простое ближайшее перемещение. Мир проверит все ограничения.
            options = []
            for dx,dy in DIRECTIONS:
                nx,ny = x+dx,y+dy
                if (nx,ny) in walkable and (nx,ny) not in occupied:
                    if dx and dy and ((x+dx,y) not in walkable or (x,y+dy) not in walkable):
                        continue
                    score = max(abs(nx-tx), abs(ny-ty))
                    options.append((score, self.rng.random(), nx, ny))
            if options:
                options.sort()
                return [options[0][2], options[0][3]]
            return None

        if targets:
            target = targets[0]
            move = available_step(target['x'],target['y'])
            if move:
                return act('MOVE', f'Иду к ресурсу в ({target["x"]},{target["y"]}).', target=move)
        options = [(x+dx,y+dy) for dx,dy in DIRECTIONS if (x+dx,y+dy) in walkable
                   and (x+dx,y+dy) not in occupied and
                   (not(dx and dy) or ((x+dx,y) in walkable and (x,y+dy) in walkable))]
        if options:
            nx,ny = self.rng.choice(options)
            return act('MOVE', 'Исследую неизвестную часть карты.', target=[nx,ny])
        return act('WAIT', 'Жду: вокруг нет доступного прохода.')
