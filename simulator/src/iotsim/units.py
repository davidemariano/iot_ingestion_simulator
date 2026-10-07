"""Normalizzazione delle unità verso quella canonica del catalogo (RF-IOT-06)."""

from __future__ import annotations

from collections.abc import Callable

_Conv = Callable[[float], float]

_ident: _Conv = lambda v: v  # noqa: E731

# unità canonica -> {unità ricevuta: conversione}
CONVERSIONS: dict[str, dict[str, _Conv]] = {
    "%vol": {"%vol": _ident, "%": _ident, "m3/m3": lambda v: v * 100},
    "°C": {"°C": _ident, "C": _ident, "degC": _ident, "°F": lambda v: (v - 32) * 5 / 9,
           "F": lambda v: (v - 32) * 5 / 9, "K": lambda v: v - 273.15},
    "%": {"%": _ident},
    "mm": {"mm": _ident, "in": lambda v: v * 25.4},
    "dS/m": {"dS/m": _ident, "mS/cm": _ident, "uS/cm": lambda v: v / 1000, "µS/cm": lambda v: v / 1000},
    "W/m2": {"W/m2": _ident, "W/m²": _ident},
    "m/s": {"m/s": _ident, "km/h": lambda v: v / 3.6, "kn": lambda v: v * 0.514444},
    "deg": {"deg": _ident, "°": _ident},
    "lx": {"lx": _ident, "klx": lambda v: v * 1000},
    "hPa": {"hPa": _ident, "mbar": _ident, "kPa": lambda v: v * 10, "Pa": lambda v: v / 100},
    "g": {"g": _ident, "m/s2": lambda v: v / 9.80665},
}

# unità alternative usate dal simulatore per esercitare la normalizzazione
ALTERNATIVE: dict[str, tuple[str, _Conv]] = {
    "°C": ("°F", lambda v: v * 9 / 5 + 32),
    "hPa": ("kPa", lambda v: v / 10),
    "lx": ("klx", lambda v: v / 1000),
    "m/s": ("km/h", lambda v: v * 3.6),
    "dS/m": ("uS/cm", lambda v: v * 1000),
    "%vol": ("m3/m3", lambda v: v / 100),
}


def normalize(value: float, unit: str, canonical: str) -> float | None:
    """Ritorna il valore nell'unità canonica, o None se l'unità non è convertibile."""
    conv = CONVERSIONS.get(canonical, {}).get(unit)
    return None if conv is None else conv(value)
