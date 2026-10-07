"""Orologio simulato con fattore di compressione (§13.3: es. ×20).

Tutte le scadenze del dominio (cadenza, completezza, debounce, aggregazioni) sono
in tempo simulato; ``sent_at`` e la latenza usano invece l'orologio reale, come
richiesto dal §8.5.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


def utcnow() -> datetime:
    return datetime.now(UTC)


class Clock(Protocol):
    factor: float
    paused: bool

    def now(self) -> datetime: ...

    def set_factor(self, factor: float) -> None: ...

    def set_paused(self, paused: bool) -> None: ...


class SimClock:
    def __init__(self, start: datetime, factor: float = 20.0) -> None:
        self._sim_anchor = start
        self._real_anchor = time.monotonic()
        self.factor = factor
        self.paused = False

    def now(self) -> datetime:
        if self.paused:
            return self._sim_anchor
        elapsed = time.monotonic() - self._real_anchor
        return self._sim_anchor + timedelta(seconds=elapsed * self.factor)

    def _reanchor(self) -> None:
        self._sim_anchor = self.now()
        self._real_anchor = time.monotonic()

    def set_factor(self, factor: float) -> None:
        self._reanchor()
        self.factor = factor

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        if paused:
            self._reanchor()
            self.paused = True
        else:
            self.paused = False
            self._real_anchor = time.monotonic()


class ManualClock:
    """Orologio dei test: avanza solo con ``advance``."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self.factor = 1.0
        self.paused = False

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now

    def set_factor(self, factor: float) -> None:
        self.factor = factor

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
