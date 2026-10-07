"""Motore della simulazione: compone broker, flotta, ingestion e task periodici (Celery beat nel PoC).

Il nucleo è sincrono e guidato dal tempo (``tick`` e ``ingestion.flush``), così i test
possono avanzare l'orologio a mano; ``run`` aggiunge i due loop asyncio del servizio.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .broker import EmbeddedBroker
from .clock import Clock, SimClock, utcnow
from .completeness import CompletenessChecker
from .domain import Station, StatoStazione
from .fleet import EmbeddedPublisher, Fleet, registry_source
from .ingestion import IngestionService
from .lands import DEMO_USER_ID, Land, make_test_lands, seed_lands
from .metrics import Metrics, RingLog
from .nrt import NrtService
from .registry import StationRegistry
from .storage import SAMPLE_TABLES, Repository, month_key
from .weather import WeatherField

log = logging.getLogger("iotsim")

DISCOVERY_S = 60
COMPLETENESS_S = 180
RAW_RETENTION_DAYS = 8  # nel PoC reale 12 mesi (D5); qui la memoria è il limite


def _next_month_keys(ts: float, n: int) -> list[str]:
    d = datetime.fromtimestamp(ts, UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    keys = []
    for _ in range(n):
        keys.append(month_key(d.timestamp()))
        d = (d + timedelta(days=32)).replace(day=1)
    return keys


class Engine:
    def __init__(self, seed: int = 42, factor: float = 20.0, start: datetime | None = None,
                 clock: Clock | None = None, demo: bool = True, backfill_hours: int = 24) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        start = (start or (clock.now() if clock else utcnow())).replace(microsecond=0)
        self.clock: Clock = clock or SimClock(start, factor)
        self.started_real = time.time()
        self.repo = Repository()
        self.broker = EmbeddedBroker()
        self.metrics = Metrics()
        self.msglog = RingLog(800)
        self.events = RingLog(400)
        self.registry = StationRegistry(seed_lands(), self.broker, self.repo, self.clock, self.events,
                                        random.Random(self.rng.getrandbits(64)))
        t0 = start.timestamp() - (backfill_hours + 1) * 3600 if demo else start.timestamp()
        self.weather = WeatherField(random.Random(self.rng.getrandbits(64)), t0)
        self.fleet = Fleet(registry_source(self.registry), EmbeddedPublisher(self.broker, self.registry),
                           self.weather, random.Random(self.rng.getrandbits(64)), self.msglog)
        self.ingestion = IngestionService(self.broker, self.registry, self.repo, self.metrics, self.clock,
                                          self.msglog, on_new_data=self._on_new_data)
        self.completeness = CompletenessChecker(self.registry)
        self.nrt = NrtService(self.registry, self.repo, self.ingestion.quality, self.events)
        self.db_down_until = 0.0
        self._next: dict[str, float] = {}
        self._ensure_partitions(start.timestamp() - backfill_hours * 3600)
        self._ensure_partitions(start.timestamp())
        if demo:
            paused = self.clock.paused
            self.clock.set_paused(True)  # il backfill avviene "fuori dal tempo"
            self._seed_demo(start, backfill_hours)
            self.clock.set_paused(paused)
        self._init_schedule(self.clock.now().timestamp())

    # --- configurazione ----------------------------------------------------------------------------
    def _init_schedule(self, t: float) -> None:
        self._next = {
            "discovery": t,
            "completeness": t + COMPLETENESS_S,
            "hourly": (t // 3600 + 1) * 3600,
            "partitions": t + 86400,
        }

    def _grace(self) -> float:
        # 2 s reali di pipeline (flush ogni 1 s + coda) espressi in tempo simulato
        return 2.0 * self.clock.factor

    def _on_new_data(self, ids: set[uuid.UUID]) -> None:
        self.nrt.notify(ids)

    def _ensure_partitions(self, t: float) -> list[str]:
        created = []
        for key in _next_month_keys(t, 3):
            for table in SAMPLE_TABLES:
                if self.repo.ensure_partition(table, key):
                    created.append(f"{table}_{key}")
        return created

    # --- seed della demo --------------------------------------------------------------------------
    def _seed_demo(self, start: datetime, hours: int) -> None:
        lands = list(self.registry.lands.values())
        seeds = [  # (terreno, nome stazione, RAW iniziale)
            (lands[0], "Serrone Nord", 0.74),
            (lands[1], "Le Chiuse – filare 12", 0.57),
            (lands[3], "Canale dell'Asso", 0.36),
        ]
        reg_at = start - timedelta(days=21)
        t_begin = start.timestamp() - hours * 3600 + 900  # margine sulla tolleranza di 24 h
        for land, nome, raw in seeds:
            lat, lon = land.random_point(self.rng)
            st = self.registry.register(DEMO_USER_ID, nome, lat, lon, at=reg_at)
            self.fleet.initial_raw[st.id] = raw
        if hours <= 0:
            return
        self.weather.advance_to(t_begin)
        self.fleet.discover(t_begin)
        for sim in self.fleet.sims.values():
            sim.next_due = t_begin + self.rng.randrange(sim.cadenza_s)
        # misure storiche fatte passare per la stessa pipeline (validazione, qualità, dedup)
        t = t_begin
        while t < start.timestamp():
            self.fleet.publish_due(t)
            while self.ingestion.flush():
                pass
            self._run_scheduled(datetime.fromtimestamp(t, UTC), backfill=True)
            t += 60
        while self.ingestion.flush():
            pass
        # i contatori del PoC partono dal traffico live
        self.metrics = Metrics()
        self.ingestion.metrics = self.metrics
        self.msglog = RingLog(800)
        self.ingestion.msglog = self.msglog
        self.fleet.msglog = self.msglog
        self.broker.pubblicati = 0
        self.fleet.pubblicati = 0

    # --- task periodici ------------------------------------------------------------------------------
    def _run_scheduled(self, now: datetime, backfill: bool = False) -> None:
        t = now.timestamp()
        if not self._next:
            self._init_schedule(t)
        nx = self._next
        self.completeness.pipeline_grace_s = 0.0 if backfill else self._grace()
        if t >= nx["completeness"]:
            self.completeness.run(now)  # verifica_completezza_iot_task
            nx["completeness"] = t + COMPLETENESS_S
        if t >= nx["hourly"]:  # aggrega_misure_orarie_task: l'ora chiusa e la precedente (dati tardivi)
            hour = nx["hourly"]
            for st in self.registry.list(include_dismissed=False):
                for s in st.sensors:
                    if s.tipo.tipo_valore == "scalare":
                        for h0 in (hour - 7200, hour - 3600):
                            self.repo.aggregate_hour(s.id, h0, s.tipo.aggregazione)
            nx["hourly"] = hour + 3600
        if t >= nx["partitions"]:  # gestisci_partizioni_iot_task
            for name in self._ensure_partitions(t):
                self.events.add({"tipo": "partizione", "messaggio": f"Creata la partizione {name}",
                                 "sim_ts": now.isoformat()})
            self.repo.apply_retention(t - RAW_RETENTION_DAYS * 86400)
            if self.repo.default_rows():
                self.events.add({"tipo": "partizione", "messaggio": "Righe nella partizione default",
                                 "sim_ts": now.isoformat()})
            nx["partitions"] = t + 86400
        self.nrt.run_due(now)  # calcola_indicatori_nrt_task con debounce

    def tick(self) -> int:
        now = self.clock.now()
        t = now.timestamp()
        self.repo.available = time.monotonic() >= self.db_down_until
        self.weather.advance_to(t)
        if t >= self._next["discovery"]:
            started, stopped = self.fleet.discover(t)
            for sid in started:
                st = self.registry.get(sid)
                if st:
                    self.events.add({"tipo": "simulatore", "station_id": str(sid), "stazione": st.nome,
                                     "messaggio": "Rilevata dal simulatore: inizia a trasmettere",
                                     "sim_ts": now.isoformat()})
            for sid in stopped:
                st = self.registry.get(sid)
                self.events.add({"tipo": "simulatore", "station_id": str(sid),
                                 "stazione": st.nome if st else str(sid),
                                 "messaggio": "Non più registrata: traffico interrotto", "sim_ts": now.isoformat()})
            self._next["discovery"] = t + DISCOVERY_S
        n = self.fleet.publish_due(t)
        self._run_scheduled(now)
        return n

    def drain(self) -> int:
        n = 0
        while (k := self.ingestion.flush()):
            n += k
        return n

    # --- comandi del simulatore --------------------------------------------------------------------
    def set_scenario(self, scenario: str) -> None:
        self.weather.set_scenario(scenario)
        self.events.add({"tipo": "simulatore", "messaggio": f"Scenario meteo: {scenario}",
                         "sim_ts": self.clock.now().isoformat()})

    def make_db_unavailable(self, seconds: float) -> None:
        self.db_down_until = time.monotonic() + seconds
        self.repo.available = False
        self.events.add({"tipo": "simulatore", "messaggio": f"Database non disponibile per {seconds:g} s",
                         "sim_ts": self.clock.now().isoformat()})

    def restart_ingestion(self, downtime_s: float = 5.0) -> int:
        n = self.ingestion.restart(downtime_s)
        self.events.add({"tipo": "simulatore",
                         "messaggio": f"Ingestion riavviata: {n} messaggi senza PUBACK riconsegnati",
                         "sim_ts": self.clock.now().isoformat()})
        return n

    def create_test_stations(self, n: int) -> list[Station]:
        """Equivalente di ``--crea-stazioni N``: lotti di prova + registrazione via registro."""
        existing = sum(1 for land in self.registry.lands.values() if land.di_prova)
        lands: list[Land] = make_test_lands(n, existing, self.rng)
        self.registry.add_lands(lands)
        out = []
        for land in lands:
            lat, lon = land.random_point(self.rng)
            out.append(self.registry.register(DEMO_USER_ID, f"Prova {land.nome[-3:]}", lat, lon))
        return out

    # --- stato per la console ----------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        stations = self.registry.list()
        by_state = {s.value: 0 for s in StatoStazione if s is not StatoStazione.DISMESSA}
        for st in stations:
            by_state[st.stato.value] += 1
        lat = self.metrics.latency()
        canali = 16 * len(stations)
        nominal = sum(16 / st.cadenza_s for st in stations) * self.clock.factor
        return {
            "sim_now": self.clock.now().isoformat(),
            "real_now": utcnow().isoformat(),
            "fattore": self.clock.factor,
            "in_pausa": self.clock.paused,
            "scenario_meteo": self.weather.scenario,
            "seme": self.seed,
            "uptime_s": round(time.time() - self.started_real, 1),
            "stazioni": {"totale": len(stations), "per_stato": by_state, "canali": canali},
            "throughput": {"msg_s": round(self.metrics.throughput(), 2), "nominale_msg_s": round(nominal, 2)},
            "latenza_s": lat,
            "contatori": {
                "pubblicati": self.fleet.pubblicati,
                "ricevuti": self.metrics.ricevuti,
                "salvati": dict(self.metrics.salvati),
                "scartati": dict(self.metrics.scartati),
                "rifiutati_broker": {k: v for k, v in self.broker.rifiutati.items() if v},
                "normalizzati": self.metrics.normalizzati,
                "sospetti": self.metrics.sospetti,
                "perdite": self.metrics.perdite(),
                "riconsegnati": self.broker.riconsegnati,
                "batch": self.metrics.batch,
                "ultimo_batch": self.metrics.ultimo_batch,
                "errori_db": self.metrics.errori_db,
            },
            "broker": {"in_coda": self.broker.queued, "in_volo": self.broker.inflight,
                       "ritardati": self.fleet.delayed},
            "servizi": {"database": self.repo.available, "ingestion": not self.ingestion.is_down},
            "partizioni": dict(sorted(self.repo.partitions.items())),
            "istogramma_latenza": self.metrics.latency_histogram(),
        }

    # --- loop asyncio --------------------------------------------------------------------------------
    async def run(self, tick_s: float = 0.25) -> None:
        await asyncio.gather(self._tick_loop(tick_s), self._ingestion_loop())

    async def _tick_loop(self, tick_s: float) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # il simulatore non deve fermarsi per un errore in un tick
                log.exception("errore nel tick")
            await asyncio.sleep(tick_s)

    async def _ingestion_loop(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            # flush ogni 1 s o al raggiungimento di 500 messaggi (§8.3)
            if self.broker.queued >= self.ingestion.batch_max or now - last >= self.ingestion.flush_interval_s:
                try:
                    self.ingestion.flush()
                except Exception:
                    log.exception("errore nel flush")
                last = now
