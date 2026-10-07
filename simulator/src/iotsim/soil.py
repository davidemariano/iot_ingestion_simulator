"""Funzioni di pedotrasferimento di Saxton e Rawls (2006), §9.2 punto 1.

Ingressi nelle unità di SoilGrids salvate in ``soil_data``:
sabbia e argilla in g/kg, carbonio organico (SOC) in dg/kg.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SoilData:
    sand_gkg: float
    clay_gkg: float
    soc_dgkg: float


@dataclass(frozen=True, slots=True)
class SoilHydraulics:
    theta_fc: float  # capacità di campo, %vol (−33 kPa)
    theta_wp: float  # punto di appassimento, %vol (−1500 kPa)

    @property
    def available(self) -> float:
        return self.theta_fc - self.theta_wp


def saxton_rawls(soil: SoilData) -> SoilHydraulics:
    s = soil.sand_gkg / 1000  # frazione
    c = soil.clay_gkg / 1000
    om = soil.soc_dgkg / 100 * 1.724  # SOC dg/kg → %, poi sostanza organica (fattore di van Bemmelen)

    t1500t = (-0.024 * s + 0.487 * c + 0.006 * om + 0.005 * (s * om)
              - 0.013 * (c * om) + 0.068 * (s * c) + 0.031)
    t1500 = t1500t + (0.14 * t1500t - 0.02)

    t33t = (-0.251 * s + 0.195 * c + 0.011 * om + 0.006 * (s * om)
            - 0.027 * (c * om) + 0.452 * (s * c) + 0.299)
    t33 = t33t + (1.283 * t33t ** 2 - 0.374 * t33t - 0.015)

    return SoilHydraulics(theta_fc=round(t33 * 100, 2), theta_wp=round(t1500 * 100, 2))


def relative_available_water(theta: float, h: SoilHydraulics) -> float:
    """RAW = (θ − θ_WP) / (θ_FC − θ_WP), limitata a [0, 1] (§9.2 punto 3)."""
    if h.available <= 0:
        return 0.0
    return max(0.0, min(1.0, (theta - h.theta_wp) / h.available))
