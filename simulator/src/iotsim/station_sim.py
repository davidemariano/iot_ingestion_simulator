"""Generatore dei 16 canali di una stazione (§13.3) con stato fisico locale e guasti.

Il mondo fisico (suolo, foglia) evolve a ogni intervallo anche quando la stazione
è offline o un canale è silenziato: i guasti filtrano solo ciò che viene trasmesso.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import geo
from .broker import telemetry_topic
from .catalog import SENSOR_TYPES, TipoValore
from .faults import GlobalFaults, StationFaults
from .soil import SoilHydraulics
from .units import ALTERNATIVE
from .weather import Weather, magnus_rh, solar_hour

RAIN_BUCKET_MM = 0.2  # risoluzione del pluviometro a bascula
LAYER_MM = 250.0  # spessore rappresentato da ciascun sensore di umidità
PCT_PER_MM = 100 / LAYER_MM

# entità dei picchi iniettati, per tipo
_SPIKE = {"soil_moisture": 25.0, "soil_temperature": 15.0, "air_temperature": 18.0,
          "soil_ec": 4.0, "barometric_pressure": 25.0}


@dataclass(frozen=True, slots=True)
class ChannelRef:
    device_uid: str
    sensor_type: str
    profondita_cm: int | None


@dataclass(slots=True)
class Outgoing:
    topic: str
    payload: dict[str, Any]
    release_at: float  # tempo simulato di invio
    station_id: uuid.UUID
    device_uid: str


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class StationSim:
    station_id: uuid.UUID
    gateway_id: str
    channels: list[ChannelRef]
    lat: float
    lon: float
    cadenza_s: int
    hydraulics: SoilHydraulics
    ec_base: float
    rng: random.Random
    next_due: float
    initial_raw: float | None = None

    theta1: float = field(init=False)
    theta2: float = field(init=False)
    wet: float = field(init=False, default=0.0)
    rain_acc: float = field(init=False, default=0.0)
    alt: float = field(init=False)
    gps_bias: tuple[float, float] = field(init=False)
    temp_bias: float = field(init=False)
    wind_k: float = field(init=False)
    rain_k: float = field(init=False)
    battery: float = field(init=False)
    seq: dict[str, int] = field(init=False)
    stuck: dict[str, Any] = field(init=False, default_factory=dict)
    held: dict[str, Outgoing] = field(init=False, default_factory=dict)
    booted: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        r = self.rng
        h = self.hydraulics
        raw0 = self.initial_raw if self.initial_raw is not None else r.uniform(0.62, 0.85)
        self.theta1 = h.theta_wp + raw0 * h.available
        self.theta2 = h.theta_wp + min(1.0, raw0 + 0.08) * h.available
        self.alt = r.uniform(60, 95)
        self.gps_bias = (r.gauss(0, 1.5), r.gauss(0, 1.5))
        self.temp_bias = r.gauss(0, 0.35)
        self.wind_k = r.uniform(0.85, 1.15)
        self.rain_k = r.uniform(0.85, 1.15)
        self.battery = r.uniform(3.55, 3.75)
        self.seq = {c.device_uid: 0 for c in self.channels}

    # --- fisica locale ------------------------------------------------------------------------
    def _physics(self, w: Weather, dt_s: float) -> float:
        dt_h = dt_s / 3600
        h = self.hydraulics
        fc, wp = h.theta_fc, h.theta_wp

        self.rain_acc += w.rain_rate * dt_h * self.rain_k
        tips = math.floor(self.rain_acc / RAIN_BUCKET_MM + 1e-9)
        rain_mm = tips * RAIN_BUCKET_MM
        self.rain_acc -= rain_mm

        root = 0.6 * self.theta1 + 0.4 * self.theta2
        ks = max(0.0, min(1.0, (root - wp) / (0.5 * h.available)))  # FAO-56, p = 0,5
        et = 0.00055 * w.ghi * max(0.2, (w.air_temp + 5) / 25) * dt_h * w.et_factor * ks

        self.theta1 += rain_mm * PCT_PER_MM - 0.7 * et * PCT_PER_MM
        self.theta2 -= 0.3 * et * PCT_PER_MM
        if self.theta1 > fc:
            d = (self.theta1 - fc) * min(1.0, 0.6 * dt_h)
            self.theta1 -= d
            self.theta2 += 0.8 * d
        if self.theta2 > fc:
            self.theta2 -= (self.theta2 - fc) * min(1.0, 0.25 * dt_h)
        flux = 0.03 * (self.theta1 - self.theta2) * dt_h
        self.theta1 -= flux
        self.theta2 += flux
        sat = fc + 12
        self.theta1 = min(sat, max(0.6 * wp, self.theta1))
        self.theta2 = min(sat, max(0.6 * wp, self.theta2))

        rh = magnus_rh(w.air_temp, w.dewpoint)
        if rain_mm > 0:
            self.wet = 100.0
        elif rh >= 93 and w.ghi < 30:
            self.wet = min(85.0, self.wet + 30 * dt_h)
        else:
            self.wet = max(0.0, self.wet - (8 + w.ghi / 12 + w.wind_mean) * dt_h)

        # pannello solare di giorno, consumo costante
        self.battery = min(4.15, max(3.4, self.battery + (0.004 * w.ghi / 700 - 0.0012) * dt_h))
        return rain_mm

    def _measure(self, ts: float, w: Weather, rain_mm: float, faults: StationFaults) -> dict[ChannelRef, Any]:
        r = self.rng
        h = solar_hour(ts, self.lon)
        air_t = w.air_temp + self.temp_bias + r.gauss(0, 0.15)
        rh = min(100.0, max(3.0, magnus_rh(air_t, w.dewpoint + self.temp_bias) + r.gauss(0, 0.8)))
        flicker = r.uniform(0.45, 1.0) if 0.2 < w.cloud < 0.85 and r.random() < 0.2 else 1.0
        ghi = max(0.0, w.ghi * flicker * (1 + r.gauss(0, 0.02)))
        speed = w.wind_mean * self.wind_k * (1 + 0.15 * math.cos(2 * math.pi * (h - 15) / 24))
        speed *= r.lognormvariate(0, 0.2) * (1.6 if r.random() < 0.05 else 1.0)

        lat, lon = geo.offset(self.lat, self.lon, self.gps_bias[0] + r.gauss(0, 2.5),
                              self.gps_bias[1] + r.gauss(0, 2.5))
        if faults.spostata_m > 0:
            d = faults.spostata_m / math.sqrt(2)
            lat, lon = geo.offset(lat, lon, d, d)

        tilt = math.radians(faults.inclinata_deg)
        acc = [r.gauss(0, 0.006), math.sin(tilt) + r.gauss(0, 0.006), math.cos(tilt) + r.gauss(0, 0.006)]
        if faults.urto:
            k = r.uniform(2.6, 3.6)
            acc = [k * 0.35 * r.choice((-1, 1)), k * 0.4 * r.choice((-1, 1)), k * 0.85]
            faults.urto = False

        values: dict[str, Any] = {
            "soil_moisture@15": self.theta1 + r.gauss(0, 0.05),
            "soil_moisture@35": self.theta2 + r.gauss(0, 0.04),
            "soil_temperature@15": w.t_mean + 1.0 + 0.35 * w.amplitude * math.cos(2 * math.pi * (h - 18) / 24)
            + self.temp_bias + r.gauss(0, 0.06),
            "air_temperature": air_t,
            "air_humidity": rh,
            "rain": rain_mm,
            "soil_ec@15": max(0.0, self.ec_base * (max(self.theta1, 0.1) / self.hydraulics.theta_fc) ** 1.5
                              + r.gauss(0, 0.015)),
            "solar_radiation": ghi,
            "wind_speed": max(0.0, speed),
            "wind_direction": (w.wind_dir + r.gauss(0, 10)) % 360,
            "leaf_wetness": min(100.0, max(0.0, self.wet + r.gauss(0, 1.5))) if self.wet > 0 else 0.0,
            "uv_irradiance": max(0.0, ghi * 0.042 * (1 + r.gauss(0, 0.03))),
            "illuminance": min(200_000.0, max(0.0, ghi * 118 * (1 + r.gauss(0, 0.02)))),
            "barometric_pressure": w.pressure + r.gauss(0, 0.12),
            "gps": {"lat": round(lat, 6), "lon": round(lon, 6), "alt_m": round(self.alt + r.gauss(0, 1.2), 1),
                    "hdop": round(r.uniform(0.7, 1.4), 1), "sats": r.randint(8, 13)},
            "accelerometer": {"ax": round(acc[0], 3), "ay": round(acc[1], 3), "az": round(acc[2], 3)},
        }
        out: dict[ChannelRef, Any] = {}
        for c in self.channels:
            key = f"{c.sensor_type}@{c.profondita_cm}" if c.profondita_cm is not None else c.sensor_type
            out[c] = values[key]
        return out

    # --- generazione dei messaggi ------------------------------------------------------------
    def generate(self, ts: float, w: Weather, faults: StationFaults, g: GlobalFaults) -> list[Outgoing]:
        rain_mm = self._physics(w, self.cadenza_s)
        measured = self._measure(ts, w, rain_mm, faults)
        r = self.rng
        out: list[Outgoing] = []
        for ch, value in measured.items():
            uid = ch.device_uid
            held = self.held.pop(uid, None)
            if faults.offline or uid in faults.canali_silenziati:
                self.stuck.pop(uid, None)
                if held:
                    out.append(held)
                continue
            t = SENSOR_TYPES[ch.sensor_type]
            unit = t.unit
            if t.tipo_valore is TipoValore.SCALARE:
                value = round(value, t.decimali)
            if uid in faults.canali_bloccati:
                value = self.stuck.setdefault(uid, value)
            else:
                self.stuck.pop(uid, None)

            if t.tipo_valore is TipoValore.SCALARE:
                if t.controllo_picchi and faults.picchi_pct and r.random() * 100 < faults.picchi_pct:
                    spike = _SPIKE[ch.sensor_type]
                    value = round(value + spike if value + spike <= t.max_valid else value - spike, t.decimali)
                if g.fuori_intervallo_pct and r.random() * 100 < g.fuori_intervallo_pct:
                    value = round(t.max_valid + (t.max_valid - t.min_valid) * 0.1 + 1, t.decimali)
                elif g.unita_alternative_pct and unit in ALTERNATIVE and r.random() * 100 < g.unita_alternative_pct:
                    unit, conv = ALTERNATIVE[unit]
                    value = round(conv(value), 4)
            elif ch.sensor_type == "accelerometer" and g.fuori_intervallo_pct \
                    and r.random() * 100 < g.fuori_intervallo_pct:
                value = {**value, "az": 19.6}

            self.seq[uid] += 1
            payload: dict[str, Any] = {
                "gateway_id": self.gateway_id, "device_uid": uid, "type": ch.sensor_type,
                "ts": _iso(ts), "value": value, "unit": unit, "seq": self.seq[uid],
                "battery_v": round(self.battery, 2),
            }
            if g.malformati_pct and r.random() * 100 < g.malformati_pct:
                payload["ts"] = "ieri sera"
            item = Outgoing(telemetry_topic(self.gateway_id, uid), payload, ts, self.station_id, uid)
            if g.ritardo_pct and r.random() * 100 < g.ritardo_pct:
                item.release_at = ts + g.ritardo_s
            if held is None and g.fuori_ordine_pct and r.random() * 100 < g.fuori_ordine_pct:
                self.held[uid] = item  # arriverà dopo il messaggio dell'intervallo successivo
                continue
            out.append(item)
            if held:
                out.append(held)
            if g.duplicati_pct and r.random() * 100 < g.duplicati_pct:
                out.append(Outgoing(item.topic, dict(payload), item.release_at, self.station_id, uid))
        return out
