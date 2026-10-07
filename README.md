# Agrivalor IoT · simulatore del layer di ingestion

Simulatore del Proof of Concept descritto in `SPECIFICHE_IOT_INGESTION.md` v0.2: stazioni a 16 canali,
traffico MQTT con guasti iniettabili, ingestion con validazione e deduplicazione, verifica di completezza,
selezione della sorgente `IOT`/`SATELLITE`, modelli near-real-time e dashboard Angular + Leaflet.

Due modi d'uso:

| Modalità | Comando | A cosa serve |
| --- | --- | --- |
| **Integrata** | `iotsim serve` | Tutto in un processo: broker MQTT emulato, ingestion, registro, task periodici, API `/iot` e dashboard. Per sviluppare la UI, provare i criteri di accettazione e fare demo senza lo stack di Agrivalor. |
| **Traffico** | `iotsim traffico` | Il simulatore del §13.3: rileva le stazioni via `GET /iot/stations` e pubblica su un **Mosquitto reale**. È il componente `iot-simulator` da usare contro lo stack vero. |

## Avvio rapido

```bash
# 1. backend (Python ≥ 3.11)
cd simulator
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
iotsim serve --fattore 20          # http://127.0.0.1:8000/api/docs

# 2. dashboard (Node 22) — in un secondo terminale
cd dashboard
npm install
npm start                          # http://localhost:4200
```

Oppure un'unica immagine con la dashboard già compilata:

```bash
docker compose up --build          # http://localhost:8000
docker compose --profile mqtt up   # + Mosquitto e simulatore "traffico"
```

All'avvio vengono registrate 3 stazioni demo con 24 ore di storico (una con stress idrico già aperto);
3 terreni restano liberi per registrarne di nuove con un clic sulla mappa.

## Cosa c'è

```
simulator/                 Python 3.11+, FastAPI, Pydantic v2
  src/iotsim/
    catalog.py             i 16 canali (seed di iot_sensor_types, §7.2)
    messages.py            contratto MQTT: unione discriminata su "type" (§8.5)
    broker.py              broker emulato: credenziali per gateway, ACL, QoS 1, sessione persistente
    station_sim.py         segnali realistici e guasti per stazione (§13.3)
    weather.py             meteo regionale condiviso (nuvole, pioggia, pressione, vento, sole)
    fleet.py               rilevamento delle stazioni e pubblicazione (integrata o MQTT reale)
    ingestion.py           validazione, normalizzazione, dedup, batch 1 s / 500 msg, PUBACK dopo il commit
    storage.py             tabelle iot_* in memoria, partizioni mensili emulate, aggregati orari
    registry.py            registrazione (ST_Contains, D8), dismissione logica, sorgente dati
    completeness.py        stati della stazione (§8.1)
    nrt.py                 stress idrico (Saxton–Rawls + FAO-56), gelata/caldo, integrità, sensori sospetti
    engine.py              orologio simulato e task periodici (al posto di Celery beat)
    api.py                 router /iot (§11) + /lands/my-lands + controlli /sim
    traffic.py             modalità traffico verso Mosquitto
  tests/                   23 test, inclusi i criteri AC2, AC4, AC7, AC8, AC9 e lo scenario S3
dashboard/                 Angular 21, Leaflet 1.9, Chart.js 4, SweetAlert2
  src/app/features/iot/    struttura del §10.1: pagina, modale, menu laterale, dettaglio, servizio API
  src/app/features/simulator/   console del simulatore (tempo, meteo, carico, guasti, KPI)
docs/ANALISI_SPECIFICHE.md copertura dei requisiti, misure, scostamenti da riportare nel §15
```

## Dashboard

- **Stazioni** (`/stazioni`, nella SPA sarà `/app/iot`): mappa con i terreni colorati per sorgente
  (salvia = `IOT`, tratteggio indaco = `SATELLITE`) e i marker delle stazioni colorati per stato.
  Clic su un terreno → modale *Registra una stazione*; gli errori 422/409 restano nella modale.
  Menu laterale con ricerca, composizione per stato, selezione multipla ed eliminazione con conferma.
  Il dettaglio mostra ultimo valore e mini-serie di ogni canale, acqua disponibile (RAW) con le soglie
  FAO-56, allerte aperte, grafico 24 h / 7 g, transizioni di stato e i guasti simulati della stazione.
- **Simulatore** (`/simulatore`): compressione temporale, scenario meteo, stazioni di prova in blocco,
  guasti sul traffico, riavvio dell'ingestion e database giù per 60 s; KPI del §14 (throughput,
  latenza p50/p95/p99, salvati, scartati per causa, perdite via `seq`, coda del broker, partizioni).
- In alto l'ora simulata e il selettore di ruolo: con `agricoltore` registrazione, eliminazione e
  controlli del simulatore spariscono o si disattivano.

### Palette

| Colore | Uso |
| --- | --- |
| `#84A98C` salvia | stato `ATTIVO_COMPLETO`, sorgente `IOT`, esiti positivi |
| `#59C3C3` teal | dati in arrivo, serie e grafici, stato `REGISTRATA` |
| `#52489C` indaco | azioni, selezione, focus; sorgente `SATELLITE` |
| `#CAD2C5` grigio salvia | filetti, stato `OFFLINE` |
| `#EBEBEB` grigio chiaro | fondo dell'applicazione |

La palette non ha toni caldi: `DEGRADATA`/attenzione e critica usano un ocra (`#C08A2E`) e un mattone
(`#B4513C`) desaturati, gli unici colori fuori palette. Tipografia IBM Plex Sans / Mono (numeri tabulari),
filetti da 1 px invece di ombre, raggi di 3 px. I controlli che esistono solo nel simulatore sono marcati
da un'etichetta tratteggiata **SIM**, per non confonderli con il prodotto.

## Modalità traffico (simulatore del PoC)

```bash
iotsim traffico \
  --api https://agrivalor.example/api --utente simulatore --password '…' \
  --mqtt-host broker.example --mqtt-port 8883 --tls --ca ca.crt \
  --mqtt-utente iot-simulator --mqtt-password '…' \
  --fattore 20 --durata 24h --seme 42 \
  --duplicati 2 --fuori-intervallo 5 --fuori-ordine 1      # scenario S4
```

- Token dal login proxy `POST {api}/keycloak/login` (`{username, password}` → `access_token`), oppure `--token`.
- `--crea-stazioni N` registra N stazioni via API in punti casuali dei terreni liberi dell'utente.
- Un messaggio per canale, topic `agrivalor/{gateway_id}/{device_uid}/telemetry`, QoS 1, payload del §8.5.

## Test

```bash
cd simulator && pytest            # 23 test, ~1 s: l'orologio manuale avanza ore simulate senza attese
cd dashboard && npm run build
```
