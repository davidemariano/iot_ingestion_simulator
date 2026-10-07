"""Persistenza in memoria che rispecchia le tabelle ``iot_*`` del §7.3.

- Chiave primaria (sensor_id, measured_at) e ``ON CONFLICT DO NOTHING`` → idempotenza (RNF-IOT-05).
- Partizioni mensili emulate: ogni riga è contata nella partizione del proprio mese,
  o nella ``default`` se la partizione non esiste ancora (va segnalato, §7.4).
- Serie in ``array`` compatti ordinati per timestamp: 100 stazioni × 8 giorni stanno in ~100 MB.
- ``available = False`` simula il database non raggiungibile (scenario S3).
"""

from __future__ import annotations

import math
import uuid
from array import array
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from .domain import Alert, Indicator, StatoStazione, StatusTransition

T_MEASUREMENTS = "iot_measurements"
T_POSITIONS = "iot_station_positions"
T_MOTION = "iot_station_motion"
SAMPLE_TABLES = (T_MEASUREMENTS, T_POSITIONS, T_MOTION)


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScalarSample:
    sensor_id: uuid.UUID
    ts: float  # epoch s (UTC), tempo simulato della misura
    value: float
    quality: int = 0


@dataclass(frozen=True, slots=True)
class PositionSample:
    sensor_id: uuid.UUID
    ts: float
    lat: float
    lon: float
    alt_m: float | None
    hdop: float | None
    sats: int | None


@dataclass(frozen=True, slots=True)
class MotionSample:
    sensor_id: uuid.UUID
    ts: float
    ax: float
    ay: float
    az: float
    inclinazione_deg: float


Sample = ScalarSample | PositionSample | MotionSample


class ScalarSeries:
    __slots__ = ("t", "v", "q")

    def __init__(self) -> None:
        self.t = array("d")
        self.v = array("d")
        self.q = array("b")

    def __len__(self) -> int:
        return len(self.t)

    def insert(self, ts: float, value: float, quality: int) -> bool:
        i = bisect_left(self.t, ts)
        if i < len(self.t) and self.t[i] == ts:
            return False
        if i == len(self.t):
            self.t.append(ts)
            self.v.append(value)
            self.q.append(quality)
        else:
            self.t.insert(i, ts)
            self.v.insert(i, value)
            self.q.insert(i, quality)
        return True

    def contains(self, ts: float) -> bool:
        i = bisect_left(self.t, ts)
        return i < len(self.t) and self.t[i] == ts

    def window(self, t0: float, t1: float) -> tuple[array, array, array]:
        i, j = bisect_left(self.t, t0), bisect_right(self.t, t1)
        return self.t[i:j], self.v[i:j], self.q[i:j]

    def last(self) -> tuple[float, float, int] | None:
        if not self.t:
            return None
        return self.t[-1], self.v[-1], self.q[-1]

    def prune_before(self, t_min: float) -> int:
        i = bisect_left(self.t, t_min)
        if i:
            del self.t[:i]
            del self.v[:i]
            del self.q[:i]
        return i


class VectorSeries:
    """Serie per GPS e accelerometro: tempi in ``array``, righe come tuple."""

    __slots__ = ("t", "rows")

    def __init__(self) -> None:
        self.t = array("d")
        self.rows: list[tuple] = []

    def __len__(self) -> int:
        return len(self.t)

    def insert(self, ts: float, row: tuple) -> bool:
        i = bisect_left(self.t, ts)
        if i < len(self.t) and self.t[i] == ts:
            return False
        self.t.insert(i, ts)
        self.rows.insert(i, row)
        return True

    def contains(self, ts: float) -> bool:
        i = bisect_left(self.t, ts)
        return i < len(self.t) and self.t[i] == ts

    def window(self, t0: float, t1: float) -> list[tuple[float, tuple]]:
        i, j = bisect_left(self.t, t0), bisect_right(self.t, t1)
        return list(zip(self.t[i:j], self.rows[i:j]))

    def last(self) -> tuple[float, tuple] | None:
        if not self.t:
            return None
        return self.t[-1], self.rows[-1]

    def prune_before(self, t_min: float) -> int:
        i = bisect_left(self.t, t_min)
        if i:
            del self.t[:i]
            del self.rows[:i]
        return i


@dataclass(slots=True)
class HourAgg:
    n: int
    v_min: float
    v_max: float
    v_avg: float
    v_sum: float | None


def month_key(ts: float) -> str:
    d = datetime.fromtimestamp(ts, UTC)
    return f"{d.year:04d}_{d.month:02d}"


def circular_mean_deg(values) -> float:
    """atan2(media(sin θ), media(cos θ)) — la media di 350° e 10° è 0°, non 180° (§7.2)."""
    s = c = 0.0
    n = 0
    for v in values:
        r = math.radians(v)
        s += math.sin(r)
        c += math.cos(r)
        n += 1
    if n == 0:
        return float("nan")
    return math.degrees(math.atan2(s / n, c / n)) % 360


class Repository:
    def __init__(self) -> None:
        self.scalars: dict[uuid.UUID, ScalarSeries] = {}
        self.positions: dict[uuid.UUID, VectorSeries] = {}
        self.motion: dict[uuid.UUID, VectorSeries] = {}
        self.hourly: dict[uuid.UUID, dict[int, HourAgg]] = {}
        self.indicators: dict[uuid.UUID, deque[Indicator]] = {}
        self.alerts: list[Alert] = []
        self.status_log: list[StatusTransition] = []
        self.partitions: dict[str, int] = {}  # 'iot_measurements_2026_10' -> righe
        self.available = True
        self._alert_seq = 0
        self._log_seq = 0

    # --- partizioni -----------------------------------------------------------
    def ensure_partition(self, table: str, key: str) -> bool:
        name = f"{table}_{key}"
        if name in self.partitions:
            return False
        self.partitions[name] = 0
        return True

    def _count(self, table: str, ts: float) -> None:
        name = f"{table}_{month_key(ts)}"
        if name in self.partitions:
            self.partitions[name] += 1
        else:
            self.partitions[f"{table}_default"] = self.partitions.get(f"{table}_default", 0) + 1

    def default_rows(self) -> int:
        return sum(v for k, v in self.partitions.items() if k.endswith("_default"))

    # --- scrittura a lotti ------------------------------------------------------
    def is_duplicate(self, s: Sample) -> bool:
        if isinstance(s, ScalarSample):
            series = self.scalars.get(s.sensor_id)
        elif isinstance(s, PositionSample):
            series = self.positions.get(s.sensor_id)
        else:
            series = self.motion.get(s.sensor_id)
        return series is not None and series.contains(s.ts)

    def bulk_insert(self, batch: list[Sample]) -> tuple[int, int]:
        """INSERT ... ON CONFLICT DO NOTHING. Ritorna (inserite, duplicate)."""
        if not self.available:
            raise DatabaseUnavailable("connessione al database non disponibile")
        inserted = dup = 0
        for s in batch:
            if isinstance(s, ScalarSample):
                ok = self.scalars.setdefault(s.sensor_id, ScalarSeries()).insert(s.ts, s.value, s.quality)
                table = T_MEASUREMENTS
            elif isinstance(s, PositionSample):
                ok = self.positions.setdefault(s.sensor_id, VectorSeries()).insert(
                    s.ts, (s.lat, s.lon, s.alt_m, s.hdop, s.sats))
                table = T_POSITIONS
            else:
                ok = self.motion.setdefault(s.sensor_id, VectorSeries()).insert(
                    s.ts, (s.ax, s.ay, s.az, s.inclinazione_deg))
                table = T_MOTION
            if ok:
                inserted += 1
                self._count(table, s.ts)
            else:
                dup += 1
        return inserted, dup

    # --- aggregati orari (RF-IOT-13) -------------------------------------------
    def aggregate_hour(self, sensor_id: uuid.UUID, hour_start: float, aggregazione: str) -> HourAgg | None:
        series = self.scalars.get(sensor_id)
        if series is None:
            return None
        _, v, q = series.window(hour_start, hour_start + 3600 - 1e-6)
        vals = [x for x, qq in zip(v, q) if qq == 0]
        if not vals:
            return None
        if aggregazione == "circolare":
            avg = circular_mean_deg(vals)
        else:
            avg = sum(vals) / len(vals)
        agg = HourAgg(n=len(vals), v_min=min(vals), v_max=max(vals), v_avg=avg,
                      v_sum=sum(vals) if aggregazione == "somma" else None)
        self.hourly.setdefault(sensor_id, {})[int(hour_start)] = agg
        return agg

    # --- retention --------------------------------------------------------------
    def apply_retention(self, t_min: float) -> int:
        removed = 0
        for coll in (self.scalars, self.positions, self.motion):
            for series in coll.values():
                removed += series.prune_before(t_min)
        return removed

    # --- indicatori, allerte, log degli stati -----------------------------------
    def add_indicator(self, ind: Indicator, maxlen: int = 2000) -> None:
        self.indicators.setdefault(ind.station_id, deque(maxlen=maxlen)).append(ind)

    def open_alert(self, station_id: uuid.UUID, tipo: str, severita: str, messaggio: str,
                   at: datetime) -> Alert:
        self._alert_seq += 1
        a = Alert(self._alert_seq, station_id, tipo, severita, messaggio, at)
        self.alerts.append(a)
        return a

    def open_alerts(self, station_id: uuid.UUID) -> list[Alert]:
        return [a for a in self.alerts if a.station_id == station_id and a.aperta]

    def find_open(self, station_id: uuid.UUID, tipo: str) -> Alert | None:
        for a in reversed(self.alerts):
            if a.station_id == station_id and a.tipo == tipo and a.aperta:
                return a
        return None

    def log_transition(self, station_id: uuid.UUID, da: StatoStazione | None, a: StatoStazione,
                       motivo: str, at: datetime) -> StatusTransition:
        self._log_seq += 1
        t = StatusTransition(self._log_seq, station_id, da, a, motivo, at)
        self.status_log.append(t)
        return t
