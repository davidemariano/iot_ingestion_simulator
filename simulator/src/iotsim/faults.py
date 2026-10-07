"""Guasti iniettabili (§13.3): globali sul traffico e specifici per stazione."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GlobalFaults:
    duplicati_pct: float = 0.0
    fuori_intervallo_pct: float = 0.0
    fuori_ordine_pct: float = 0.0
    ritardo_pct: float = 0.0
    ritardo_s: int = 900  # secondi simulati
    malformati_pct: float = 0.0
    unita_alternative_pct: float = 0.0


@dataclass(slots=True)
class StationFaults:
    offline: bool = False
    canali_silenziati: set[str] = field(default_factory=set)  # device_uid
    canali_bloccati: set[str] = field(default_factory=set)
    picchi_pct: float = 0.0
    spostata_m: float = 0.0
    inclinata_deg: float = 0.0
    urto: bool = False  # evento una tantum, consumato alla prossima misura dell'accelerometro

    def attivi(self) -> int:
        return (int(self.offline) + len(self.canali_silenziati) + len(self.canali_bloccati)
                + int(self.picchi_pct > 0) + int(self.spostata_m > 0) + int(self.inclinata_deg > 0))
