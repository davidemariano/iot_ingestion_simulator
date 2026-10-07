"""Catalogo dei tipi di canale (seed di iot_sensor_types, §7.2–7.3, decisione D1).

Oltre alle colonne del DDL, ogni tipo porta due flag usati dal controllo di qualità
(§8.5, RF-IOT-16) che nel DDL non esistono:
- ``controllo_picchi``: il test |z| > 4 ha senso solo per grandezze "lisce";
  radiazione, pioggia, vento e bagnatura cambiano a gradino in modo fisiologico.
- ``controllo_costante``: un valore costante per 6 h è anomalo solo dove il sensore
  ha rumore intrinseco; pioggia e radiazione notturna restano a 0 per ore.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TipoValore(StrEnum):
    SCALARE = "scalare"
    POSIZIONE = "posizione"
    MOVIMENTO = "movimento"


class Aggregazione(StrEnum):
    MEDIA = "media"
    SOMMA = "somma"
    CIRCOLARE = "circolare"
    NESSUNA = "nessuna"


@dataclass(frozen=True, slots=True)
class SensorType:
    code: str
    descrizione: str
    unit: str
    min_valid: float | None
    max_valid: float | None
    tipo_valore: TipoValore
    aggregazione: Aggregazione
    profondita_cm: tuple[int, ...] | None
    obbligatorio: bool = True
    decimali: int = 1
    controllo_picchi: bool = False
    soglia_picco: float = 0.0  # scostamento assoluto minimo perché un |z| > 4 sia un picco
    controllo_costante: bool = False


SENSOR_TYPES: dict[str, SensorType] = {
    t.code: t
    for t in (
        SensorType("soil_moisture", "Umidità volumetrica del suolo", "%vol", 0, 100,
                   TipoValore.SCALARE, Aggregazione.MEDIA, (15, 35), decimali=1,
                   controllo_picchi=True, soglia_picco=8, controllo_costante=True),
        SensorType("soil_temperature", "Temperatura del suolo", "°C", -30, 70,
                   TipoValore.SCALARE, Aggregazione.MEDIA, (15,), decimali=1,
                   controllo_picchi=True, soglia_picco=6, controllo_costante=True),
        SensorType("air_temperature", "Temperatura dell'aria", "°C", -40, 60,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=1,
                   controllo_picchi=True, soglia_picco=8, controllo_costante=True),
        SensorType("air_humidity", "Umidità relativa dell'aria", "%", 0, 100,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=0,
                   controllo_costante=True),
        SensorType("rain", "Precipitazione nell'intervallo", "mm", 0, 50,
                   TipoValore.SCALARE, Aggregazione.SOMMA, None, decimali=1),
        SensorType("soil_ec", "Conducibilità elettrica del suolo", "dS/m", 0, 20,
                   TipoValore.SCALARE, Aggregazione.MEDIA, (15,), decimali=2,
                   controllo_picchi=True, soglia_picco=1.5, controllo_costante=True),
        SensorType("solar_radiation", "Radiazione solare globale", "W/m2", 0, 1500,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=0),
        SensorType("wind_speed", "Velocità del vento", "m/s", 0, 75,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=1),
        SensorType("wind_direction", "Direzione di provenienza del vento", "deg", 0, 360,
                   TipoValore.SCALARE, Aggregazione.CIRCOLARE, None, decimali=0,
                   controllo_costante=True),
        SensorType("leaf_wetness", "Bagnatura fogliare", "%", 0, 100,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=0),
        SensorType("uv_irradiance", "Irraggiamento ultravioletto", "W/m2", 0, 100,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=1),
        SensorType("illuminance", "Luminosità", "lx", 0, 200_000,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=0),
        SensorType("barometric_pressure", "Pressione atmosferica", "hPa", 300, 1100,
                   TipoValore.SCALARE, Aggregazione.MEDIA, None, decimali=1,
                   controllo_picchi=True, soglia_picco=6, controllo_costante=True),
        SensorType("gps", "Posizione della stazione", "deg", None, None,
                   TipoValore.POSIZIONE, Aggregazione.NESSUNA, None),
        SensorType("accelerometer", "Accelerazione su tre assi", "g", -16, 16,
                   TipoValore.MOVIMENTO, Aggregazione.NESSUNA, None, decimali=3),
    )
}

@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """Un canale della stazione: tipo + eventuale profondità."""

    sensor_type: str
    profondita_cm: int | None

    @property
    def tipo(self) -> SensorType:
        return SENSOR_TYPES[self.sensor_type]

    def device_uid(self, gateway_id: str) -> str:
        # Formato del §7.3: 'GW-3F9A1C-SOIL_MOISTURE-15'
        base = f"{gateway_id}-{self.sensor_type.upper()}"
        return f"{base}-{self.profondita_cm}" if self.profondita_cm is not None else base


def station_channels() -> list[ChannelSpec]:
    """I 16 canali di una stazione completa, nell'ordine della tabella §7.2."""
    out: list[ChannelSpec] = []
    for code, t in SENSOR_TYPES.items():
        if t.profondita_cm:
            out.extend(ChannelSpec(code, d) for d in t.profondita_cm)
        else:
            out.append(ChannelSpec(code, None))
    return out
