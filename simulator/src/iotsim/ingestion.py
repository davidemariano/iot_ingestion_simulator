"""Servizio di ingestion (§8.3): validazione, arricchimento, deduplicazione, scrittura a lotti,
PUBACK dopo il commit, notifica "nuove misure" ai modelli NRT."""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError

from .broker import BrokerMessage, EmbeddedBroker
from .clock import Clock
from .domain import Sensor, Station
from .geo import haversine_m
from .messages import AccelTelemetry, GpsTelemetry, ScalarTelemetry, telemetry_adapter
from .metrics import Metrics, RingLog
from .registry import StationRegistry
from .storage import (
    T_MEASUREMENTS, T_MOTION, T_POSITIONS, DatabaseUnavailable, MotionSample, PositionSample,
    Repository, Sample, ScalarSample,
)
from .units import normalize

FUTURE_TOLERANCE = timedelta(seconds=60)
LATE_TOLERANCE = timedelta(hours=24)
GPS_MAX_DISTANCE_M = 5_000  # oltre: fix chiaramente errato, non "stazione spostata"
SPIKE_WINDOW_S = 2 * 3600
CONSTANT_SPAN_S = 6 * 3600


class Causa:
    SCHEMA = "schema"
    TOPIC = "topic_incoerente"
    SCONOSCIUTO = "canale_sconosciuto"
    DISATTIVATO = "canale_disattivato"
    TIPO = "tipo_incoerente"
    UNITA = "unita_non_valida"
    INTERVALLO = "fuori_intervallo"
    FUTURO = "timestamp_futuro"
    SCADUTO = "timestamp_scaduto"
    DUPLICATO = "duplicato"


@dataclass(slots=True)
class _Accepted:
    msg: BrokerMessage
    sample: Sample
    station: Station
    sensor: Sensor
    table: str
    seq: int
    sent_at: float
    display: str
    normalizzato: bool
    sospetto: str | None


@dataclass(slots=True)
class _Discarded:
    msg: BrokerMessage
    causa: str
    dettaglio: str
    device_uid: str | None = None
    seq: int | None = None


class QualityChecker:
    """Marca come sospette le misure anomale (RF-IOT-16, §8.5): picco |z| > 4 su 2 h, costante > 6 h."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.costanti: dict[uuid.UUID, float] = {}  # sensor_id -> inizio della serie costante

    def assess(self, sensor: Sensor, ts: float, value: float) -> str | None:
        t = sensor.tipo
        series = self.repo.scalars.get(sensor.id)
        if series is None or len(series) < 10:
            return None
        if t.controllo_costante:
            r = round(value, t.decimali)
            i = series.t.__len__() - 1
            # parte dall'ultimo campione precedente a ts
            while i >= 0 and series.t[i] >= ts:
                i -= 1
            run_start = ts
            while i >= 0 and round(series.v[i], t.decimali) == r:
                run_start = series.t[i]
                if ts - run_start >= CONSTANT_SPAN_S:
                    self.costanti[sensor.id] = run_start
                    return "costante"
                i -= 1
            self.costanti.pop(sensor.id, None)
        if t.controllo_picchi:
            _, v, q = series.window(ts - SPIKE_WINDOW_S, ts - 1e-6)
            vals = [x for x, qq in zip(v, q) if qq == 0]
            if len(vals) >= 10:
                mean = sum(vals) / len(vals)
                std = math.sqrt(sum((x - mean) ** 2 for x in vals) / (len(vals) - 1))
                dev = abs(value - mean)
                if dev > t.soglia_picco and dev / max(std, 1e-3) > 4:
                    return "picco"
        return None


class IngestionService:
    def __init__(self, broker: EmbeddedBroker, registry: StationRegistry, repo: Repository,
                 metrics: Metrics, clock: Clock, msglog: RingLog,
                 on_new_data: Callable[[set[uuid.UUID]], None] | None = None,
                 batch_max: int = 500, flush_interval_s: float = 1.0) -> None:
        self.broker = broker
        self.registry = registry
        self.repo = repo
        self.metrics = metrics
        self.clock = clock
        self.msglog = msglog
        self.quality = QualityChecker(repo)
        self.on_new_data = on_new_data
        self.batch_max = batch_max
        self.flush_interval_s = flush_interval_s
        self.log_accepted_per_batch = 40
        self.down_until = 0.0

    # --- validazione e arricchimento --------------------------------------------------
    def _validate(self, m: BrokerMessage, now: datetime) -> _Accepted | _Discarded:
        try:
            raw = json.loads(m.payload)
            msg = telemetry_adapter.validate_python(raw)
        except (ValueError, ValidationError) as exc:
            detail = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "JSON non valido"
            return _Discarded(m, Causa.SCHEMA, detail)

        parts = m.topic.split("/")
        if len(parts) != 4 or parts[1] != msg.gateway_id or parts[2] != msg.device_uid:
            return _Discarded(m, Causa.TOPIC, "topic e payload non coincidono", msg.device_uid, msg.seq)

        resolved = self.registry.resolve(msg.device_uid)
        if resolved is None:
            return _Discarded(m, Causa.SCONOSCIUTO, msg.device_uid, msg.device_uid, msg.seq)
        station, sensor = resolved
        if station.gateway_id != msg.gateway_id:
            return _Discarded(m, Causa.TOPIC, "gateway non coerente col canale", msg.device_uid, msg.seq)
        if not sensor.attivo:
            return _Discarded(m, Causa.DISATTIVATO, "stazione dismessa", msg.device_uid, msg.seq)
        if msg.type != sensor.sensor_type:
            return _Discarded(m, Causa.TIPO, f"{msg.type} ≠ {sensor.sensor_type}", msg.device_uid, msg.seq)

        if msg.ts > now + FUTURE_TOLERANCE:
            return _Discarded(m, Causa.FUTURO, f"{(msg.ts - now).total_seconds():.0f} s nel futuro",
                              msg.device_uid, msg.seq)
        if msg.ts < now - LATE_TOLERANCE:
            return _Discarded(m, Causa.SCADUTO, "più vecchio di 24 h", msg.device_uid, msg.seq)

        t = sensor.tipo
        ts = msg.ts.timestamp()
        sent_at = msg.sent_at.timestamp() if msg.sent_at else m.sent_at_real
        normalizzato = msg.unit != t.unit
        sospetto: str | None = None

        if isinstance(msg, ScalarTelemetry):
            value = normalize(msg.value, msg.unit, t.unit)
            if value is None:
                return _Discarded(m, Causa.UNITA, f"unità «{msg.unit}»", msg.device_uid, msg.seq)
            if not (t.min_valid <= value <= t.max_valid):
                return _Discarded(m, Causa.INTERVALLO, f"{value:g} {t.unit} fuori da [{t.min_valid:g}, {t.max_valid:g}]",
                                  msg.device_uid, msg.seq)
            sospetto = self.quality.assess(sensor, ts, value)
            sample: Sample = ScalarSample(sensor.id, ts, value, 1 if sospetto else 0)
            table = T_MEASUREMENTS
            display = f"{value:.{t.decimali}f} {t.unit}"
        elif isinstance(msg, GpsTelemetry):
            if msg.unit not in ("deg", "°"):
                return _Discarded(m, Causa.UNITA, f"unità «{msg.unit}»", msg.device_uid, msg.seq)
            v = msg.value
            if haversine_m(v.lat, v.lon, station.lat, station.lon) > GPS_MAX_DISTANCE_M:
                return _Discarded(m, Causa.INTERVALLO, "posizione non coerente con il terreno",
                                  msg.device_uid, msg.seq)
            sample = PositionSample(sensor.id, ts, v.lat, v.lon, v.alt_m, v.hdop, v.sats)
            table = T_POSITIONS
            display = f"{v.lat:.5f}, {v.lon:.5f}"
        else:
            assert isinstance(msg, AccelTelemetry)
            v = msg.value
            axes = [normalize(a, msg.unit, "g") for a in (v.ax, v.ay, v.az)]
            if any(a is None for a in axes):
                return _Discarded(m, Causa.UNITA, f"unità «{msg.unit}»", msg.device_uid, msg.seq)
            ax, ay, az = axes  # type: ignore[misc]
            if any(not (t.min_valid <= a <= t.max_valid) for a in (ax, ay, az)):
                return _Discarded(m, Causa.INTERVALLO, "asse oltre ±16 g", msg.device_uid, msg.seq)
            norm = math.sqrt(ax * ax + ay * ay + az * az)
            incl = math.degrees(math.acos(max(-1.0, min(1.0, az / norm)))) if norm > 1e-9 else 90.0
            sample = MotionSample(sensor.id, ts, ax, ay, az, incl)
            table = T_MOTION
            display = f"|a| {norm:.2f} g · {incl:.1f}°"

        return _Accepted(m, sample, station, sensor, table, msg.seq, sent_at, display, normalizzato, sospetto)

    # --- flush del batch ------------------------------------------------------------------
    def _prepare(self, msgs: list[BrokerMessage]) -> tuple[list[_Accepted], list[_Discarded]]:
        now = self.clock.now()
        accepted: list[_Accepted] = []
        discarded: list[_Discarded] = []
        keys: set[tuple[uuid.UUID, float]] = set()
        for m in msgs:
            r = self._validate(m, now)
            if isinstance(r, _Accepted):
                key = (r.sensor.id, r.sample.ts)
                if key in keys or self.repo.is_duplicate(r.sample):
                    discarded.append(_Discarded(m, Causa.DUPLICATO, "stesso canale e timestamp",
                                                r.sensor.device_uid, r.seq))
                    continue
                keys.add(key)
                accepted.append(r)
            else:
                discarded.append(r)
        return accepted, discarded

    def _commit(self, accepted: list[_Accepted]) -> set[uuid.UUID]:
        """INSERT a lotti + UPDATE ultima_misura_il / ultimo_contatto_il. Solleva DatabaseUnavailable."""
        self.repo.bulk_insert([a.sample for a in accepted])
        tz = self.clock.now().tzinfo
        touched: set[uuid.UUID] = set()
        for a in accepted:
            meas = datetime.fromtimestamp(a.sample.ts, tz)
            if a.sensor.ultima_misura_il is None or meas > a.sensor.ultima_misura_il:
                a.sensor.ultima_misura_il = meas
            if a.station.ultimo_contatto_il is None or meas > a.station.ultimo_contatto_il:
                a.station.ultimo_contatto_il = meas
            touched.add(a.station.id)
        return touched

    def flush(self) -> int:
        """Elabora fino a ``batch_max`` messaggi; PUBACK solo dopo il commit. Ritorna i messaggi elaborati."""
        if self.is_down:
            return 0
        msgs = self.broker.fetch(self.batch_max)
        if not msgs:
            return 0
        accepted, discarded = self._prepare(msgs)
        try:
            touched = self._commit(accepted)
        except DatabaseUnavailable:
            # nessun PUBACK: la sessione persistente riconsegnerà tutto il batch
            self.metrics.errori_db += 1
            self.broker.reconnect_session()
            return 0
        commit_real = time.time()
        self.broker.puback([m.mid for m in msgs])

        # contatori solo dopo il commit, così un retry non conta due volte
        met = self.metrics
        met.ricevuti += len(msgs)
        for a in accepted:
            met.salvati[a.table] += 1
            met.observe_seq(a.sensor.device_uid, a.seq)
            if a.normalizzato:
                met.normalizzati += 1
            if a.sospetto:
                met.sospetti += 1
        for d in discarded:
            met.scartati[d.causa] += 1
            if d.device_uid is not None and d.seq is not None:
                met.observe_seq(d.device_uid, d.seq)
        met.committed(len(accepted), [max(0.0, commit_real - a.sent_at) for a in accepted])

        self._log(accepted, discarded, commit_real)
        if touched and self.on_new_data:
            self.on_new_data(touched)
        return len(msgs)

    @property
    def is_down(self) -> bool:
        return time.monotonic() < self.down_until

    def restart(self, downtime_s: float = 5.0) -> int:
        """Riavvio sotto carico (S3): il batch in corso viene salvato ma il processo muore prima
        del PUBACK. Il broker lo riconsegna con DUP e la chiave primaria lo deduplica."""
        msgs = self.broker.fetch(self.batch_max)
        if msgs:
            accepted, _ = self._prepare(msgs)
            try:
                self._commit(accepted)
            except DatabaseUnavailable:
                pass
        n = self.broker.reconnect_session()
        self.down_until = time.monotonic() + downtime_s
        return n

    def _log(self, accepted: list[_Accepted], discarded: list[_Discarded], commit_real: float) -> None:
        step = max(1, len(accepted) // self.log_accepted_per_batch)
        for a in accepted[::step]:
            self.msglog.add({
                "esito": "sospetto" if a.sospetto else "salvato",
                "causa": a.sospetto,
                "topic": a.msg.topic,
                "tipo": a.sensor.sensor_type,
                "stazione": a.station.nome,
                "valore": a.display + (" · normalizzato" if a.normalizzato else ""),
                "ts": datetime.fromtimestamp(a.sample.ts, self.clock.now().tzinfo).isoformat(),
                "latenza_ms": round((commit_real - a.sent_at) * 1000),
                "dup": a.msg.dup,
            })
        for d in discarded:
            self.msglog.add({
                "esito": "scartato",
                "causa": d.causa,
                "topic": d.msg.topic,
                "tipo": None,
                "stazione": None,
                "valore": d.dettaglio,
                "ts": None,
                "latenza_ms": None,
                "dup": d.msg.dup,
            })
