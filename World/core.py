"""Детерминированная клеточная среда: мир — единственный источник игрового состояния."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import random
from typing import Any

SPECIES = ('A', 'B', 'C')
BUILD_COST = 5
BLOCK_HP = 18
STARVATION_DAMAGE = 4
VIEW_RADIUS = 3
ALLOWED_ACTIONS = {'THINK', 'MOVE', 'ATTACK', 'PICKUP', 'DROP', 'BUILD', 'TALK', 'WAIT'}
DIRECTIONS = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]


@dataclass(frozen=True)
class Config:
    width: int = 30
    height: int = 30
    seed: int = 42
    starting_agents_per_species: int = 5
    limit_ticks: int = 1000
    food_spawn_attempts: int = 20
    material_spawn_attempts: int = 8
    alpha: float = 1.0
    beta: float = 2.0
    delta: float = 0.25
    gamma: float = 12.0

    def __post_init__(self):
        if self.width < 12 or self.height < 12:
            raise ValueError('Размер мира должен быть не меньше 12×12')
        if self.starting_agents_per_species < 1 or self.limit_ticks < 1:
            raise ValueError('Число агентов и итераций должно быть положительным')
        if self.food_spawn_attempts < 0 or self.material_spawn_attempts < 0:
            raise ValueError('Число попыток спавна не может быть отрицательным')


@dataclass
class Cell:
    food: int = 0
    material: int = 0
    block_hp: int = 0


@dataclass
class Genome:
    x: int  # еда/ход
    y: int  # максимум шагов за действие MOVE
    z: int  # урон
    k: int  # максимум HP

    def valid(self, config: Config) -> bool:
        return (self.x > 0 and self.y > 0 and self.z > 0 and self.k > 0
                and config.alpha * self.z + config.beta * self.y
                + config.delta * self.k <= config.gamma * self.x)


@dataclass
class Avatar:
    id: str
    species: str
    x: int
    y: int
    genome: Genome
    hp: int
    food: int = 40
    material: int = 0
    age: int = 0
    thought: str = 'Наблюдаю за окружением'
    last_action: str = 'WAIT'
    inbox: list[dict] = field(default_factory=list)

    @property
    def position(self) -> tuple[int, int]:
        return self.x, self.y


class World:
    """Без LLM и рендера. Принимает только команды и возвращает наблюдения/снимки."""

    def __init__(self, config: Config):
        self.config = config
        self.rng = random.Random(config.seed)
        self.grid = [[Cell() for _ in range(config.width)] for _ in range(config.height)]
        self.tick = 0
        self.status = 'running'
        self.winner: str | None = None
        self.agents: dict[str, Avatar] = {}
        self.events: list[dict] = []
        self.last_events: list[dict] = []
        self.history: list[dict] = []
        self.bases = self._make_bases()
        self.stores = {kind: {'food': 0, 'material': 0} for kind in SPECIES}
        self._spawn_initial_agents()
        # Стартовые ресурсы, чтобы начало партии не зависело от удачи одного тика.
        for _ in range(8):
            self.spawn_resources()
        self._log('start', 'Три вида появились в разных частях карты')
        self._record_history()

    def _make_bases(self):
        w, h = self.config.width, self.config.height
        return {'A': (3, 3), 'B': (w - 4, 3), 'C': (w // 2, h - 4)}

    def _spawn_initial_agents(self):
        for species in SPECIES:
            bx, by = self.bases[species]
            available = [(x, y) for y in range(by - 2, by + 3) for x in range(bx - 2, bx + 3)
                         if self.inside(x, y) and (x, y) != (bx, by)
                         and (x, y) not in self.occupants()]
            self.rng.shuffle(available)
            if self.config.starting_agents_per_species > len(available):
                raise ValueError('Слишком много стартовых агентов для стартовой зоны')
            for i, (x, y) in enumerate(available[:self.config.starting_agents_per_species]):
                genome = Genome(x=self.rng.choice([1, 2]), y=self.rng.choice([1, 2, 3]),
                                z=self.rng.randint(2, 5), k=self.rng.randint(25, 36))
                # Если высокая характеристика не помещается в пищевой бюджет — корректируем X.
                while not genome.valid(self.config):
                    genome.x += 1
                agent_id = f'{species}-{i+1:02d}'
                self.agents[agent_id] = Avatar(agent_id, species, x, y, genome, genome.k)

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.config.width and 0 <= y < self.config.height

    def cell(self, x: int, y: int) -> Cell:
        if not self.inside(x, y):
            raise IndexError('Координаты за пределами карты')
        return self.grid[y][x]

    def in_base(self, species: str, x: int, y: int) -> bool:
        bx, by = self.bases[species]
        return max(abs(x-bx), abs(y-by)) <= 1

    def occupants(self) -> dict[tuple[int, int], Avatar]:
        return {(a.x, a.y): a for a in self.agents.values()}

    def _log(self, typ: str, text: str, **extra):
        event = {'tick': self.tick, 'type': typ, 'text': text, **extra}
        self.events.append(event)
        self.last_events.append(event)
        if len(self.events) > 350:
            self.events = self.events[-350:]

    def spawn_resources(self):
        """Равномерный случайный спавн независимо от числа агентов."""
        for resource, attempts, amounts in (
            ('food', self.config.food_spawn_attempts, (2, 4)),
            ('material', self.config.material_spawn_attempts, (1, 2)),
        ):
            for _ in range(attempts):
                x = self.rng.randrange(self.config.width)
                y = self.rng.randrange(self.config.height)
                cell = self.cell(x, y)
                if cell.block_hp > 0:
                    continue
                setattr(cell, resource, getattr(cell, resource) + self.rng.randint(*amounts))

    def observe(self, agent: Avatar) -> dict[str, Any]:
        """В публичное наблюдение нельзя включать скрытые параметры врагов."""
        visible_cells = []
        r = VIEW_RADIUS
        for y in range(max(0, agent.y-r), min(self.config.height, agent.y+r+1)):
            for x in range(max(0, agent.x-r), min(self.config.width, agent.x+r+1)):
                c = self.cell(x, y)
                visible_cells.append({'x': x, 'y': y, 'food': c.food,
                                      'material': c.material, 'block_hp': c.block_hp})
        seen_agents = []
        for other in self.agents.values():
            if other.id == agent.id or max(abs(other.x-agent.x), abs(other.y-agent.y)) > r:
                continue
            if other.species == agent.species:
                seen_agents.append({'id': other.id, 'species': other.species,
                                    'x': other.x, 'y': other.y, 'ally': True})
            else:
                # Имя, HP, геном, запасы и мысли врага не передаются.
                seen_agents.append({'species': other.species, 'x': other.x,
                                    'y': other.y, 'ally': False})
        return {
            'tick': self.tick, 'world_size': [self.config.width, self.config.height],
            'self': {'id': agent.id, 'species': agent.species, 'x': agent.x, 'y': agent.y,
                     'hp': agent.hp, 'food': agent.food, 'material': agent.material,
                     'genome': asdict(agent.genome)},
            'visible_cells': visible_cells, 'visible_agents': seen_agents,
            'messages': [m.copy() for m in agent.inbox],
            'base': {'x': self.bases[agent.species][0], 'y': self.bases[agent.species][1],
                     'food': self.stores[agent.species]['food'],
                     'material': self.stores[agent.species]['material']},
        }

    def observations(self) -> dict[str, dict]:
        return {a.id: self.observe(a) for a in self.agents.values()}

    def _reachable_paths(self, avatar: Avatar, occupied: dict[tuple[int, int], Avatar]) -> dict[tuple[int, int], list]:
        start = avatar.position
        paths = {start: []}
        q = deque([start])
        while q:
            px, py = q.popleft()
            if len(paths[(px, py)]) >= avatar.genome.y:
                continue
            for dx, dy in DIRECTIONS:
                nx, ny = px + dx, py + dy
                if (not self.inside(nx, ny) or (nx, ny) in paths or (nx, ny) in occupied
                        or self.cell(nx, ny).block_hp > 0):
                    continue
                # Диагональ разрешена, но нельзя проходить сквозь угол из двух стен.
                if dx and dy and (self.cell(px+dx, py).block_hp or self.cell(px, py+dy).block_hp):
                    continue
                paths[(nx, ny)] = paths[(px, py)] + [[nx, ny]]
                q.append((nx, ny))
        return paths

    @staticmethod
    def _coords(value) -> tuple[int, int] | None:
        if isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value):
            return value[0], value[1]
        return None

    @staticmethod
    def _amount(value) -> int | None:
        return value if type(value) is int and value > 0 else None

    def step(self, actions: dict[str, dict], *, spawn_first: bool = True) -> dict:
        """Один полный ход после получения одновременно собранных команд."""
        if self.status != 'running':
            return self.snapshot()
        self.last_events = []
        if spawn_first:
            self.spawn_resources()
        original_positions = self.occupants()
        valid = {}
        moves: dict[str, tuple[tuple[int, int], list]] = {}
        damages: dict[str, int] = {}
        block_damages: dict[tuple[int, int], int] = {}
        for agent_id in sorted(self.agents):
            actor = self.agents[agent_id]
            command = actions.get(agent_id, {'type': 'WAIT'})
            if not isinstance(command, dict):
                command = {'type': 'WAIT'}
            typ = str(command.get('type', 'WAIT')).upper()
            if typ not in ALLOWED_ACTIONS:
                self._log('invalid', f'{agent_id}: неизвестное действие {typ}')
                typ = 'WAIT'
            valid[agent_id] = (typ, command)
            actor.last_action = typ
            if isinstance(command.get('thought'), str):
                actor.thought = command['thought'][:240]
            if typ == 'MOVE':
                target = self._coords(command.get('target'))
                if target is None:
                    self._log('invalid', f'{agent_id}: MOVE требует target [x, y]')
                    continue
                paths = self._reachable_paths(actor, original_positions)
                if target not in paths or target == actor.position:
                    self._log('invalid', f'{agent_id}: недостижимая клетка {target}')
                    continue
                moves[agent_id] = (target, paths[target])
            elif typ == 'ATTACK':
                target = self._coords(command.get('target'))
                if target is None or max(abs(target[0]-actor.x), abs(target[1]-actor.y)) != 1 or not self.inside(*target):
                    self._log('invalid', f'{agent_id}: ATTACK требует соседнюю клетку')
                    continue
                other = original_positions.get(target)
                if other:
                    damages[other.id] = damages.get(other.id, 0) + actor.genome.z
                    self._log('attack', f'{agent_id} атакует {other.id}: −{actor.genome.z} HP', actor=agent_id, target=other.id)
                elif self.cell(*target).block_hp:
                    block_damages[target] = block_damages.get(target, 0) + actor.genome.z
                    self._log('attack', f'{agent_id} атакует блок {target}', actor=agent_id)

        # Два агента, желающие встать на одну клетку: выигрывает один по фиксированному ID.
        # Это детерминировано и не зависит от порядка обхода словаря.
        destinations: dict[tuple[int, int], list[str]] = {}
        for aid, (dest, _) in moves.items():
            destinations.setdefault(dest, []).append(aid)
        for dest, ids in sorted(destinations.items()):
            winner_id = min(ids)
            a = self.agents[winner_id]
            path = moves[winner_id][1]
            old = [a.x, a.y]
            a.x, a.y = dest
            self._log('move', f'{a.id}: {old} → {list(dest)}', actor=a.id, path=path)
            for loser in ids:
                if loser != winner_id:
                    self._log('conflict', f'{loser}: клетка занята конкурентом')

        # Не боевые действия применяются последовательно в стабильном порядке.
        # Конфликт ресурса разрешается по доступному остатку.
        current_positions = self.occupants()
        for agent_id in sorted(self.agents):
            a = self.agents[agent_id]
            typ, cmd = valid[agent_id]
            if typ not in ('PICKUP', 'DROP', 'BUILD', 'TALK', 'THINK'):
                continue
            if typ == 'THINK':
                self._log('think', f'{a.id} пересматривает план', actor=a.id)
            elif typ in ('PICKUP', 'DROP'):
                resource = cmd.get('resource', 'food')
                amount = self._amount(cmd.get('amount'))
                if resource not in ('food', 'material') or amount is None:
                    self._log('invalid', f'{a.id}: некорректный ресурс или количество')
                    continue
                c = self.cell(a.x, a.y)
                store = self.stores[a.species]
                if typ == 'PICKUP':
                    # На базе сперва берем ресурс с земли, затем со склада.
                    source = (c.__dict__ if getattr(c, resource) > 0 or
                              not self.in_base(a.species, a.x, a.y) else store)
                    dest = a.__dict__
                else:
                    source = a.__dict__
                    dest = store if self.in_base(a.species, a.x, a.y) else c.__dict__
                moved = min(amount, source[resource])
                if moved:
                    source[resource] -= moved
                    dest[resource] += moved
                    self._log('resource', f'{a.id}: {typ.lower()} {moved} {resource}', actor=a.id)
            elif typ == 'BUILD':
                target = self._coords(cmd.get('target'))
                if (target is None or not self.inside(*target) or
                        max(abs(target[0]-a.x), abs(target[1]-a.y)) > 1 or
                        a.material < BUILD_COST or target in current_positions):
                    self._log('invalid', f'{a.id}: нельзя построить блок')
                    continue
                cell = self.cell(*target)
                if cell.block_hp or cell.food or cell.material:
                    self._log('invalid', f'{a.id}: клетка занята ресурсами/блоком')
                    continue
                a.material -= BUILD_COST
                cell.block_hp = BLOCK_HP
                self._log('build', f'{a.id} построил блок на {target}', actor=a.id)
            elif typ == 'TALK':
                recipient_id = cmd.get('recipient_id')
                recipient = self.agents.get(recipient_id)
                message = cmd.get('message')
                if (recipient is None or not isinstance(message, str) or not message.strip()
                        or max(abs(a.x-recipient.x), abs(a.y-recipient.y)) > 1 or recipient.id == a.id):
                    self._log('invalid', f'{a.id}: TALK возможен только с соседним агентом')
                    continue
                msg = {'from': a.id, 'text': message[:240], 'tick': self.tick+1}
                recipient.inbox.append(msg)
                recipient.inbox = recipient.inbox[-10:]
                self._log('talk', f'{a.id} → {recipient.id}: {msg["text"]}', actor=a.id, target=recipient.id)

        # Урон одновременный: оба участника успевают атаковать даже при смертельном ударе.
        for agent_id, damage in damages.items():
            self.agents[agent_id].hp -= damage
        for (x, y), damage in block_damages.items():
            c = self.cell(x, y)
            c.block_hp = max(0, c.block_hp - damage)
            if c.block_hp == 0:
                self._log('break', f'Блок ({x},{y}) разрушен')
        self.tick += 1
        for a in self.agents.values():
            a.age += 1
            consumed = min(a.food, a.genome.x)
            a.food -= consumed
            if consumed < a.genome.x:
                a.hp -= STARVATION_DAMAGE
                if a.age % 3 == 0:
                    self._log('hunger', f'{a.id} голодает: −{STARVATION_DAMAGE} HP', actor=a.id)
            # Новые сообщения получены после этого хода; в следующем observe() их увидят.
        dead = [a for a in self.agents.values() if a.hp <= 0]
        for a in dead:
            self._log('death', f'{a.id} погиб', actor=a.id)
            # Содержимое инвентаря возвращается миру.
            c = self.cell(a.x, a.y)
            c.food += a.food
            c.material += a.material
            del self.agents[a.id]
        survivors = {a.species for a in self.agents.values()}
        if len(survivors) == 1:
            self.status = 'finished'
            self.winner = next(iter(survivors))
            self._log('end', f'Победил вид {self.winner}')
        elif not survivors:
            self.status = 'finished'
            self._log('end', 'Все виды вымерли: победителя нет')
        elif self.tick >= self.config.limit_ticks:
            self.status = 'finished'
            # Лимит: больше живых, больше ресурсов, выше суммарный HP.
            score = []
            for species in SPECIES:
                team = [a for a in self.agents.values() if a.species == species]
                resources = (self.stores[species]['food'] + self.stores[species]['material']
                             + sum(a.food+a.material for a in team))
                score.append((len(team), resources, sum(a.hp for a in team), species))
            best_metrics = max(row[:3] for row in score)
            best = [row[-1] for row in score if row[:3] == best_metrics]
            self.winner = best[0] if len(best) == 1 else None
            msg = f'Лимит ходов. Победитель по статистике: {self.winner}' if self.winner else 'Лимит ходов. Равенство: ничья'
            self._log('end', msg)
        self._record_history()
        return self.snapshot()

    def _record_history(self):
        self.history.append({'tick': self.tick, **{sp: sum(1 for a in self.agents.values() if a.species == sp)
                                                   for sp in SPECIES}})

    def snapshot(self) -> dict[str, Any]:
        """Полное состояние только для наблюдателя/UI. Не передавать агентам!"""
        agents = []
        for a in self.agents.values():
            agents.append({'id': a.id, 'species': a.species, 'x': a.x, 'y': a.y,
                           'hp': a.hp, 'max_hp': a.genome.k, 'food': a.food,
                           'material': a.material, 'age': a.age,
                           'genome': asdict(a.genome), 'thought': a.thought,
                           'last_action': a.last_action})
        return {
            'tick': self.tick, 'limit_ticks': self.config.limit_ticks, 'status': self.status,
            'winner': self.winner, 'seed': self.config.seed,
            'width': self.config.width, 'height': self.config.height,
            'cells': [[{'food': c.food, 'material': c.material, 'block_hp': c.block_hp}
                       for c in row] for row in self.grid],
            'bases': {k: list(v) for k, v in self.bases.items()},
            'stores': self.stores,
            'agents': agents,
            'counts': {sp: sum(a.species == sp for a in self.agents.values()) for sp in SPECIES},
            'events': self.events[-55:],
            'last_events': self.last_events,
            'history': self.history,
            'rules': {'view_radius': VIEW_RADIUS, 'build_cost': BUILD_COST,
                      'starvation_damage': STARVATION_DAMAGE, 'block_hp': BLOCK_HP,
                      'genome_budget': 'alpha*Z + beta*Y + delta*K <= gamma*X'},
        }
