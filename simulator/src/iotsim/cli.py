"""Riga di comando.

``iotsim serve``     simulatore integrato (broker emulato + ingestion + API + dashboard).
``iotsim traffico``  simulatore del PoC (§13.3): rileva le stazioni via API e pubblica su un
                     broker MQTT reale (Mosquitto), con guasti iniettabili.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time


def _duration(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"durata non valida: {s} (es. 90s, 30m, 24h, 2d)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_app
    from .engine import Engine

    engine = Engine(seed=a.seme, factor=a.fattore, demo=not a.vuoto, backfill_hours=a.storico_h)
    if a.crea_stazioni:
        engine.create_test_stations(a.crea_stazioni)
    app = create_app(engine, static_dir=a.static)
    # timeout breve: lo stream SSE della console resta aperto e altrimenti bloccherebbe l'arresto
    config = uvicorn.Config(app, host=a.host, port=a.port, log_level="info", timeout_graceful_shutdown=2)
    # dopo Config, che configura i logger: le sonde dell'healthcheck non riempiono il log
    logging.getLogger("uvicorn.access").addFilter(_DropHealthcheck())
    uvicorn.Server(config).run()


class _DropHealthcheck(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/health" not in record.getMessage()


def cmd_traffico(a: argparse.Namespace) -> int:
    from .traffic import TrafficConfig, run_traffic

    cfg = TrafficConfig(
        api_url=a.api.rstrip("/"), token=a.token, login_url=a.login_url, username=a.utente, password=a.password,
        mqtt_host=a.mqtt_host, mqtt_port=a.mqtt_port, mqtt_user=a.mqtt_utente, mqtt_password=a.mqtt_password,
        tls=a.tls, ca_file=a.ca, fattore=a.fattore, durata_sim_s=a.durata, seme=a.seme,
        crea_stazioni=a.crea_stazioni, scoperta_s=a.scoperta_s,
        duplicati_pct=a.duplicati, fuori_intervallo_pct=a.fuori_intervallo, fuori_ordine_pct=a.fuori_ordine,
        ritardo_pct=a.ritardi, malformati_pct=a.malformati,
    )
    return run_traffic(cfg)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="iotsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="simulatore integrato con API e dashboard")
    s.add_argument("--host", default=os.getenv("IOTSIM_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.getenv("IOTSIM_PORT", "8000")))
    s.add_argument("--fattore", type=float, default=float(os.getenv("IOTSIM_FATTORE", "20")),
                   help="compressione temporale (default ×20)")
    s.add_argument("--seme", type=int, default=int(os.getenv("IOTSIM_SEME", "42")))
    s.add_argument("--storico-h", type=int, default=int(os.getenv("IOTSIM_STORICO_H", "24")),
                   help="ore di storico generate all'avvio")
    s.add_argument("--vuoto", action="store_true", default=os.getenv("IOTSIM_VUOTO", "") in ("1", "true"),
                   help="nessuna stazione demo")
    s.add_argument("--crea-stazioni", type=int, default=int(os.getenv("IOTSIM_CREA_STAZIONI", "0")), metavar="N",
                   help="registra N stazioni di prova")
    s.add_argument("--static", default=os.getenv("IOTSIM_STATIC_DIR"), help="cartella della dashboard compilata")
    s.set_defaults(func=cmd_serve)

    t = sub.add_parser("traffico", help="pubblica su un broker MQTT reale (simulatore del PoC, §13.3)")
    t.add_argument("--api", default=os.getenv("IOTSIM_API", "http://127.0.0.1:8000/api"),
                   help="base URL delle API (con /iot/stations)")
    t.add_argument("--token", default=os.getenv("IOTSIM_TOKEN"), help="JWT già ottenuto")
    t.add_argument("--login-url", default=os.getenv("IOTSIM_LOGIN_URL"),
                   help="login proxy Keycloak (default: {api}/keycloak/login)")
    t.add_argument("--utente", default=os.getenv("IOTSIM_UTENTE"))
    t.add_argument("--password", default=os.getenv("IOTSIM_PASSWORD"))
    t.add_argument("--mqtt-host", default=os.getenv("IOTSIM_MQTT_HOST", "127.0.0.1"))
    t.add_argument("--mqtt-port", type=int, default=int(os.getenv("IOTSIM_MQTT_PORT", "1883")))
    t.add_argument("--mqtt-utente", default=os.getenv("IOTSIM_MQTT_UTENTE"))
    t.add_argument("--mqtt-password", default=os.getenv("IOTSIM_MQTT_PASSWORD"))
    t.add_argument("--tls", action="store_true", help="MQTT su TLS (porta 8883)")
    t.add_argument("--ca", default=os.getenv("IOTSIM_MQTT_CA"), help="certificato CA del broker")
    t.add_argument("--fattore", type=float, default=float(os.getenv("IOTSIM_FATTORE", "20")))
    t.add_argument("--durata", type=_duration, default=None, help="durata simulata, es. 24h (default: infinita)")
    t.add_argument("--seme", type=int, default=int(os.getenv("IOTSIM_SEME", "42")))
    t.add_argument("--crea-stazioni", type=int, default=0, metavar="N",
                   help="registra N stazioni via API in punti casuali dei terreni liberi")
    t.add_argument("--scoperta-s", type=float, default=60, help="periodo di rilevamento, secondi simulati")
    t.add_argument("--duplicati", type=float, default=0, metavar="PCT")
    t.add_argument("--fuori-intervallo", type=float, default=0, metavar="PCT")
    t.add_argument("--fuori-ordine", type=float, default=0, metavar="PCT")
    t.add_argument("--ritardi", type=float, default=0, metavar="PCT")
    t.add_argument("--malformati", type=float, default=0, metavar="PCT")
    t.set_defaults(func=cmd_traffico)

    a = p.parse_args(argv)
    t0 = time.time()
    rc = a.func(a)
    logging.getLogger("iotsim").info("terminato in %.1f s", time.time() - t0)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
