from __future__ import annotations

import pytest

from iotsim.catalog import station_channels
from iotsim.lands import DEMO_USER_ID, Land
from iotsim.registry import RegistrationError
from iotsim.soil import SoilData, relative_available_water, saxton_rawls
from iotsim.storage import circular_mean_deg


def test_sixteen_channels_and_device_uid():
    chans = station_channels()
    assert len(chans) == 16
    assert [c.device_uid("GW-3F9A1C") for c in chans][:2] == [
        "GW-3F9A1C-SOIL_MOISTURE-15", "GW-3F9A1C-SOIL_MOISTURE-35"]
    assert chans[-1].device_uid("GW-3F9A1C") == "GW-3F9A1C-ACCELEROMETER"


def test_saxton_rawls_loam():
    h = saxton_rawls(SoilData(sand_gkg=420, clay_gkg=200, soc_dgkg=110))
    assert h.theta_fc == pytest.approx(26.9, abs=0.2)
    assert h.theta_wp == pytest.approx(13.3, abs=0.2)
    assert relative_available_water(h.theta_wp, h) == 0
    assert relative_available_water(h.theta_fc + 5, h) == 1


def test_circular_mean():
    assert circular_mean_deg([350, 10]) == pytest.approx(0, abs=1e-6) or circular_mean_deg([350, 10]) == pytest.approx(360)
    assert circular_mean_deg([80, 100]) == pytest.approx(90)


def test_registration_rules(h):
    reg = h.e.registry
    with pytest.raises(RegistrationError) as exc:
        reg.register(DEMO_USER_ID, "Fuori", 41.9, 12.5)
    assert exc.value.status == 422
    with pytest.raises(RegistrationError) as exc:
        h.register(0, nome="   ")
    assert exc.value.status == 422

    st = h.register(0, "Prima")
    assert len(st.sensors) == 16 and st.stato == "REGISTRATA" and st.cadenza_s == 180
    assert reg.broker.is_enabled(st.gateway_id)
    with pytest.raises(RegistrationError) as exc:
        h.register(0, "Seconda")
    assert exc.value.status == 409

    reg.decommission([st.id])  # D8: dopo la dismissione il terreno è di nuovo libero
    assert h.register(0, "Terza").nome == "Terza"


def test_overlapping_lands_pick_smallest(h):
    big = Land(id=__import__("uuid").uuid4(), nome="Grande", coltura="x",
               ring=[(18.0, 40.0), (18.02, 40.0), (18.02, 40.02), (18.0, 40.02)],
               soil=SoilData(400, 200, 100), ec_base=1)
    small = Land(id=__import__("uuid").uuid4(), nome="Piccolo", coltura="x",
                 ring=[(18.005, 40.005), (18.01, 40.005), (18.01, 40.01), (18.005, 40.01)],
                 soil=SoilData(400, 200, 100), ec_base=1)
    h.e.registry.add_lands([big, small])
    assert h.e.registry.land_for_point(DEMO_USER_ID, 40.007, 18.007).nome == "Piccolo"
    assert h.e.registry.land_for_point(DEMO_USER_ID, 40.015, 18.015).nome == "Grande"
