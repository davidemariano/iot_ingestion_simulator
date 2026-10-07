from __future__ import annotations

from datetime import UTC, datetime

import pytest

from iotsim.clock import ManualClock
from iotsim.engine import Engine
from iotsim.lands import DEMO_USER_ID

START = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


class Harness:
    def __init__(self, engine: Engine, clock: ManualClock) -> None:
        self.e = engine
        self.clock = clock

    def advance(self, seconds: float, step: float = 30) -> None:
        """Avanza il tempo simulato eseguendo tick e flush come farebbero i due loop del servizio."""
        done = 0.0
        while done < seconds:
            dt = min(step, seconds - done)
            self.clock.advance(dt)
            self.e.tick()
            self.e.drain()
            done += dt

    def register(self, land_index: int = 0, nome: str = "Test"):
        land = list(self.e.registry.lands.values())[land_index]
        lat, lon = land.random_point(self.e.rng)
        return self.e.registry.register(DEMO_USER_ID, nome, lat, lon)


@pytest.fixture
def h() -> Harness:
    clock = ManualClock(START)
    return Harness(Engine(seed=7, clock=clock, demo=False), clock)
