"""Flotta simulata: rileva le stazioni registrate (§13.3) e ne pubblica il traffico.

Sorgente delle stazioni e trasporto sono astratti, così lo stesso generatore serve:
- la modalità integrata (registro in-process + broker emulato), usata dalla dashboard;
- la modalità ``traffico`` (API HTTP + broker MQTT reale), cioè il simulatore del PoC.
"""

from __future__ import annotations

import heapq
import itertools
import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .broker import Credentials, EmbeddedBroker
from .faults import GlobalFaults, StationFaults
from .metrics import RingLog
from .registry import StationRegistry
from .soil import SoilHydraulics
from .station_sim import ChannelRef, Outgoing, StationSim
from .weather import WeatherField


@dataclass(frozen=True, slots=True)
class StationSpec:
    id: uuid.UUID
    gateway_id: str
    nome: str
    channels: tuple[ChannelRef, ...]
    lat: float
    lon: float
    cadenza_s: int
    hydraulics: SoilHydraulics
    ec_base: float = 1.0


class Publisher(Protocol):
    def start(self, spec: StationSpec) -> None: ...

    def stop(self, station_id: uuid.UUID) -> None: ...

    def publish(self, item: Outgoing, payload: bytes, sent_at_real: float) -> str | None:
        """Ritorna None se accettato, altrimenti la causa del rifiuto."""


class EmbeddedPublisher:
    """Credenziali per gateway lette dal registro alla scoperta e poi tenute in cache:
    dopo una dismissione il broker le rifiuta finché il rilevamento non ferma la stazione (AC9)."""

    def __init__(self, broker: EmbeddedBroker, registry: StationRegistry) -> None:
        self.broker = broker
        self.registry = registry
        self._creds: dict[uuid.UUID, Credentials] = {}

    def start(self, spec: StationSpec) -> None:
        self._creds[spec.id] = self.registry.credentials[spec.gateway_id]

    def stop(self, station_id: uuid.UUID) -> None:
        self._creds.pop(station_id, None)

    def publish(self, item: Outgoing, payload: bytes, sent_at_real: float) -> str | None:
        cred = self._creds.get(item.station_id)
        if cred is None:
            return "senza_credenziali"
        r = self.broker.publish(cred, item.topic, payload, sent_at_real)
        return r.value if r else None


def registry_source(registry: StationRegistry) -> Callable[[], list[StationSpec]]:
    def source() -> list[StationSpec]:
        out = []
        for st in registry.list():
            land = registry.lands[st.land_id]
            out.append(StationSpec(
                id=st.id, gateway_id=st.gateway_id, nome=st.nome,
                channels=tuple(ChannelRef(s.device_uid, s.sensor_type, s.profondita_cm) for s in st.sensors),
                lat=st.lat, lon=st.lon, cadenza_s=st.cadenza_s, hydraulics=land.hydraulics, ec_base=land.ec_base))
        return out
    return source


def _iso_real(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Fleet:
    def __init__(self, source: Callable[[], list[StationSpec]], publisher: Publisher, weather: WeatherField,
                 rng: random.Random, msglog: RingLog | None = None) -> None:
        self.source = source
        self.publisher = publisher
        self.weather = weather
        self.rng = rng
        self.msglog = msglog
        self.sims: dict[uuid.UUID, StationSim] = {}
        self.names: dict[uuid.UUID, str] = {}
        self.faults: dict[uuid.UUID, StationFaults] = {}
        self.global_faults = GlobalFaults()
        self.initial_raw: dict[uuid.UUID, float] = {}  # umidità iniziale imposta (seed della demo)
        self._delayed: list[tuple[float, int, Outgoing]] = []
        self._tie = itertools.count()
        self.pubblicati = 0
        self.rifiutati = 0

    # --- rilevamento (equivale a GET /iot/stations ogni 60 s) ------------------------------------
    def discover(self, now: float) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        active = {spec.id: spec for spec in self.source()}
        started, stopped = [], []
        for sid, spec in active.items():
            sim = self.sims.get(sid)
            if sim is None:
                # la prima misura parte subito: dato entro un ciclo di rilevamento (AC7)
                self.sims[sid] = StationSim(
                    station_id=sid, gateway_id=spec.gateway_id, channels=list(spec.channels), lat=spec.lat,
                    lon=spec.lon, cadenza_s=spec.cadenza_s, hydraulics=spec.hydraulics, ec_base=spec.ec_base,
                    rng=random.Random(self.rng.getrandbits(64)), next_due=float(int(now)),
                    initial_raw=self.initial_raw.get(sid))
                self.names[sid] = spec.nome
                self.faults.setdefault(sid, StationFaults())
                self.publisher.start(spec)
                started.append(sid)
            else:
                sim.cadenza_s = spec.cadenza_s
        for sid in list(self.sims):
            if sid not in active:
                del self.sims[sid]
                self.publisher.stop(sid)
                stopped.append(sid)
        return started, stopped

    # --- pubblicazione ----------------------------------------------------------------------------
    def _publish(self, item: Outgoing) -> None:
        now_real = time.time()
        item.payload["sent_at"] = _iso_real(now_real)
        payload = json.dumps(item.payload, separators=(",", ":"), ensure_ascii=False).encode()
        rifiuto = self.publisher.publish(item, payload, now_real)
        self.pubblicati += 1
        if rifiuto is not None:
            self.rifiutati += 1
            if self.msglog is not None:
                self.msglog.add({"esito": "rifiutato", "causa": rifiuto, "topic": item.topic, "tipo": None,
                                 "stazione": self.names.get(item.station_id), "valore": "PUBLISH rifiutata dal broker",
                                 "ts": None, "latenza_ms": None, "dup": False})

    def publish_due(self, now: float, max_catchup: int = 480) -> int:
        n = 0
        for sid, sim in list(self.sims.items()):
            faults = self.faults.setdefault(sid, StationFaults())
            steps = 0
            while sim.next_due <= now and steps < max_catchup:
                w = self.weather.at(sim.next_due)
                for item in sim.generate(sim.next_due, w, faults, self.global_faults):
                    if item.release_at > now:
                        heapq.heappush(self._delayed, (item.release_at, next(self._tie), item))
                    else:
                        self._publish(item)
                        n += 1
                if not sim.booted:
                    # dopo la prima misura (immediata, AC7) ogni stazione prende una fase propria:
                    # stazioni registrate insieme non trasmettono tutte nello stesso istante
                    sim.booted = True
                    sim.next_due += self.rng.randint(max(1, sim.cadenza_s // 2), sim.cadenza_s)
                else:
                    sim.next_due += sim.cadenza_s
                steps += 1
            if sim.next_due <= now:  # arretrato eccessivo (pausa lunga): si riallinea
                sim.next_due = float(int(now))
        while self._delayed and self._delayed[0][0] <= now:
            _, _, item = heapq.heappop(self._delayed)
            if item.station_id in self.sims:
                self._publish(item)
                n += 1
        return n

    @property
    def delayed(self) -> int:
        return len(self._delayed)
