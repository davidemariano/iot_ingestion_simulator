"""Osservabilità (RNF-IOT-12): contatori per causa, latenza, throughput, perdite via ``seq``,
più i due registri mostrati dalla console: messaggi recenti ed eventi di dominio."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass(slots=True)
class SeqTracker:
    """Perdite = sequenze attese − sequenze distinte arrivate all'ingestion (§14).

    Tiene in memoria solo le ultime ``window`` sequenze: quelle più vecchie vengono
    consolidate in ``lost`` (un messaggio in ritardo di oltre ``window`` intervalli
    sarebbe comunque fuori dalla tolleranza di 24 h dei dati tardivi).
    """

    floor: int  # tutte le seq <= floor sono già consolidate
    last: int
    seen: set[int] = field(default_factory=set)
    lost: int = 0
    window: int = 600

    def observe(self, seq: int) -> None:
        if seq <= self.floor:
            return
        self.last = max(self.last, seq)
        self.seen.add(seq)
        if len(self.seen) > 2 * self.window:
            new_floor = self.last - self.window
            old = {s for s in self.seen if s <= new_floor}
            self.lost += (new_floor - self.floor) - len(old)
            self.seen -= old
            self.floor = new_floor

    @property
    def missing(self) -> int:
        return self.lost + (self.last - self.floor) - len(self.seen)


class Metrics:
    def __init__(self, latency_window: int = 20_000) -> None:
        self.ricevuti = 0
        self.salvati: Counter[str] = Counter()  # per tabella
        self.scartati: Counter[str] = Counter()  # per causa
        self.normalizzati = 0
        self.sospetti = 0
        self.batch = 0
        self.ultimo_batch = 0
        self.errori_db = 0
        self._lat: deque[float] = deque(maxlen=latency_window)
        self._rate: deque[tuple[float, int]] = deque()  # (t reale, n salvati)
        self._seq: dict[str, SeqTracker] = {}

    def observe_seq(self, device_uid: str, seq: int) -> None:
        tr = self._seq.get(device_uid)
        if tr is None:
            self._seq[device_uid] = SeqTracker(floor=seq - 1, last=seq, seen={seq})
        else:
            tr.observe(seq)

    def perdite(self) -> int:
        return sum(t.missing for t in self._seq.values())

    def committed(self, n_saved: int, latencies: list[float]) -> None:
        now = time.monotonic()
        self._lat.extend(latencies)
        self._rate.append((now, n_saved))
        self.batch += 1
        self.ultimo_batch = n_saved

    def throughput(self, window_s: float = 10.0) -> float:
        now = time.monotonic()
        while self._rate and now - self._rate[0][0] > window_s:
            self._rate.popleft()
        return sum(n for _, n in self._rate) / window_s

    def latency(self) -> dict[str, float | None]:
        vals = sorted(self._lat)
        return {
            "p50": percentile(vals, 0.50),
            "p95": percentile(vals, 0.95),
            "p99": percentile(vals, 0.99),
            "max": vals[-1] if vals else None,
            "campioni": len(vals),
        }

    def latency_histogram(self, edges: tuple[float, ...] = (0.25, 0.5, 1, 2, 5, 10)) -> list[dict[str, Any]]:
        counts = [0] * (len(edges) + 1)
        for v in self._lat:
            for i, e in enumerate(edges):
                if v <= e:
                    counts[i] += 1
                    break
            else:
                counts[-1] += 1
        labels = [f"≤ {e:g} s" for e in edges] + [f"> {edges[-1]:g} s"]
        return [{"fascia": lbl, "n": c} for lbl, c in zip(labels, counts)]


class RingLog:
    """Registro circolare con cursore monotono, per lo stream SSE della console."""

    def __init__(self, maxlen: int) -> None:
        self._items: deque[tuple[int, dict[str, Any]]] = deque(maxlen=maxlen)
        self._seq = 0

    def add(self, item: dict[str, Any]) -> None:
        self._seq += 1
        self._items.append((self._seq, item))

    def since(self, cursor: int, limit: int = 200) -> tuple[int, list[dict[str, Any]]]:
        out = [it for s, it in self._items if s > cursor]
        return self._seq, out[-limit:]

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return [it for _, it in list(self._items)[-limit:]]

    @property
    def cursor(self) -> int:
        return self._seq
