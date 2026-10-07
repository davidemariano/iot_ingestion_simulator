"""Terreni salvati dell'utente demo (sostituiscono ``user_land_parcels`` + ``soil_data``).

Sei appezzamenti nel Salento, attorno al punto d'esempio del §8.5 (40.1453, 18.1931),
con tessiture diverse così che il modello di stress idrico dia risposte diverse.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field

from . import geo
from .soil import SoilData, SoilHydraulics, saxton_rawls

DEMO_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


@dataclass(slots=True)
class Land:
    id: uuid.UUID
    nome: str
    coltura: str
    ring: geo.Ring
    soil: SoilData
    ec_base: float  # conducibilità di riferimento alla capacità di campo, dS/m
    owner_id: uuid.UUID = DEMO_USER_ID
    di_prova: bool = False
    area_m2: float = field(init=False)
    hydraulics: SoilHydraulics = field(init=False)

    def __post_init__(self) -> None:
        self.area_m2 = geo.area_m2(self.ring)
        self.hydraulics = saxton_rawls(self.soil)

    @property
    def area_ha(self) -> float:
        return self.area_m2 / 10_000

    def contains(self, lat: float, lon: float) -> bool:
        return geo.contains(self.ring, lat, lon)

    def random_point(self, rng: random.Random) -> tuple[float, float]:
        lat_min, lon_min, lat_max, lon_max = geo.bbox(self.ring)
        for _ in range(500):
            lat = rng.uniform(lat_min, lat_max)
            lon = rng.uniform(lon_min, lon_max)
            if self.contains(lat, lon):
                return lat, lon
        raise RuntimeError(f"nessun punto interno trovato per {self.nome}")

    def centroid(self) -> tuple[float, float]:
        lat = sum(p[1] for p in self.ring) / len(self.ring)
        lon = sum(p[0] for p in self.ring) / len(self.ring)
        return lat, lon


def _uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"6c616e64-0000-4000-8000-{n:012x}")


# Coordinate (lon, lat). Poligoni tracciati a mano, 3–25 ha.
_SEED: list[tuple[str, str, geo.Ring, SoilData, float]] = [
    ("Vigneto Serrone", "Vite (Negroamaro)",
     [(18.1858, 40.1489), (18.1931, 40.1501), (18.1952, 40.1462), (18.1902, 40.1441), (18.1861, 40.1452)],
     SoilData(sand_gkg=420, clay_gkg=200, soc_dgkg=110), 0.9),
    ("Uliveto Le Chiuse", "Olivo",
     [(18.1985, 40.1532), (18.2071, 40.1540), (18.2079, 40.1488), (18.2010, 40.1475), (18.1979, 40.1497)],
     SoilData(sand_gkg=300, clay_gkg=380, soc_dgkg=140), 1.4),
    ("Seminativo Tre Masserie", "Grano duro",
     [(18.1772, 40.1418), (18.1880, 40.1425), (18.1889, 40.1361), (18.1781, 40.1352)],
     SoilData(sand_gkg=360, clay_gkg=300, soc_dgkg=120), 1.2),
    ("Orto Canale dell'Asso", "Orticole",
     [(18.1968, 40.1420), (18.2014, 40.1428), (18.2021, 40.1398), (18.1974, 40.1391)],
     SoilData(sand_gkg=680, clay_gkg=110, soc_dgkg=90), 0.6),
    ("Frutteto Ponente", "Pesco",
     [(18.1790, 40.1530), (18.1842, 40.1536), (18.1848, 40.1497), (18.1797, 40.1492)],
     SoilData(sand_gkg=500, clay_gkg=180, soc_dgkg=100), 0.8),
    ("Pascolo Cavallino", "Erbaio",
     [(18.2052, 40.1452), (18.2128, 40.1458), (18.2133, 40.1405), (18.2091, 40.1392), (18.2049, 40.1410)],
     SoilData(sand_gkg=380, clay_gkg=260, soc_dgkg=160), 1.0),
]


def seed_lands() -> list[Land]:
    return [
        Land(id=_uuid(i + 1), nome=nome, coltura=coltura, ring=ring, soil=soil, ec_base=ec)
        for i, (nome, coltura, ring, soil, ec) in enumerate(_SEED)
    ]


def make_test_lands(n: int, start_index: int, rng: random.Random) -> list[Land]:
    """Lotti di prova da 1 ha in griglia a ovest dei terreni reali (per ``--crea-stazioni N``)."""
    out: list[Land] = []
    origin_lat, origin_lon = 40.1530, 18.1640
    side = 100.0  # m
    gap = 30.0
    cols = 10
    for k in range(n):
        idx = start_index + k
        r, c = divmod(idx, cols)
        lat0, lon0 = geo.offset(origin_lat, origin_lon, -r * (side + gap), c * (side + gap) - cols * (side + gap))
        lat1, lon1 = geo.offset(lat0, lon0, -side, side)
        ring = [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)]
        soil = SoilData(sand_gkg=rng.uniform(250, 650), clay_gkg=rng.uniform(120, 380),
                        soc_dgkg=rng.uniform(80, 170))
        out.append(Land(id=_uuid(1000 + idx), nome=f"Lotto di prova {idx + 1:03d}", coltura="Prova",
                        ring=ring, soil=soil, ec_base=rng.uniform(0.6, 1.4), di_prova=True))
    return out
