"""Contratto MQTT (§8.5): DTO ``TelemetryMessage`` come unione discriminata su ``type`` (Pydantic v2)."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

ScalarCode = Literal[
    "soil_moisture", "soil_temperature", "air_temperature", "air_humidity", "rain", "soil_ec",
    "solar_radiation", "wind_speed", "wind_direction", "leaf_wetness", "uv_irradiance",
    "illuminance", "barometric_pressure",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    gateway_id: str = Field(min_length=1, max_length=64)
    device_uid: str = Field(min_length=1, max_length=80)
    ts: AwareDatetime
    unit: str = Field(min_length=1, max_length=16)
    seq: int = Field(ge=0)
    battery_v: float | None = None
    sent_at: AwareDatetime | None = None


class ScalarTelemetry(_Base):
    type: ScalarCode
    value: float

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("valore non finito")
        return v


class GpsValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None
    hdop: float | None = Field(default=None, ge=0)
    sats: int | None = Field(default=None, ge=0)


class GpsTelemetry(_Base):
    type: Literal["gps"]
    value: GpsValue


class AccelValue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    ax: float
    ay: float
    az: float


class AccelTelemetry(_Base):
    type: Literal["accelerometer"]
    value: AccelValue


TelemetryMessage = Annotated[
    ScalarTelemetry | GpsTelemetry | AccelTelemetry, Field(discriminator="type")
]
telemetry_adapter: TypeAdapter[ScalarTelemetry | GpsTelemetry | AccelTelemetry] = TypeAdapter(TelemetryMessage)
