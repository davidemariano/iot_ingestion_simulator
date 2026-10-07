"""Modelli near-real-time separati dal SOM (§9): stress idrico, regole semplici, integrità,
qualità dei canali. Ogni modello produce indicatori; ``AlertService`` apre e chiude le allerte."""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .completeness import channel_label
from .domain import Indicator, Station, StatoStazione
from .geo import haversine_m
from .ingestion import QualityChecker
from .metrics import RingLog
from .registry import StationRegistry
from .soil import relative_available_water
from .storage import Repository

WINDOW_S = 3600


@dataclass(frozen=True, slots=True)
class Thresholds:
    p: float = 0.5  # frazione di esaurimento FAO-56
    raw_critica: float = 0.3
    peso_superficiale: float = 0.6  # 10–20 cm; il resto a 30–40 cm
    gelata_c: float = 0.0
    caldo_c: float = 35.0
    spostamento_m: float = 50.0
    inclinazione_deg: float = 20.0
    urto_g: float = 2.0
    urto_chiusura_s: float = 3600.0

    @property
    def raw_attenzione(self) -> float:
        return 1 - self.p


class NearRealTimeModel(Protocol):
    def evaluate(self, st: Station, now: datetime) -> list[Indicator]: ...


def _fmt(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}".replace(".", ",")


class AlertService:
    def __init__(self, repo: Repository, events: RingLog) -> None:
        self.repo = repo
        self.events = events

    def _event(self, st: Station, verb: str, tipo: str, severita: str, msg: str, at: datetime) -> None:
        self.events.add({"tipo": "allerta", "azione": verb, "station_id": str(st.id), "stazione": st.nome,
                         "allerta": tipo, "severita": severita, "messaggio": msg, "sim_ts": at.isoformat()})

    def raise_(self, st: Station, tipo: str, severita: str, msg: str, at: datetime) -> None:
        a = self.repo.find_open(st.id, tipo)
        if a is None:
            self.repo.open_alert(st.id, tipo, severita, msg, at)
            self._event(st, "aperta", tipo, severita, msg, at)
        else:
            if a.severita != severita:
                self._event(st, "aggiornata", tipo, severita, msg, at)
            a.severita, a.messaggio = severita, msg

    def clear(self, st: Station, tipo: str, at: datetime, msg: str = "") -> None:
        a = self.repo.find_open(st.id, tipo)
        if a is not None:
            a.chiusa_il = at
            self._event(st, "chiusa", tipo, a.severita, msg or a.messaggio, at)


class WaterStressModel:
    """§9.2: RAW sulla zona radicale, soglie FAO-56; esclude le misure sospette."""

    def __init__(self, registry: StationRegistry, repo: Repository, alerts: AlertService, th: Thresholds) -> None:
        self.registry, self.repo, self.alerts, self.th = registry, repo, alerts, th

    def root_zone_theta(self, st: Station, now: datetime) -> float | None:
        t1 = now.timestamp()
        theta = 0.0
        for depth, w in ((15, self.th.peso_superficiale), (35, 1 - self.th.peso_superficiale)):
            sensor = st.sensor_by_type("soil_moisture", depth)
            series = self.repo.scalars.get(sensor.id) if sensor else None
            if series is None:
                return None
            _, v, q = series.window(t1 - WINDOW_S, t1)
            vals = [x for x, qq in zip(v, q) if qq == 0]
            if not vals:
                return None
            theta += w * (sum(vals) / len(vals))
        return theta

    def evaluate(self, st: Station, now: datetime) -> list[Indicator]:
        theta = self.root_zone_theta(st, now)
        if theta is None:
            return []
        h = self.registry.lands[st.land_id].hydraulics
        raw = relative_available_water(theta, h)
        det = {"theta": round(theta, 2), "theta_fc": h.theta_fc, "theta_wp": h.theta_wp,
               "soglia_attenzione": self.th.raw_attenzione, "soglia_critica": self.th.raw_critica}
        msg = (f"Acqua disponibile al {_fmt(raw * 100, 0)}% "
               f"(θ {_fmt(theta)} %vol tra θWP {_fmt(h.theta_wp)} e θFC {_fmt(h.theta_fc)})")
        if raw < self.th.raw_critica:
            self.alerts.raise_(st, "stress_idrico", "critica", msg, now)
        elif raw < self.th.raw_attenzione:
            self.alerts.raise_(st, "stress_idrico", "attenzione", msg, now)
        elif raw >= self.th.raw_attenzione + 0.02:  # isteresi contro l'apri-e-chiudi
            self.alerts.clear(st, "stress_idrico", now, msg)
        return [Indicator(st.id, now, "raw_suolo", round(raw, 4), det),
                Indicator(st.id, now, "theta_radicale", round(theta, 2), None)]


class WeatherRulesModel:
    """Regole semplici del §9.2: rischio di gelata e stress termico."""

    def __init__(self, repo: Repository, alerts: AlertService, th: Thresholds) -> None:
        self.repo, self.alerts, self.th = repo, alerts, th

    def evaluate(self, st: Station, now: datetime) -> list[Indicator]:
        sensor = st.sensor_by_type("air_temperature")
        series = self.repo.scalars.get(sensor.id) if sensor else None
        if not series:
            return []
        _, v, q = series.window(now.timestamp() - WINDOW_S, now.timestamp())
        vals = [x for x, qq in zip(v, q) if qq == 0]
        if not vals:
            return []
        t = vals[-1]
        if t < self.th.gelata_c:
            self.alerts.raise_(st, "rischio_gelata", "critica", f"Temperatura dell'aria {_fmt(t)} °C", now)
        elif t > self.th.gelata_c + 1:
            self.alerts.clear(st, "rischio_gelata", now)
        if t > self.th.caldo_c:
            self.alerts.raise_(st, "stress_termico", "attenzione", f"Temperatura dell'aria {_fmt(t)} °C", now)
        elif t < self.th.caldo_c - 2:
            self.alerts.clear(st, "stress_termico", now)
        return [Indicator(st.id, now, "temperatura_aria", round(t, 2), None)]


class IntegrityModel:
    """§9.3: spostamento (GPS mediano dell'ultima ora), inclinazione persistente, urti."""

    def __init__(self, repo: Repository, alerts: AlertService, th: Thresholds) -> None:
        self.repo, self.alerts, self.th = repo, alerts, th
        self._last_shock: dict[uuid.UUID, float] = {}

    def evaluate(self, st: Station, now: datetime) -> list[Indicator]:
        out: list[Indicator] = []
        t1 = now.timestamp()

        gps = st.sensor_by_type("gps")
        pos = self.repo.positions.get(gps.id) if gps else None
        rows = pos.window(t1 - WINDOW_S, t1) if pos else []
        if rows:
            lat = statistics.median(r[0] for _, r in rows)
            lon = statistics.median(r[1] for _, r in rows)
            dist = haversine_m(lat, lon, st.lat, st.lon)
            out.append(Indicator(st.id, now, "spostamento_m", round(dist, 1), {"lat": lat, "lon": lon}))
            if dist > self.th.spostamento_m:
                self.alerts.raise_(st, "stazione_spostata", "critica",
                                   f"Posizione GPS mediana a {_fmt(dist, 0)} m da quella registrata", now)
            elif dist <= self.th.spostamento_m * 0.8:
                self.alerts.clear(st, "stazione_spostata", now)

        acc = st.sensor_by_type("accelerometer")
        mot = self.repo.motion.get(acc.id) if acc else None
        if mot and len(mot):
            recent = mot.window(t1 - 12 * st.cadenza_s, t1)
            run = 0
            for _, r in reversed(recent):
                if r[3] > self.th.inclinazione_deg:
                    run += 1
                else:
                    break
            last_incl = recent[-1][1][3] if recent else mot.last()[1][3]
            out.append(Indicator(st.id, now, "inclinazione", round(last_incl, 2), {"campioni_oltre_soglia": run}))
            if run > 2:
                self.alerts.raise_(st, "stazione_inclinata", "attenzione",
                                   f"Inclinazione di {_fmt(last_incl)}° per {run} intervalli", now)
            elif run == 0:
                self.alerts.clear(st, "stazione_inclinata", now)

            for ts, r in mot.window(t1 - WINDOW_S, t1):
                norm = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
                if norm > self.th.urto_g and ts > self._last_shock.get(st.id, 0):
                    self._last_shock[st.id] = ts
                    at = datetime.fromtimestamp(ts, now.tzinfo).strftime("%H:%M")
                    self.alerts.raise_(st, "urto", "attenzione", f"Picco di {_fmt(norm, 2)} g alle {at}", now)
            last = self._last_shock.get(st.id)
            if last is not None and t1 - last > self.th.urto_chiusura_s:
                self.alerts.clear(st, "urto", now)
        return out


class ChannelQualityModel:
    """§9.2 punto 5: un canale costante per oltre 6 h apre ``sensore_sospetto``."""

    def __init__(self, quality: QualityChecker, alerts: AlertService) -> None:
        self.quality, self.alerts = quality, alerts

    def evaluate(self, st: Station, now: datetime) -> list[Indicator]:
        stuck = [s for s in st.sensors if s.id in self.quality.costanti]
        if stuck:
            names = ", ".join(channel_label(s.sensor_type, s.profondita_cm) for s in stuck)
            self.alerts.raise_(st, "sensore_sospetto", "attenzione", f"Valore costante da oltre 6 h: {names}", now)
        else:
            self.alerts.clear(st, "sensore_sospetto", now)
        return [Indicator(st.id, now, "canali_sospetti", float(len(stuck)), None)]


class NrtService:
    """Esegue i modelli per le stazioni ATTIVO_COMPLETO (RF-IOT-10), con debounce per stazione."""

    def __init__(self, registry: StationRegistry, repo: Repository, quality: QualityChecker,
                 events: RingLog, th: Thresholds | None = None) -> None:
        self.registry = registry
        self.repo = repo
        self.th = th or Thresholds()
        self.alerts = AlertService(repo, events)
        self.models: list[NearRealTimeModel] = [
            WaterStressModel(registry, repo, self.alerts, self.th),
            WeatherRulesModel(repo, self.alerts, self.th),
            IntegrityModel(repo, self.alerts, self.th),
            ChannelQualityModel(quality, self.alerts),
        ]
        self._pending: set[uuid.UUID] = set()
        self._last: dict[uuid.UUID, datetime] = {}

    def notify(self, station_ids: set[uuid.UUID]) -> None:
        self._pending |= station_ids

    def run_due(self, now: datetime) -> int:
        """Equivale a ``SET NX EX cadenza_s``: al più una valutazione per intervallo."""
        done = 0
        for sid in list(self._pending):
            st = self.registry.get(sid)
            if st is None or not st.attiva:
                self._pending.discard(sid)
                continue
            last = self._last.get(sid)
            if last is not None and now - last < timedelta(seconds=st.cadenza_s):
                continue
            self._pending.discard(sid)
            self._last[sid] = now
            self.evaluate(st, now)
            done += 1
        return done

    def evaluate(self, st: Station, now: datetime) -> list[Indicator]:
        if st.stato is not StatoStazione.ATTIVO_COMPLETO:
            return []
        out: list[Indicator] = []
        for m in self.models:
            out.extend(m.evaluate(st, now))
        for ind in out:
            self.repo.add_indicator(ind)
        return out
