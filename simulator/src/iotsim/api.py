"""API HTTP: router ``/iot`` del §11, terreni dell'utente e controlli del simulatore.

L'autenticazione Keycloak (JWT) è fuori dal perimetro del simulatore: il ruolo arriva
nell'header ``X-Ruolo`` (``admin`` | ``agricoltore``) per esercitare i permessi del §12.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .catalog import SENSOR_TYPES, TipoValore
from .domain import Alert, Sensor, Station, StatoStazione
from .engine import Engine
from .faults import StationFaults
from .geo import haversine_m
from .lands import DEMO_USER_ID
from .registry import RegistrationError
from .storage import circular_mean_deg
from .weather import Scenario

Ruolo = Literal["admin", "agricoltore"]


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


# --- schemi -----------------------------------------------------------------------------------------
class StationIn(BaseModel):
    nome: str = Field(max_length=200)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class StationPatch(BaseModel):
    nome: str | None = None
    cadenza_s: int | None = None


class BatchDelete(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1)


class SensorOut(BaseModel):
    id: uuid.UUID
    sensor_type: str
    device_uid: str
    profondita_cm: int | None
    unit: str
    descrizione: str
    attivo: bool
    ultima_misura_il: str | None


class StationOut(BaseModel):
    id: uuid.UUID
    nome: str
    land_id: uuid.UUID
    land_nome: str
    lat: float
    lon: float
    gateway_id: str
    stato: StatoStazione
    sorgente: Literal["IOT", "SATELLITE"]
    cadenza_s: int
    registrata_il: str
    ultimo_contatto_il: str | None
    allerte_aperte: int
    allerta_max: Literal["attenzione", "critica"] | None


class StationDetailOut(StationOut):
    sensors: list[SensorOut]
    theta_fc: float
    theta_wp: float


class AlertOut(BaseModel):
    id: int
    tipo: str
    severita: str
    messaggio: str
    aperta_il: str
    chiusa_il: str | None


class DataSourceOut(BaseModel):
    land_id: uuid.UUID
    sorgente: Literal["IOT", "SATELLITE"]
    stato: StatoStazione | None
    station_id: uuid.UUID | None
    ultimo_contatto: str | None


class SimConfigIn(BaseModel):
    fattore: float | None = Field(default=None, ge=1, le=720)
    in_pausa: bool | None = None
    scenario_meteo: str | None = None
    guasti: dict[str, float] | None = None


class StationFaultsIn(BaseModel):
    offline: bool = False
    canali_silenziati: list[str] = []
    canali_bloccati: list[str] = []
    picchi_pct: float = Field(default=0, ge=0, le=100)
    spostata_m: float = Field(default=0, ge=0, le=5000)
    inclinata_deg: float = Field(default=0, ge=0, le=90)


# --- dipendenze -------------------------------------------------------------------------------------
def get_ruolo(x_ruolo: Annotated[str | None, Header()] = None) -> Ruolo:
    return "agricoltore" if (x_ruolo or "").lower() == "agricoltore" else "admin"


def require_admin(ruolo: Annotated[Ruolo, Depends(get_ruolo)]) -> Ruolo:
    if ruolo != "admin":
        raise HTTPException(403, "Operazione riservata al ruolo admin")
    return ruolo


def create_app(engine: Engine | None = None, run_loops: bool = True, static_dir: str | Path | None = None) -> FastAPI:
    from contextlib import asynccontextmanager

    eng = engine or Engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(eng.run()) if run_loops else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Agrivalor IoT — simulatore", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.engine = eng

    def station_or_404(station_id: uuid.UUID) -> Station:
        st = eng.registry.get(station_id)
        if st is None or not st.attiva:
            raise HTTPException(404, "Stazione non trovata")
        if st.registrata_da != DEMO_USER_ID:  # controllo di ownership (utente demo unico)
            raise HTTPException(404, "Stazione non trovata")
        return st

    def station_out(st: Station) -> dict[str, Any]:
        land = eng.registry.lands[st.land_id]
        sorgente, _ = eng.registry.data_source(st.land_id)
        open_alerts = eng.repo.open_alerts(st.id)
        sev = None
        if open_alerts:
            sev = "critica" if any(a.severita == "critica" for a in open_alerts) else "attenzione"
        return dict(id=st.id, nome=st.nome, land_id=st.land_id, land_nome=land.nome, lat=st.lat, lon=st.lon,
                    gateway_id=st.gateway_id, stato=st.stato, sorgente=sorgente.value, cadenza_s=st.cadenza_s,
                    registrata_il=_iso(st.registrata_il), ultimo_contatto_il=_iso(st.ultimo_contatto_il),
                    allerte_aperte=len(open_alerts), allerta_max=sev)

    def sensor_out(s: Sensor) -> dict[str, Any]:
        t = s.tipo
        return dict(id=s.id, sensor_type=s.sensor_type, device_uid=s.device_uid, profondita_cm=s.profondita_cm,
                    unit=t.unit, descrizione=t.descrizione, attivo=s.attivo,
                    ultima_misura_il=_iso(s.ultima_misura_il))

    def alert_out(a: Alert) -> dict[str, Any]:
        return dict(id=a.id, tipo=a.tipo, severita=a.severita, messaggio=a.messaggio,
                    aperta_il=_iso(a.aperta_il), chiusa_il=_iso(a.chiusa_il))

    # --- terreni ---------------------------------------------------------------------------------
    lands = APIRouter(prefix="/api/lands", tags=["terreni"])

    @lands.get("/my-lands")
    def my_lands() -> dict[str, Any]:
        feats = []
        for land in eng.registry.lands.values():
            if land.owner_id != DEMO_USER_ID:
                continue
            sorgente, st = eng.registry.data_source(land.id)
            ring = [list(p) for p in land.ring]
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]},
                "properties": {
                    "id": str(land.id), "nome": land.nome, "coltura": land.coltura,
                    "area_ha": round(land.area_ha, 2), "di_prova": land.di_prova,
                    "suolo": {"sabbia_gkg": land.soil.sand_gkg, "argilla_gkg": land.soil.clay_gkg,
                              "soc_dgkg": land.soil.soc_dgkg},
                    "theta_fc": land.hydraulics.theta_fc, "theta_wp": land.hydraulics.theta_wp,
                    "sorgente": sorgente.value, "station_id": str(st.id) if st else None,
                },
            })
        return {"type": "FeatureCollection", "features": feats}

    # --- router /iot (§11) -----------------------------------------------------------------------
    iot = APIRouter(prefix="/api/iot", tags=["iot"])

    @iot.post("/stations", status_code=201, response_model=StationDetailOut)
    def register(body: StationIn, _: Annotated[Ruolo, Depends(require_admin)]):
        try:
            st = eng.registry.register(DEMO_USER_ID, body.nome, body.lat, body.lon)
        except RegistrationError as e:
            raise HTTPException(e.status, e.message) from e
        return detail_out(st)

    @iot.get("/stations", response_model=list[StationOut])
    def list_stations(_: Annotated[Ruolo, Depends(get_ruolo)]):
        return [station_out(st) for st in eng.registry.list()]

    # /stations/batch prima di /stations/{id}, altrimenti "batch" verrebbe letto come UUID
    @iot.delete("/stations/batch")
    def delete_batch(body: BatchDelete, _: Annotated[Ruolo, Depends(require_admin)]):
        done = eng.registry.decommission(body.ids)
        return {"dismesse": [str(s.id) for s in done]}

    def detail_out(st: Station) -> dict[str, Any]:
        h = eng.registry.lands[st.land_id].hydraulics
        return {**station_out(st), "sensors": [sensor_out(s) for s in st.sensors],
                "theta_fc": h.theta_fc, "theta_wp": h.theta_wp}

    @iot.get("/stations/{station_id}", response_model=StationDetailOut)
    def get_station(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)]):
        return detail_out(station_or_404(station_id))

    @iot.patch("/stations/{station_id}", response_model=StationOut)
    def patch_station(station_id: uuid.UUID, body: StationPatch, _: Annotated[Ruolo, Depends(require_admin)]):
        station_or_404(station_id)
        try:
            st = eng.registry.update(station_id, body.nome, body.cadenza_s)
        except RegistrationError as e:
            raise HTTPException(e.status, e.message) from e
        return station_out(st)

    @iot.delete("/stations/{station_id}")
    def delete_station(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(require_admin)]):
        station_or_404(station_id)
        eng.registry.decommission([station_id])
        return {"dismesse": [str(station_id)]}

    @iot.get("/stations/{station_id}/latest")
    def latest(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)]):
        st = station_or_404(station_id)
        now = eng.clock.now()
        fresh = now - timedelta(seconds=2 * st.cadenza_s + 2 * eng.clock.factor)
        canali = []
        for s in st.sensors:
            t = s.tipo
            item: dict[str, Any] = {"sensor_id": str(s.id), "sensor_type": s.sensor_type,
                                    "device_uid": s.device_uid, "profondita_cm": s.profondita_cm,
                                    "unit": t.unit, "descrizione": t.descrizione, "measured_at": None,
                                    "value": None, "quality": 0,
                                    "presente": s.ultima_misura_il is not None and s.ultima_misura_il >= fresh}
            if t.tipo_valore is TipoValore.SCALARE:
                last = eng.repo.scalars[s.id].last() if s.id in eng.repo.scalars else None
                if last:
                    item.update(measured_at=_iso_ts(last[0]), value=last[1], quality=last[2])
            elif t.tipo_valore is TipoValore.POSIZIONE:
                last = eng.repo.positions[s.id].last() if s.id in eng.repo.positions else None
                if last:
                    lat, lon, alt, hdop, sats = last[1]
                    item.update(measured_at=_iso_ts(last[0]),
                                value={"lat": lat, "lon": lon, "alt_m": alt, "hdop": hdop, "sats": sats})
            else:
                last = eng.repo.motion[s.id].last() if s.id in eng.repo.motion else None
                if last:
                    ax, ay, az, incl = last[1]
                    item.update(measured_at=_iso_ts(last[0]),
                                value={"ax": ax, "ay": ay, "az": az, "inclinazione_deg": incl})
            canali.append(item)
        inds = eng.repo.indicators.get(st.id) or []
        ultimi: dict[str, Any] = {}
        for ind in inds:
            ultimi[ind.indicatore] = {"valore": ind.valore, "calcolato_il": _iso(ind.calcolato_il),
                                      "dettagli": ind.dettagli}
        return {"station_id": str(st.id), "generato_il": now.isoformat(), "canali": canali, "indicatori": ultimi}

    @iot.get("/stations/{station_id}/sparklines")
    def sparklines(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)],
                   hours: int = Query(24, ge=1, le=168), buckets: int = Query(48, ge=8, le=200)):
        """Estensione per la dashboard (non nel §11): una mini-serie per canale in una sola chiamata.
        GPS → scostamento dalla posizione registrata (m); accelerometro → inclinazione (°)."""
        st = station_or_404(station_id)
        t1 = eng.clock.now().timestamp()
        t0 = t1 - hours * 3600
        width = (t1 - t0) / buckets
        out = []
        for s in st.sensors:
            t = s.tipo
            pts: list[tuple[float, float]] = []
            if t.tipo_valore is TipoValore.SCALARE and s.id in eng.repo.scalars:
                ts_, v, q = eng.repo.scalars[s.id].window(t0, t1)
                pts = [(a, b) for a, b, c in zip(ts_, v, q) if c == 0]
            elif t.tipo_valore is TipoValore.POSIZIONE and s.id in eng.repo.positions:
                pts = [(a, haversine_m(r[0], r[1], st.lat, st.lon)) for a, r in eng.repo.positions[s.id].window(t0, t1)]
            elif t.tipo_valore is TipoValore.MOVIMENTO and s.id in eng.repo.motion:
                pts = [(a, r[3]) for a, r in eng.repo.motion[s.id].window(t0, t1)]
            bins: list[list[float]] = [[] for _ in range(buckets)]
            for a, b in pts:
                bins[min(buckets - 1, int((a - t0) / width))].append(b)
            vals: list[float | None] = []
            for b in bins:
                if not b:
                    vals.append(None)
                elif t.aggregazione == "somma":
                    vals.append(round(sum(b), 3))
                elif t.aggregazione == "circolare":
                    vals.append(round(circular_mean_deg(b), 1))
                else:
                    vals.append(round(sum(b) / len(b), 3))
            out.append({"sensor_id": str(s.id), "v": vals})
        return out

    @iot.get("/lands/{land_id}/data-source", response_model=DataSourceOut)
    def data_source(land_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)]):
        if land_id not in eng.registry.lands:
            raise HTTPException(404, "Terreno non trovato")
        sorgente, st = eng.registry.data_source(land_id)
        return dict(land_id=land_id, sorgente=sorgente.value, stato=st.stato if st else None,
                    station_id=st.id if st else None, ultimo_contatto=_iso(st.ultimo_contatto_il) if st else None)

    @iot.get("/stations/{station_id}/measurements")
    def measurements(
        station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)],
        type: str = Query(..., description="codice del tipo di canale, es. soil_moisture"),
        depth: int | None = Query(None, description="profondità in cm, per i canali del suolo"),
        from_: datetime | None = Query(None, alias="from"),
        to: datetime | None = None,
        agg: Literal["raw", "hour", "day"] = "raw",
    ):
        st = station_or_404(station_id)
        if type not in SENSOR_TYPES:
            raise HTTPException(422, f"Tipo di canale sconosciuto: {type}")
        sensor = st.sensor_by_type(type, depth)
        if sensor is None:
            raise HTTPException(404, "Canale non presente")
        t_to = (to or eng.clock.now()).timestamp()
        t_from = (from_.timestamp() if from_ else t_to - 86400)
        if agg == "raw" and t_to - t_from > 7 * 86400:
            raise HTTPException(422, "La serie grezza è limitata a 7 giorni: usare agg=hour o agg=day")
        t = sensor.tipo
        base = {"station_id": str(st.id), "sensor_type": type, "profondita_cm": sensor.profondita_cm,
                "unit": t.unit, "agg": agg, "aggregazione": t.aggregazione.value}
        if t.tipo_valore is TipoValore.POSIZIONE:
            rows = eng.repo.positions[sensor.id].window(t_from, t_to) if sensor.id in eng.repo.positions else []
            return {**base, "punti": [{"t": _iso_ts(ts), "lat": r[0], "lon": r[1], "alt_m": r[2], "hdop": r[3]}
                                      for ts, r in rows]}
        if t.tipo_valore is TipoValore.MOVIMENTO:
            rows = eng.repo.motion[sensor.id].window(t_from, t_to) if sensor.id in eng.repo.motion else []
            return {**base, "punti": [{"t": _iso_ts(ts), "ax": r[0], "ay": r[1], "az": r[2],
                                       "inclinazione_deg": r[3], "v": r[3]} for ts, r in rows]}
        if agg == "raw":
            series = eng.repo.scalars.get(sensor.id)
            if series is None:
                return {**base, "punti": []}
            ts_, v, q = series.window(t_from, t_to)
            return {**base, "punti": [{"t": _iso_ts(a), "v": b, "q": c} for a, b, c in zip(ts_, v, q)]}
        hourly = eng.repo.hourly.get(sensor.id, {})
        hours = sorted(h for h in hourly if t_from <= h <= t_to)
        if agg == "hour":
            pts = [{"t": _iso_ts(h), "v": (hourly[h].v_sum if t.aggregazione == "somma" else hourly[h].v_avg),
                    "min": hourly[h].v_min, "max": hourly[h].v_max, "n": hourly[h].n} for h in hours]
            return {**base, "punti": pts}
        days: dict[int, list] = {}
        for h in hours:
            days.setdefault(int(h // 86400 * 86400), []).append(hourly[h])
        pts = []
        for d, aggs in sorted(days.items()):
            n = sum(a.n for a in aggs)
            if t.aggregazione == "somma":
                v = sum(a.v_sum or 0 for a in aggs)
            elif t.aggregazione == "circolare":  # media circolare delle medie orarie, pesata su n
                sin_ = sum(math.sin(math.radians(a.v_avg)) * a.n for a in aggs)
                cos_ = sum(math.cos(math.radians(a.v_avg)) * a.n for a in aggs)
                v = math.degrees(math.atan2(sin_, cos_)) % 360
            else:
                v = sum(a.v_avg * a.n for a in aggs) / n
            pts.append({"t": _iso_ts(d), "v": v, "min": min(a.v_min for a in aggs),
                        "max": max(a.v_max for a in aggs), "n": n})
        return {**base, "punti": pts}

    @iot.get("/stations/{station_id}/indicators")
    def indicators(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)],
                   from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                   nome: str | None = None):
        st = station_or_404(station_id)
        t_to = to or eng.clock.now()
        t_from = from_ or t_to - timedelta(hours=24)
        out = [{"calcolato_il": _iso(i.calcolato_il), "indicatore": i.indicatore, "valore": i.valore,
                "dettagli": i.dettagli}
               for i in eng.repo.indicators.get(st.id, [])
               if t_from <= i.calcolato_il <= t_to and (nome is None or i.indicatore == nome)]
        return out

    @iot.get("/stations/{station_id}/alerts", response_model=list[AlertOut])
    def alerts(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)],
               stato: Literal["aperta", "chiusa"] | None = None):
        st = station_or_404(station_id)
        out = [a for a in eng.repo.alerts if a.station_id == st.id
               and (stato is None or (stato == "aperta") == a.aperta)]
        return [alert_out(a) for a in sorted(out, key=lambda a: a.aperta_il, reverse=True)]

    @iot.get("/stations/{station_id}/status-log")
    def status_log(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(get_ruolo)]):
        st = station_or_404(station_id)
        return [{"da": t.da_stato, "a": t.a_stato, "motivo": t.motivo, "avvenuto_il": _iso(t.avvenuto_il)}
                for t in reversed(eng.repo.status_log) if t.station_id == st.id][:50]

    # --- simulatore -------------------------------------------------------------------------------
    sim = APIRouter(prefix="/api/sim", tags=["simulatore"])

    @sim.get("/status")
    def status():
        return eng.status()

    @sim.patch("/config")
    def config(body: SimConfigIn, _: Annotated[Ruolo, Depends(require_admin)]):
        if body.fattore is not None:
            eng.clock.set_factor(body.fattore)
        if body.in_pausa is not None:
            eng.clock.set_paused(body.in_pausa)
        if body.scenario_meteo is not None:
            if body.scenario_meteo not in Scenario.ALL:
                raise HTTPException(422, f"Scenario sconosciuto: {body.scenario_meteo}")
            eng.set_scenario(body.scenario_meteo)
        if body.guasti:
            g = eng.fleet.global_faults
            for k, v in body.guasti.items():
                if not hasattr(g, k):
                    raise HTTPException(422, f"Guasto sconosciuto: {k}")
                if not math.isfinite(v) or v < 0 or (k.endswith("_pct") and v > 100):
                    raise HTTPException(422, f"Valore non valido per {k}")
                setattr(g, k, int(v) if k == "ritardo_s" else float(v))
        return config_out()

    def config_out() -> dict[str, Any]:
        g = eng.fleet.global_faults
        return {"fattore": eng.clock.factor, "in_pausa": eng.clock.paused, "scenario_meteo": eng.weather.scenario,
                "scenari": list(Scenario.ALL),
                "guasti": {k: getattr(g, k) for k in g.__slots__}}

    @sim.get("/config")
    def get_config():
        return config_out()

    def faults_out(sid: uuid.UUID) -> dict[str, Any]:
        f = eng.fleet.faults.get(sid)
        if f is None:
            return StationFaultsIn().model_dump()
        return {"offline": f.offline, "canali_silenziati": sorted(f.canali_silenziati),
                "canali_bloccati": sorted(f.canali_bloccati), "picchi_pct": f.picchi_pct,
                "spostata_m": f.spostata_m, "inclinata_deg": f.inclinata_deg}

    @sim.get("/stations/{station_id}/faults")
    def get_faults(station_id: uuid.UUID):
        station_or_404(station_id)
        return faults_out(station_id)

    @sim.put("/stations/{station_id}/faults")
    def put_faults(station_id: uuid.UUID, body: StationFaultsIn, _: Annotated[Ruolo, Depends(require_admin)]):
        st = station_or_404(station_id)
        uids = {s.device_uid for s in st.sensors}
        bad = (set(body.canali_silenziati) | set(body.canali_bloccati)) - uids
        if bad:
            raise HTTPException(422, f"Canali non della stazione: {', '.join(sorted(bad))}")
        f = eng.fleet.faults.setdefault(station_id, StationFaults())
        f.offline = body.offline
        f.canali_silenziati = set(body.canali_silenziati)
        f.canali_bloccati = set(body.canali_bloccati)
        f.picchi_pct = body.picchi_pct
        f.spostata_m = body.spostata_m
        f.inclinata_deg = body.inclinata_deg
        return faults_out(station_id)

    @sim.post("/stations/{station_id}/urto")
    def shock(station_id: uuid.UUID, _: Annotated[Ruolo, Depends(require_admin)]):
        station_or_404(station_id)
        eng.fleet.faults.setdefault(station_id, StationFaults()).urto = True
        return {"ok": True}

    @sim.post("/stations/bulk", status_code=201)
    def bulk(_: Annotated[Ruolo, Depends(require_admin)], n: int = Query(10, ge=1, le=200)):
        created = eng.create_test_stations(n)
        return {"create": len(created)}

    @sim.post("/ingestion/riavvio")
    def restart(_: Annotated[Ruolo, Depends(require_admin)], downtime_s: float = Query(5, ge=0, le=120)):
        return {"riconsegnati": eng.restart_ingestion(downtime_s)}

    @sim.post("/db/indisponibile")
    def db_down(_: Annotated[Ruolo, Depends(require_admin)], durata_s: float = Query(60, ge=1, le=600)):
        eng.make_db_unavailable(durata_s)
        return {"ok": True}

    @sim.get("/messages")
    def messages(limit: int = Query(200, ge=1, le=800)):
        return eng.msglog.recent(limit)

    @sim.get("/events")
    def events(limit: int = Query(100, ge=1, le=400)):
        return eng.events.recent(limit)

    @sim.get("/stream")
    async def stream(request: Request):
        """Server-Sent Events: stato ogni secondo, nuovi messaggi ed eventi di dominio."""

        async def gen():
            msg_cur = eng.msglog.cursor  # lo storico recente si legge da /messages
            ev_cur = eng.events.cursor
            while True:
                if await request.is_disconnected():
                    break
                msg_cur, msgs = eng.msglog.since(msg_cur, limit=60)
                ev_cur, evs = eng.events.since(ev_cur, limit=50)
                data = {"status": eng.status(), "messaggi": msgs, "eventi": evs}
                yield f"event: tick\ndata: {json.dumps(data, default=str)}\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/health", tags=["servizio"])
    def health():
        """Sonda per l'healthcheck dei container: risponde appena il motore è inizializzato."""
        return {"ok": True, "sim_now": eng.clock.now().isoformat()}

    app.include_router(lands)
    app.include_router(iot)
    app.include_router(sim)

    # --- dashboard compilata (opzionale) ------------------------------------------------------------
    if static_dir and Path(static_dir).is_dir():
        root = Path(static_dir)
        index = root / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (root / path).resolve()
            if path and f.is_file() and root.resolve() in f.parents:
                return FileResponse(f)
            if path.startswith("api/"):
                raise HTTPException(404, "Not Found")
            return FileResponse(index)

    return app
