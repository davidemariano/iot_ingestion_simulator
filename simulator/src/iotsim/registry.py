"""Registro delle stazioni (StationRegistryService + BrokerCredentialService, §6 e §8.2)."""

from __future__ import annotations

import random
import uuid
from datetime import datetime

from .broker import Credentials, EmbeddedBroker
from .catalog import station_channels
from .clock import Clock
from .domain import DataSource, Sensor, Station, StatoStazione
from .lands import Land
from .metrics import RingLog
from .storage import Repository


class RegistrationError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class StationRegistry:
    def __init__(self, lands: list[Land], broker: EmbeddedBroker, repo: Repository, clock: Clock,
                 events: RingLog, rng: random.Random) -> None:
        self.lands: dict[uuid.UUID, Land] = {land.id: land for land in lands}
        self.broker = broker
        self.repo = repo
        self.clock = clock
        self.events = events
        self.rng = rng
        self.stations: dict[uuid.UUID, Station] = {}
        self.credentials: dict[str, Credentials] = {}
        # cache device_uid -> (stazione, canale) usata dall'arricchimento dell'ingestion
        self._by_device: dict[str, tuple[Station, Sensor]] = {}

    # --- terreni ------------------------------------------------------------------
    def add_lands(self, lands: list[Land]) -> None:
        for land in lands:
            self.lands[land.id] = land

    def land_for_point(self, user_id: uuid.UUID, lat: float, lon: float) -> Land | None:
        """ST_Contains tra i terreni dell'utente; se si sovrappongono, il più piccolo (S4)."""
        hits = [land for land in self.lands.values() if land.owner_id == user_id and land.contains(lat, lon)]
        return min(hits, key=lambda land: land.area_m2) if hits else None

    def active_station_for_land(self, land_id: uuid.UUID) -> Station | None:
        for s in self.stations.values():
            if s.land_id == land_id and s.attiva:
                return s
        return None

    # --- registrazione (RF-IOT-01..03) -----------------------------------------------
    def _new_gateway_id(self) -> str:
        while True:
            gw = f"GW-{self.rng.getrandbits(24):06X}"
            if all(s.gateway_id != gw for s in self.stations.values()):
                return gw

    def register(self, user_id: uuid.UUID, nome: str, lat: float, lon: float,
                 at: datetime | None = None, cadenza_s: int = 180) -> Station:
        nome = (nome or "").strip()
        if not 1 <= len(nome) <= 60:
            raise RegistrationError(422, "Il nome è obbligatorio (1–60 caratteri)")
        if not 30 <= cadenza_s <= 3600:
            raise RegistrationError(422, "La cadenza deve essere tra 30 e 3.600 s")
        land = self.land_for_point(user_id, lat, lon)
        if land is None:
            raise RegistrationError(422, "Il punto non ricade in nessuno dei tuoi terreni salvati")
        if self.active_station_for_land(land.id) is not None:
            raise RegistrationError(409, "Il terreno ha già una stazione attiva")

        at = at or self.clock.now()
        sid = uuid.UUID(int=self.rng.getrandbits(128), version=4)
        gw = self._new_gateway_id()
        st = Station(id=sid, land_id=land.id, registrata_da=user_id, nome=nome, lat=lat, lon=lon,
                     gateway_id=gw, registrata_il=at, cadenza_s=cadenza_s)
        for ch in station_channels():
            sensor = Sensor(id=uuid.UUID(int=self.rng.getrandbits(128), version=4), station_id=sid,
                            sensor_type=ch.sensor_type, device_uid=ch.device_uid(gw),
                            profondita_cm=ch.profondita_cm)
            st.sensors.append(sensor)
            self._by_device[sensor.device_uid] = (st, sensor)
        self.stations[sid] = st
        self.credentials[gw] = self.broker.create_credentials(gw)
        self.repo.log_transition(sid, None, StatoStazione.REGISTRATA, "registrazione dalla dashboard", at)
        self.events.add({"tipo": "registrazione", "station_id": str(sid), "stazione": nome,
                         "messaggio": f"Registrata su «{land.nome}» ({gw}, 16 canali)", "sim_ts": at.isoformat()})
        return st

    # --- consultazione -------------------------------------------------------------------
    def list(self, include_dismissed: bool = False) -> list[Station]:
        out = [s for s in self.stations.values() if include_dismissed or s.attiva]
        return sorted(out, key=lambda s: s.registrata_il)

    def get(self, station_id: uuid.UUID) -> Station | None:
        return self.stations.get(station_id)

    def resolve(self, device_uid: str) -> tuple[Station, Sensor] | None:
        return self._by_device.get(device_uid)

    def update(self, station_id: uuid.UUID, nome: str | None, cadenza_s: int | None) -> Station:
        st = self.stations.get(station_id)
        if st is None or not st.attiva:
            raise RegistrationError(404, "Stazione non trovata")
        if nome is not None:
            nome = nome.strip()
            if not 1 <= len(nome) <= 60:
                raise RegistrationError(422, "Il nome è obbligatorio (1–60 caratteri)")
            st.nome = nome
        if cadenza_s is not None:
            if not 30 <= cadenza_s <= 3600:
                raise RegistrationError(422, "La cadenza deve essere tra 30 e 3.600 s")
            st.cadenza_s = cadenza_s
        return st

    # --- dismissione logica (RF-IOT-05, S3) --------------------------------------------
    def decommission(self, ids: list[uuid.UUID], at: datetime | None = None) -> list[Station]:
        at = at or self.clock.now()
        done: list[Station] = []
        for sid in ids:
            st = self.stations.get(sid)
            if st is None or not st.attiva:
                continue
            self.set_state(st, StatoStazione.DISMESSA, "eliminazione dalla dashboard", at)
            st.dismessa_il = at
            for s in st.sensors:
                s.attivo = False
            self.broker.revoke(st.gateway_id)
            self.credentials.pop(st.gateway_id, None)
            for a in self.repo.open_alerts(sid):
                a.chiusa_il = at
            done.append(st)
        return done

    # --- stato e sorgente ------------------------------------------------------------------
    def set_state(self, st: Station, new: StatoStazione, motivo: str, at: datetime) -> bool:
        if st.stato is new:
            return False
        old = st.stato
        st.stato = new
        self.repo.log_transition(st.id, old, new, motivo, at)
        self.events.add({"tipo": "stato", "station_id": str(st.id), "stazione": st.nome,
                         "da": old.value, "a": new.value, "messaggio": motivo, "sim_ts": at.isoformat()})
        return True

    def data_source(self, land_id: uuid.UUID) -> tuple[DataSource, Station | None]:
        """Equivalente di ``v_land_data_source`` (§7.3)."""
        st = self.active_station_for_land(land_id)
        if st is not None and st.stato is StatoStazione.ATTIVO_COMPLETO:
            return DataSource.IOT, st
        return DataSource.SATELLITE, st
