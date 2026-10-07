"""Verificatore di completezza (§8.1, RF-IOT-08): calcola lo stato di ogni stazione."""

from __future__ import annotations

from datetime import datetime, timedelta

from .catalog import SENSOR_TYPES
from .domain import Station, StatoStazione
from .registry import StationRegistry

S = StatoStazione


def channel_label(sensor_type: str, depth: int | None) -> str:
    base = SENSOR_TYPES[sensor_type].descrizione.lower()
    return f"{base} {depth} cm" if depth is not None else base


class CompletenessChecker:
    def __init__(self, registry: StationRegistry, pipeline_grace_s: float = 0.0) -> None:
        self.registry = registry
        # tolleranza per la latenza della pipeline espressa in tempo simulato
        # (1 s reale di batch a ×360 sono 6 minuti simulati)
        self.pipeline_grace_s = pipeline_grace_s

    def evaluate(self, st: Station, now: datetime) -> tuple[StatoStazione, str]:
        cad = st.cadenza_s
        fresh = now - timedelta(seconds=2 * cad + self.pipeline_grace_s)
        offline = now - timedelta(seconds=10 * cad + self.pipeline_grace_s)
        missing = [s for s in st.sensors
                   if s.tipo.obbligatorio and (s.ultima_misura_il is None or s.ultima_misura_il < fresh)]
        cur = st.stato

        if cur is S.REGISTRATA:
            if not missing:
                return S.ATTIVO_COMPLETO, "tutti i 16 canali con misura valida entro 2 intervalli"
            return cur, ""
        if st.ultimo_contatto_il is None or st.ultimo_contatto_il < offline:
            return S.OFFLINE, "nessuna misura da 10 intervalli"
        if not missing:
            motivo = "ripresa completa" if cur is S.OFFLINE else "canali obbligatori di nuovo presenti"
            return S.ATTIVO_COMPLETO, motivo
        names = ", ".join(channel_label(s.sensor_type, s.profondita_cm) for s in missing[:3])
        extra = f" e altri {len(missing) - 3}" if len(missing) > 3 else ""
        if cur is S.OFFLINE:
            return S.DEGRADATA, f"ripresa parziale: manca {names}{extra}"
        return S.DEGRADATA, f"manca {names}{extra}"

    def run(self, now: datetime) -> int:
        changed = 0
        for st in self.registry.list():
            new, motivo = self.evaluate(st, now)
            if new is not st.stato and self.registry.set_state(st, new, motivo, now):
                changed += 1
        return changed
