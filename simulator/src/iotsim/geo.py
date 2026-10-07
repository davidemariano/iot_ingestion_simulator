"""Geometria minima per il PoC: punto nel poligono, area, distanze (WGS84).

Sostituisce ST_Contains / ST_Area di PostGIS nel simulatore: i terreni sono piccoli
(pochi ettari), quindi una proiezione equirettangolare locale è più che sufficiente.
"""

from __future__ import annotations

import math

EARTH_R = 6_371_008.8  # raggio medio, m

Ring = list[tuple[float, float]]  # [(lon, lat), ...] come in GeoJSON


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Sposta un punto di (north_m, east_m) metri."""
    dlat = north_m / EARTH_R
    dlon = east_m / (EARTH_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def contains(ring: Ring, lat: float, lon: float) -> bool:
    """Ray casting; il bordo conta come esterno (come ST_Contains)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def area_m2(ring: Ring) -> float:
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = EARTH_R * math.cos(math.radians(lat0)) * math.pi / 180
    ky = EARTH_R * math.pi / 180
    pts = [(lon * kx, lat * ky) for lon, lat in ring]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def bbox(ring: Ring) -> tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lats), min(lons), max(lats), max(lons)
