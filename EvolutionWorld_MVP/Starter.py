"""Сборщик приложения. Позднее замените DummyAgent на реализацию из Agent/."""
from __future__ import annotations
from threading import RLock
from World import Config, World
from World.dummy import DummyAgent


class Starter:
    def __init__(self, config: Config):
        self.config = config
        self.lock = RLock()
        self.world = World(config)
        self.policy = self._build_stub_agents()

    def _build_stub_agents(self):
        return {agent_id: DummyAgent(agent_id, self.config.seed)
                for agent_id in self.world.agents}

    def state(self):
        with self.lock:
            return self.world.snapshot()

    def step(self):
        with self.lock:
            if self.world.status != 'running':
                return self.world.snapshot()
            # 1. Все агенты получают наблюдение из одного состояния.
            self.world.spawn_resources()
            observations = self.world.observations()
            # Сообщения после наблюдения считаются прочитанными однократно.
            for avatar in self.world.agents.values():
                avatar.inbox.clear()
            # 2. Команды выдаются отдельно от исполнения; ничего не меняют в world.
            actions = {aid: self.policy[aid].decide(obs) for aid, obs in observations.items()}
            # 3. Только мир проверяет команды и меняет состояние.
            return self.world.step(actions, spawn_first=False)

    def reset(self, seed=None):
        with self.lock:
            seed = self.config.seed if seed is None else int(seed)
            self.config = Config(**{**self.config.__dict__, 'seed': seed})
            self.world = World(self.config)
            self.policy = self._build_stub_agents()
            return self.world.snapshot()
