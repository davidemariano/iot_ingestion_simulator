from __future__ import annotations

from fastapi.testclient import TestClient

from iotsim.api import create_app


def _client(h):
    return TestClient(create_app(h.e, run_loops=False))


def _inside(h, idx):
    land = list(h.e.registry.lands.values())[idx]
    return land.random_point(h.e.rng)


def test_register_list_detail_delete(h):
    c = _client(h)
    r = c.post("/api/iot/stations", json={"nome": "Fuori", "lat": 41.9, "lon": 12.5})
    assert r.status_code == 422
    assert r.json()["detail"] == "Il punto non ricade in nessuno dei tuoi terreni salvati"

    lat, lon = _inside(h, 2)
    r = c.post("/api/iot/stations", json={"nome": "Nuova", "lat": lat, "lon": lon})
    assert r.status_code == 201
    body = r.json()
    assert len(body["sensors"]) == 16 and body["stato"] == "REGISTRATA" and body["sorgente"] == "SATELLITE"
    sid = body["id"]

    lat2, lon2 = _inside(h, 2)
    r = c.post("/api/iot/stations", json={"nome": "Doppia", "lat": lat2, "lon": lon2})
    assert r.status_code == 409

    h.advance(900)
    stations = c.get("/api/iot/stations").json()
    assert [s["stato"] for s in stations] == ["ATTIVO_COMPLETO"]

    latest = c.get(f"/api/iot/stations/{sid}/latest").json()
    assert len(latest["canali"]) == 16 and all(ch["presente"] for ch in latest["canali"])
    assert "raw_suolo" in latest["indicatori"]

    raw = c.get(f"/api/iot/stations/{sid}/measurements", params={"type": "soil_moisture", "depth": 15}).json()
    assert raw["unit"] == "%vol" and len(raw["punti"]) >= 4

    ds = c.get(f"/api/iot/lands/{body['land_id']}/data-source").json()
    assert ds["sorgente"] == "IOT"

    r = c.patch(f"/api/iot/stations/{sid}", json={"cadenza_s": 10})
    assert r.status_code == 422
    assert c.patch(f"/api/iot/stations/{sid}", json={"cadenza_s": 300}).json()["cadenza_s"] == 300

    r = c.request("DELETE", "/api/iot/stations/batch", json={"ids": [sid]})
    assert r.json()["dismesse"] == [sid]
    assert c.get("/api/iot/stations").json() == []
    assert c.get(f"/api/iot/lands/{body['land_id']}/data-source").json()["sorgente"] == "SATELLITE"


def test_role_agricoltore_is_read_only(h):
    c = _client(h)
    lat, lon = _inside(h, 0)
    r = c.post("/api/iot/stations", json={"nome": "X", "lat": lat, "lon": lon}, headers={"X-Ruolo": "agricoltore"})
    assert r.status_code == 403
    assert c.get("/api/iot/stations", headers={"X-Ruolo": "agricoltore"}).status_code == 200


def test_raw_series_limited_to_seven_days(h):
    c = _client(h)
    st = h.register(0)
    r = c.get(f"/api/iot/stations/{st.id}/measurements",
              params={"type": "rain", "from": "2026-09-01T00:00:00Z", "agg": "raw"})
    assert r.status_code == 422


def test_sim_controls(h):
    c = _client(h)
    st = h.register(0)
    r = c.patch("/api/sim/config", json={"fattore": 60, "scenario_meteo": "siccita",
                                         "guasti": {"duplicati_pct": 2}})
    assert r.status_code == 200 and r.json()["guasti"]["duplicati_pct"] == 2
    assert c.patch("/api/sim/config", json={"guasti": {"inesistente": 1}}).status_code == 422
    silenced = [st.sensors[0].device_uid]
    r = c.put(f"/api/sim/stations/{st.id}/faults", json={"canali_silenziati": silenced})
    assert r.json()["canali_silenziati"] == silenced
    lands = c.get("/api/lands/my-lands").json()
    assert len(lands["features"]) == 6
    s = c.get("/api/sim/status").json()
    assert s["stazioni"]["totale"] == 1 and "p95" in s["latenza_s"]
