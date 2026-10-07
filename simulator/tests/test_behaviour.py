"""Criteri di accettazione del §13.4 riprodotti sul motore con orologio manuale."""

from __future__ import annotations

from iotsim.domain import DataSource, StatoStazione as S
from iotsim.weather import Scenario


def test_ac7_registered_station_gets_data_and_becomes_complete(h):
    st = h.register(0)
    h.advance(60)
    assert st.ultimo_contatto_il is not None  # dati entro 60 s
    first = st.ultimo_contatto_il
    while st.stato is S.REGISTRATA:
        h.advance(10, step=10)
    assert st.stato is S.ATTIVO_COMPLETO
    assert (h.clock.now() - first).total_seconds() <= 2 * st.cadenza_s
    assert h.e.registry.data_source(st.land_id)[0] is DataSource.IOT


def test_ac2_silenced_channel_falls_back_to_satellite_and_recovers(h):
    st = h.register(0)
    h.advance(900)
    assert st.stato is S.ATTIVO_COMPLETO
    ec = st.sensor_by_type("soil_ec")
    h.e.fleet.faults[st.id].canali_silenziati = {ec.device_uid}
    t0 = h.clock.now()
    while st.stato is S.ATTIVO_COMPLETO:
        h.advance(30)
    # rilevata entro 2 intervalli dal primo campione mancante (+ periodo del verificatore)
    assert (h.clock.now() - t0).total_seconds() <= 3 * st.cadenza_s + 30
    assert st.stato is S.DEGRADATA
    assert h.e.registry.data_source(st.land_id)[0] is DataSource.SATELLITE
    h.e.fleet.faults[st.id].canali_silenziati = set()
    h.advance(2 * st.cadenza_s + 60)
    assert st.stato is S.ATTIVO_COMPLETO


def test_offline_after_ten_intervals(h):
    st = h.register(0)
    h.advance(900)
    h.e.fleet.faults[st.id].offline = True
    h.advance(11 * st.cadenza_s + 180)
    assert st.stato is S.OFFLINE
    h.e.fleet.faults[st.id].offline = False
    h.advance(3 * st.cadenza_s)
    assert st.stato is S.ATTIVO_COMPLETO
    log = [(t.da_stato, t.a_stato) for t in h.e.repo.status_log if t.station_id == st.id]
    # silenzio totale: DEGRADATA dopo 2 intervalli (sorgente SATELLITE), OFFLINE dopo 10
    assert (S.ATTIVO_COMPLETO, S.DEGRADATA) in log and (S.DEGRADATA, S.OFFLINE) in log
    assert (S.OFFLINE, S.ATTIVO_COMPLETO) in log


def test_ac9_decommission_revokes_and_frees_land(h):
    st = h.register(0)
    h.advance(900)
    h.e.registry.decommission([st.id])
    assert st.stato is S.DISMESSA
    assert all(not s.attivo for s in st.sensors)
    assert h.e.registry.data_source(st.land_id)[0] is DataSource.SATELLITE
    assert st not in h.e.registry.list()
    # prima del prossimo rilevamento il simulatore trasmette ancora con le credenziali in cache
    sim = h.e.fleet.sims[st.id]
    sim.next_due = h.clock.now().timestamp()
    h.e.fleet.publish_due(sim.next_due)
    assert h.e.broker.rifiutati["non_autorizzato"] == 16  # credenziali revocate
    h.advance(120)
    assert st.id not in h.e.fleet.sims  # il rilevamento ha fermato il traffico


def test_ac4_water_stress_opens_in_drought_and_closes_after_rain(h):
    land_idx = 3  # terreno sabbioso
    land = list(h.e.registry.lands.values())[land_idx]
    lat, lon = land.random_point(h.e.rng)
    st = h.e.registry.register(__import__("iotsim.lands").lands.DEMO_USER_ID, "Asciutta", lat, lon)
    h.e.fleet.initial_raw[st.id] = 0.42
    h.advance(1800)
    alert = h.e.repo.find_open(st.id, "stress_idrico")
    assert alert is not None and alert.severita == "attenzione"
    h.e.set_scenario(Scenario.PIOGGIA)
    h.advance(8 * 3600, step=60)
    assert h.e.repo.find_open(st.id, "stress_idrico") is None


def test_integrity_moved_tilted_and_shock(h):
    st = h.register(0)
    h.advance(900)
    f = h.e.fleet.faults[st.id]
    f.spostata_m = 150
    f.inclinata_deg = 30
    f.urto = True
    h.advance(1800, step=60)
    tipi = {a.tipo for a in h.e.repo.open_alerts(st.id)}
    assert {"stazione_spostata", "stazione_inclinata", "urto"} <= tipi
    h.advance(3600, step=60)
    urto = next(a for a in h.e.repo.alerts if a.tipo == "urto")
    assert urto.chiusa_il is not None  # evento: si chiude da solo dopo un'ora senza nuovi urti
    assert st.stato is S.ATTIVO_COMPLETO  # §9.3: l'allerta non cambia lo stato
    f.spostata_m = f.inclinata_deg = 0
    h.advance(2 * 3600, step=60)
    tipi = {a.tipo for a in h.e.repo.open_alerts(st.id)}
    assert not tipi & {"stazione_spostata", "stazione_inclinata", "urto"}


def test_stuck_sensor_flagged_after_six_hours(h):
    st = h.register(0)
    h.advance(1800)
    p = st.sensor_by_type("barometric_pressure")
    h.e.fleet.faults[st.id].canali_bloccati = {p.device_uid}
    h.advance(5 * 3600, step=60)
    assert h.e.repo.find_open(st.id, "sensore_sospetto") is None
    h.advance(1.5 * 3600, step=60)
    assert h.e.repo.find_open(st.id, "sensore_sospetto") is not None
    assert h.e.repo.scalars[p.id].q[-1] == 1


def test_hourly_aggregates_use_the_right_function(h):
    st = h.register(0)
    h.e.set_scenario(Scenario.PIOGGIA)
    h.advance(4 * 3600, step=60)
    rain = st.sensor_by_type("rain")
    hourly = h.e.repo.hourly[rain.id]
    assert hourly and any((a.v_sum or 0) > 0 for a in hourly.values())
    for hour, a in hourly.items():
        _, v, _ = h.e.repo.scalars[rain.id].window(hour, hour + 3600 - 1e-6)
        assert abs(a.v_sum - sum(v)) < 1e-9
    wd = h.e.repo.hourly[st.sensor_by_type("wind_direction").id]
    assert all(0 <= a.v_avg < 360 for a in wd.values())
