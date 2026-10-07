from __future__ import annotations

import json
import time
from datetime import timedelta

from iotsim.broker import telemetry_topic


def _send(h, st, sensor, **over):
    payload = {"gateway_id": st.gateway_id, "device_uid": sensor.device_uid, "type": sensor.sensor_type,
               "ts": h.clock.now().isoformat(), "value": 21.5, "unit": sensor.tipo.unit, "seq": 1}
    payload.update(over)
    raw = payload.pop("_raw", None)
    body = raw if raw is not None else json.dumps(payload).encode()
    cred = h.e.registry.credentials[st.gateway_id]
    return h.e.broker.publish(cred, telemetry_topic(st.gateway_id, sensor.device_uid), body, time.time())


def test_validation_causes_and_dedup(h):
    st = h.register(0)
    air = st.sensor_by_type("air_temperature")
    now = h.clock.now()
    _send(h, st, air, seq=1)
    _send(h, st, air, seq=1)  # duplicato
    _send(h, st, air, seq=2, value=99)  # fuori intervallo
    _send(h, st, air, seq=3, ts=(now + timedelta(minutes=5)).isoformat())
    _send(h, st, air, seq=4, ts=(now - timedelta(hours=25)).isoformat())
    _send(h, st, air, seq=5, type="rain")  # tipo incoerente col canale
    _send(h, st, air, seq=6, ts=(now - timedelta(minutes=3)).isoformat(), value=70.7, unit="°F")
    _send(h, st, air, _raw=b"{non json")
    h.e.drain()

    m = h.e.metrics
    assert m.salvati["iot_measurements"] == 2
    assert m.scartati == {"duplicato": 1, "fuori_intervallo": 1, "timestamp_futuro": 1,
                          "timestamp_scaduto": 1, "tipo_incoerente": 1, "schema": 1}
    assert m.normalizzati == 1
    series = h.e.repo.scalars[air.id]
    assert round(series.v[0], 2) == 21.5  # 70,7 °F → 21,5 °C
    assert h.e.broker.inflight == 0  # PUBACK anche per gli scartati


def test_vector_channels_routed_to_dedicated_tables(h):
    st = h.register(0)
    gps, acc = st.sensor_by_type("gps"), st.sensor_by_type("accelerometer")
    _send(h, st, gps, value={"lat": st.lat, "lon": st.lon, "alt_m": 80, "hdop": 0.9, "sats": 11}, unit="deg")
    _send(h, st, acc, value={"ax": 0.0, "ay": 0.5, "az": 0.866}, unit="g")
    h.e.drain()
    assert h.e.metrics.salvati == {"iot_station_positions": 1, "iot_station_motion": 1}
    incl = h.e.repo.motion[acc.id].rows[0][3]
    assert abs(incl - 30) < 0.1


def test_acl_rejects_foreign_topic(h):
    a, b = h.register(0, "A"), h.register(1, "B")
    cred_a = h.e.registry.credentials[a.gateway_id]
    r = h.e.broker.publish(cred_a, telemetry_topic(b.gateway_id, b.sensors[0].device_uid), b"{}", time.time())
    assert r == "acl_negata"


def test_restart_under_load_has_no_loss(h):
    h.register(0)
    h.register(1)
    h.advance(1800)
    # messaggi accodati e non ancora elaborati, poi crash dopo il commit ma prima del PUBACK
    h.clock.advance(180)
    h.e.tick()
    n = h.e.ingestion.restart(downtime_s=0)
    assert n > 0
    h.advance(600)
    m = h.e.metrics
    assert m.scartati["duplicato"] == n
    assert m.perdite() == 0


def test_db_unavailable_then_recovers_without_loss(h):
    h.register(0)
    h.advance(600)
    saved_before = sum(h.e.metrics.salvati.values())
    h.e.repo.available = False
    h.e.db_down_until = time.monotonic() + 3600
    h.advance(900)
    assert sum(h.e.metrics.salvati.values()) == saved_before
    assert h.e.metrics.errori_db > 0 and h.e.broker.queued > 0
    h.e.db_down_until = 0
    h.advance(60)
    assert h.e.broker.queued == 0
    assert sum(h.e.metrics.salvati.values()) > saved_before
    assert h.e.metrics.perdite() == 0


def test_fault_injection_counts(h):
    st = h.register(0)
    g = h.e.fleet.global_faults
    g.duplicati_pct, g.fuori_intervallo_pct, g.unita_alternative_pct = 10, 5, 10
    h.advance(6 * 3600, step=60)
    m = h.e.metrics
    assert m.scartati["duplicato"] > 0 and m.scartati["fuori_intervallo"] > 0
    assert m.normalizzati > 0
    assert m.perdite() == 0
    assert st.stato == "ATTIVO_COMPLETO"
