"""Meteo regionale condiviso dalle stazioni (§13.3 "segnali realistici").

Processi stocastici a passo fisso (60 s simulati), riproducibili con il seme:
- nuvolosità: Ornstein–Uhlenbeck in [0, 1];
- pioggia: eventi markoviani più probabili con cielo coperto;
- pressione: deriva lenta tra 1.000 e 1.025 hPa, cala durante la pioggia;
- vento: velocità media OU con ciclo diurno, direzione persistente che ruota lentamente.
Le grandezze osservabili (temperatura, umidità, radiazione…) si ricavano dallo stato
e dall'ora solare. Ogni stazione aggiunge poi rumore e stato locale (suolo, foglia).
"""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime

STEP_S = 60.0


class Scenario:
    NOMINALE = "nominale"
    SICCITA = "siccita"
    PIOGGIA = "pioggia"
    CALDO = "caldo"
    GELATA = "gelata"

    ALL = (NOMINALE, SICCITA, PIOGGIA, CALDO, GELATA)


@dataclass(slots=True)
class WeatherState:
    t: float
    cloud: float
    rain_rate: float  # mm/h, 0 se non piove
    rain_left_h: float
    pressure: float
    wind_mean: float
    wind_dir: float
    prevailing: float
    t_noise: float
    dd_noise: float


@dataclass(frozen=True, slots=True)
class Weather:
    """Osservabili al tempo t, prima del rumore della singola stazione."""

    t: float
    air_temp: float
    dewpoint: float
    ghi: float  # radiazione globale, W/m²
    sin_elev: float
    cloud: float
    rain_rate: float
    pressure: float
    wind_mean: float
    wind_dir: float
    t_mean: float
    amplitude: float
    et_factor: float  # moltiplicatore dell'evapotraspirazione (scenario)


def solar_sin_elevation(t: float, lat: float, lon: float) -> float:
    d = datetime.fromtimestamp(t, UTC)
    doy = d.timetuple().tm_yday
    b = math.radians(360 / 365 * (doy - 81))
    decl = math.radians(23.44) * math.sin(b)
    eot_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_h = d.hour + d.minute / 60 + d.second / 3600 + lon / 15 + eot_min / 60
    h = math.radians(15 * (solar_h - 12))
    phi = math.radians(lat)
    return math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(h)


def solar_hour(t: float, lon: float) -> float:
    d = datetime.fromtimestamp(t, UTC)
    return (d.hour + d.minute / 60 + d.second / 3600 + lon / 15) % 24


def seasonal_mean_temp(t: float) -> float:
    """Media giornaliera climatologica del Salento: ~9 °C a gennaio, ~26 °C ad agosto."""
    doy = datetime.fromtimestamp(t, UTC).timetuple().tm_yday
    return 17.5 - 8.5 * math.cos(2 * math.pi * (doy - 20) / 365)


def magnus_rh(temp: float, dew: float) -> float:
    a, b = 17.625, 243.04
    return 100 * math.exp(a * dew / (b + dew)) / math.exp(a * temp / (b + temp))


class WeatherField:
    def __init__(self, rng: random.Random, start: float, lat: float = 40.145, lon: float = 18.19) -> None:
        self.rng = rng
        self.lat = lat
        self.lon = lon
        self.scenario = Scenario.NOMINALE
        prevailing = rng.choice([330.0, 150.0, 20.0])
        self._state = WeatherState(
            t=start, cloud=0.3, rain_rate=0.0, rain_left_h=0.0, pressure=1014.0, wind_mean=3.5,
            wind_dir=prevailing, prevailing=prevailing, t_noise=0.0, dd_noise=0.0,
        )
        self._hist: deque[WeatherState] = deque(maxlen=int(6 * 3600 / STEP_S))
        self._times: deque[float] = deque(maxlen=self._hist.maxlen)
        self._push()

    def _push(self) -> None:
        self._hist.append(replace(self._state))
        self._times.append(self._state.t)

    @property
    def now(self) -> float:
        return self._state.t

    def set_scenario(self, scenario: str) -> None:
        if scenario not in Scenario.ALL:
            raise ValueError(scenario)
        self.scenario = scenario
        s = self._state
        if scenario == Scenario.PIOGGIA:
            s.rain_rate = self.rng.uniform(5.0, 9.0)
            s.rain_left_h = self.rng.uniform(3.0, 5.0)
            s.cloud = max(s.cloud, 0.9)
        elif scenario == Scenario.SICCITA:
            s.rain_rate, s.rain_left_h = 0.0, 0.0

    # --- evoluzione dello stato ---------------------------------------------------------
    def _step(self) -> None:
        s, rng = self._state, self.rng
        dt_h = STEP_S / 3600
        sq = math.sqrt(dt_h)
        scen = self.scenario

        cloud_mu = {Scenario.SICCITA: 0.05, Scenario.CALDO: 0.1, Scenario.PIOGGIA: 0.9}.get(scen, 0.35)
        s.cloud += 0.15 * (cloud_mu - s.cloud) * dt_h + 0.12 * sq * rng.gauss(0, 1)
        s.cloud = min(1.0, max(0.0, s.cloud))

        if s.rain_left_h > 0:
            s.rain_left_h -= dt_h
            s.rain_rate = max(0.2, s.rain_rate * math.exp(0.3 * sq * rng.gauss(0, 1)))
            s.cloud = max(s.cloud, 0.85)
            if s.rain_left_h <= 0 or scen == Scenario.SICCITA:
                s.rain_rate, s.rain_left_h = 0.0, 0.0
                if scen == Scenario.PIOGGIA:
                    self.scenario = Scenario.NOMINALE
        elif scen not in (Scenario.SICCITA, Scenario.CALDO):
            p_start_h = 0.05 * max(0.0, s.cloud - 0.55) / 0.45
            if rng.random() < p_start_h * dt_h:
                s.rain_left_h = rng.expovariate(1 / 3.0)
                s.rain_rate = rng.lognormvariate(math.log(1.5), 0.6)

        p_mu = 1006.0 if s.rain_rate > 0 else 1015.0
        s.pressure += 0.08 * (p_mu - s.pressure) * dt_h + 0.35 * sq * rng.gauss(0, 1)
        s.pressure = min(1025.0, max(1000.0, s.pressure))

        w_mu = 3.5 + (2.5 if s.rain_rate > 0 else 0.0)
        s.wind_mean += 0.3 * (w_mu - s.wind_mean) * dt_h + 0.8 * sq * rng.gauss(0, 1)
        s.wind_mean = min(18.0, max(0.3, s.wind_mean))
        if rng.random() < dt_h / 48:  # cambio di regime ogni ~2 giorni
            s.prevailing = rng.choice([330.0, 150.0, 20.0, 240.0])
        diff = ((s.prevailing - s.wind_dir + 180) % 360) - 180
        s.wind_dir = (s.wind_dir + 0.4 * diff * dt_h + 8 * sq * rng.gauss(0, 1)) % 360

        s.t_noise += -0.5 * s.t_noise * dt_h + 0.5 * sq * rng.gauss(0, 1)
        s.dd_noise += -0.3 * s.dd_noise * dt_h + 0.8 * sq * rng.gauss(0, 1)
        s.t += STEP_S

    def advance_to(self, t: float) -> None:
        steps = 0
        while self._state.t + STEP_S <= t:
            self._step()
            self._push()
            steps += 1
            if steps > 50_000:  # salto di oltre un mese: si riallinea senza simulare
                self._state.t = t
                self._push()
                break

    # --- osservabili ----------------------------------------------------------------------------
    def at(self, t: float) -> Weather:
        if t >= self._state.t + STEP_S:
            self.advance_to(t)
        i = bisect_right(self._times, t) - 1
        s = self._hist[max(0, i)]
        scen = self.scenario

        t_mean = seasonal_mean_temp(t) + {Scenario.CALDO: 13.0, Scenario.SICCITA: 3.0,
                                          Scenario.GELATA: -17.0}.get(scen, 0.0)
        amplitude = 5.5 * (1 - 0.6 * s.cloud)
        h = solar_hour(t, self.lon)
        temp = t_mean + amplitude * math.cos(2 * math.pi * (h - 15) / 24) + s.t_noise
        if s.rain_rate > 0:
            temp -= 2.0

        if s.rain_rate > 0:
            dew = temp - 0.5 - 0.2 * abs(s.dd_noise)
        else:
            dd = 6.5 + (5.0 if scen in (Scenario.SICCITA, Scenario.CALDO) else 0.0) + s.dd_noise
            dew = min(temp, t_mean - amplitude * 0.6 - max(0.0, dd - 4))

        sin_el = solar_sin_elevation(t, self.lat, self.lon)
        ghi = 0.0
        if sin_el > 0.01:
            ghi = 1098 * sin_el * math.exp(-0.057 / sin_el) * (1 - 0.75 * s.cloud ** 3.4)

        et_factor = {Scenario.SICCITA: 3.0, Scenario.CALDO: 2.0}.get(scen, 1.0)
        return Weather(t=t, air_temp=temp, dewpoint=dew, ghi=ghi, sin_elev=sin_el, cloud=s.cloud,
                       rain_rate=s.rain_rate, pressure=s.pressure, wind_mean=s.wind_mean, wind_dir=s.wind_dir,
                       t_mean=t_mean, amplitude=amplitude, et_factor=et_factor)
