"""Entità del registro (§6, §7.3): stazioni, canali, stati, allerte, indicatori."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .catalog import SENSOR_TYPES, SensorType


class StatoStazione(StrEnum):
    REGISTRATA = "REGISTRATA"
    ATTIVO_COMPLETO = "ATTIVO_COMPLETO"
    DEGRADATA = "DEGRADATA"
    OFFLINE = "OFFLINE"
    DISMESSA = "DISMESSA"


class DataSource(StrEnum):
    IOT = "IOT"
    SATELLITE = "SATELLITE"


@dataclass(slots=True)
class Sensor:
    id: uuid.UUID
    station_id: uuid.UUID
    sensor_type: str
    device_uid: str
    profondita_cm: int | None
    attivo: bool = True
    ultima_misura_il: datetime | None = None

    @property
    def tipo(self) -> SensorType:
        return SENSOR_TYPES[self.sensor_type]


@dataclass(slots=True)
class Station:
    id: uuid.UUID
    land_id: uuid.UUID
    registrata_da: uuid.UUID
    nome: str
    lat: float
    lon: float
    gateway_id: str
    registrata_il: datetime
    stato: StatoStazione = StatoStazione.REGISTRATA
    cadenza_s: int = 180
    ultimo_contatto_il: datetime | None = None
    dismessa_il: datetime | None = None
    sensors: list[Sensor] = field(default_factory=list)

    @property
    def attiva(self) -> bool:
        return self.stato is not StatoStazione.DISMESSA

    def sensor_by_type(self, code: str, depth: int | None = None) -> Sensor | None:
        for s in self.sensors:
            if s.sensor_type == code and (depth is None or s.profondita_cm == depth):
                return s
        return None


@dataclass(slots=True)
class StatusTransition:
    id: int
    station_id: uuid.UUID
    da_stato: StatoStazione | None
    a_stato: StatoStazione
    motivo: str
    avvenuto_il: datetime


@dataclass(slots=True)
class Indicator:
    station_id: uuid.UUID
    calcolato_il: datetime
    indicatore: str
    valore: float
    dettagli: dict[str, Any] | None = None


@dataclass(slots=True)
class Alert:
    id: int
    station_id: uuid.UUID
    tipo: str
    severita: str  # 'attenzione' | 'critica'
    messaggio: str
    aperta_il: datetime
    chiusa_il: datetime | None = None

    @property
    def aperta(self) -> bool:
        return self.chiusa_il is None
