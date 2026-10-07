"""Modalità ``traffico``: il simulatore del PoC (§13.3) verso lo stack reale.

- token dal login proxy (``POST {api}/keycloak/login``, come ``script_comune``) o passato a mano;
- rilevamento delle stazioni con ``GET /iot/stations`` ogni 60 s simulati (+ dettaglio per i canali);
- ``--crea-stazioni N``: registra N stazioni via API in punti casuali dei terreni liberi;
- pubblicazione QoS 1 con un unico account MQTT (semplificazione S7 del PoC).
"""

from __future__ import annotations

import logging
import random
import signal
import time
import uuid
from dataclasses import dataclass
from typing import Any

from . import geo
from .clock import SimClock, utcnow
from .fleet import Fleet, StationSpec
from .soil import SoilHydraulics
from .station_sim import ChannelRef, Outgoing
from .weather import WeatherField

log = logging.getLogger("iotsim.traffico")

DEFAULT_HYDRAULICS = SoilHydraulics(theta_fc=30.0, theta_wp=14.0)


@dataclass(slots=True)
class TrafficConfig:
    api_url: str
    token: str | None
    login_url: str | None
    username: str | None
    password: str | None
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str | None
    mqtt_password: str | None
    tls: bool
    ca_file: str | None
    fattore: float
    durata_sim_s: float | None
    seme: int
    crea_stazioni: int
    scoperta_s: float
    duplicati_pct: float = 0
    fuori_intervallo_pct: float = 0
    fuori_ordine_pct: float = 0
    ritardo_pct: float = 0
    malformati_pct: float = 0


class ApiClient:
    def __init__(self, cfg: TrafficConfig) -> None:
        import httpx

        self.base = cfg.api_url
        headers = {"Accept": "application/json"}
        token = cfg.token or self._login(cfg)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=self.base, headers=headers, timeout=10)
        self._details: dict[uuid.UUID, dict[str, Any]] = {}

    def _login(self, cfg: TrafficConfig) -> str | None:
        if not (cfg.username and cfg.password):
            return None
        import httpx

        url = cfg.login_url or f"{cfg.api_url}/keycloak/login"
        r = httpx.post(url, json={"username": cfg.username, "password": cfg.password}, timeout=10)
        r.raise_for_status()
        body = r.json()
        return body.get("access_token") or body.get("token")

    def stations(self) -> list[StationSpec]:
        r = self.http.get("/iot/stations")
        r.raise_for_status()
        out = []
        for s in r.json():
            sid = uuid.UUID(s["id"])
            d = self._details.get(sid)
            if d is None:
                rd = self.http.get(f"/iot/stations/{sid}")
                rd.raise_for_status()
                d = self._details[sid] = rd.json()
            hyd = (SoilHydraulics(d["theta_fc"], d["theta_wp"]) if "theta_fc" in d and "theta_wp" in d
                   else DEFAULT_HYDRAULICS)
            channels = tuple(ChannelRef(c["device_uid"], c["sensor_type"], c.get("profondita_cm"))
                             for c in d["sensors"])
            out.append(StationSpec(id=sid, gateway_id=d["gateway_id"], nome=d["nome"], channels=channels,
                                   lat=d["lat"], lon=d["lon"], cadenza_s=s.get("cadenza_s", 180), hydraulics=hyd))
        return out

    def create_stations(self, n: int, rng: random.Random) -> int:
        lands = self.http.get("/lands/my-lands").json()["features"]
        free = [f for f in lands if not f["properties"].get("station_id")]
        created = 0
        for f in free[:n]:
            ring = [tuple(p) for p in f["geometry"]["coordinates"][0][:-1]]
            lat_min, lon_min, lat_max, lon_max = geo.bbox(ring)
            for _ in range(200):
                lat, lon = rng.uniform(lat_min, lat_max), rng.uniform(lon_min, lon_max)
                if geo.contains(ring, lat, lon):
                    break
            r = self.http.post("/iot/stations",
                               json={"nome": f"Sim {f['properties']['nome'][:48]}", "lat": lat, "lon": lon})
            if r.status_code == 201:
                created += 1
            else:
                log.warning("registrazione su %s rifiutata: %s", f["properties"]["nome"], r.text)
        if created < n:
            log.warning("create %d stazioni su %d richieste: terreni liberi insufficienti", created, n)
        return created


class MqttPublisher:
    def __init__(self, cfg: TrafficConfig) -> None:
        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"iot-simulator-{cfg.seme}-{uuid.uuid4().hex[:6]}")
        if cfg.mqtt_user:
            self.client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        if cfg.tls:
            self.client.tls_set(ca_certs=cfg.ca_file)
        self.client.max_queued_messages_set(0)
        self.client.max_inflight_messages_set(200)
        self.client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=30)
        self.client.loop_start()

    def start(self, spec: StationSpec) -> None:  # account unico nel PoC (S7)
        pass

    def stop(self, station_id: uuid.UUID) -> None:
        pass

    def publish(self, item: Outgoing, payload: bytes, sent_at_real: float) -> str | None:
        info = self.client.publish(item.topic, payload, qos=1, retain=False)
        return None if info.rc == self._mqtt.MQTT_ERR_SUCCESS else f"mqtt_rc_{info.rc}"

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def run_traffic(cfg: TrafficConfig) -> int:
    rng = random.Random(cfg.seme)
    api = ApiClient(cfg)
    if cfg.crea_stazioni:
        log.info("registrazione di %d stazioni via API…", cfg.crea_stazioni)
        api.create_stations(cfg.crea_stazioni, rng)
    pub = MqttPublisher(cfg)
    start = utcnow().replace(microsecond=0)
    clock = SimClock(start, cfg.fattore)
    weather = WeatherField(random.Random(rng.getrandbits(64)), start.timestamp())
    fleet = Fleet(api.stations, pub, weather, random.Random(rng.getrandbits(64)))
    g = fleet.global_faults
    g.duplicati_pct, g.fuori_intervallo_pct = cfg.duplicati_pct, cfg.fuori_intervallo_pct
    g.fuori_ordine_pct, g.ritardo_pct, g.malformati_pct = cfg.fuori_ordine_pct, cfg.ritardo_pct, cfg.malformati_pct

    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    next_discovery = 0.0
    last_report = time.monotonic()
    t_end = start.timestamp() + cfg.durata_sim_s if cfg.durata_sim_s else None
    log.info("traffico verso %s:%d, ×%g, seme %d", cfg.mqtt_host, cfg.mqtt_port, cfg.fattore, cfg.seme)
    try:
        while not stop:
            t = clock.now().timestamp()
            if t_end and t >= t_end:
                break
            weather.advance_to(t)
            if t >= next_discovery:
                try:
                    started, stopped = fleet.discover(t)
                    if started or stopped:
                        log.info("stazioni: +%d −%d (attive %d)", len(started), len(stopped), len(fleet.sims))
                except Exception as exc:  # API momentaneamente non raggiungibile: si riprova
                    log.warning("rilevamento fallito: %s", exc)
                next_discovery = t + cfg.scoperta_s
            fleet.publish_due(t)
            if time.monotonic() - last_report > 10:
                log.info("pubblicati %d (rifiutati %d) · ora simulata %s", fleet.pubblicati, fleet.rifiutati,
                         clock.now().isoformat(timespec="seconds"))
                last_report = time.monotonic()
            time.sleep(0.2)
    finally:
        pub.close()
    log.info("fine: %d messaggi pubblicati, %d rifiutati", fleet.pubblicati, fleet.rifiutati)
    return 0
