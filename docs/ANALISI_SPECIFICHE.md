# Analisi delle specifiche e copertura del simulatore

Riferimento: `SPECIFICHE_IOT_INGESTION.md` v0.2 del 7 ottobre 2026.

## 1. Che cosa chiede la specifica al simulatore

Il §13.3 definisce `iot-simulator` come generatore di traffico: rileva le stazioni registrate
(`GET /iot/stations` ogni 60 s), pubblica i 16 canali con segnali realistici, accetta parametri
(cadenza, compressione temporale, durata, seme) e inietta guasti (duplicati, fuori intervallo, fuori ordine,
ritardi, canali silenziati, stazione offline, spostata, inclinata). Serve a verificare i KPI del §14 e i
criteri AC1–AC9.

Il repository copre questo componente in due modi:

- **modalità `traffico`**: il simulatore vero e proprio, verso API e Mosquitto reali (verificata con
  Mosquitto 2 locale: 192 messaggi conformi al §8.5 ricevuti da un sottoscrittore QoS 1);
- **modalità integrata**: un gemello in-process del resto del sottosistema (broker, ingestion, registro,
  completezza, selettore, modelli NRT, API `/iot`, dashboard) per sviluppare e dimostrare i flussi
  senza lo stack di Agrivalor. Le parti "infrastrutturali" sono emulate, la logica di dominio no.

| Componente del §5.2 | Qui | Note |
| --- | --- | --- |
| `iot-broker` Mosquitto | emulato (`broker.py`) / reale in `traffico` | credenziali per gateway, ACL, QoS 1, sessione persistente, `max_queued_messages` |
| `iot-ingestion` | `ingestion.py` | stessa pipeline del §8.3; scrittura in memoria anziché `psycopg` |
| PostgreSQL / tabelle `iot_*` | `storage.py` | chiavi primarie e `ON CONFLICT DO NOTHING` emulati, partizioni mensili contate |
| Redis + Celery beat/worker | `engine.py` | task periodici in tempo simulato, debounce equivalente a `SET NX EX` |
| Router `/iot` | `api.py` | tutte le rotte del §11, più due estensioni per la dashboard |
| Dashboard Angular + Leaflet | `dashboard/` | struttura del §10.1, portabile nella SPA |
| Keycloak | header `X-Ruolo` | ruoli `admin` / `agricoltore`, utente demo unico |

## 2. Requisiti funzionali

| ID | Stato | Dove / come |
| --- | --- | --- |
| RF-IOT-01 registrazione da mappa | ✓ | modale, `POST /iot/stations`, terreno con punto-nel-poligono, il più piccolo se sovrapposti |
| RF-IOT-02 gateway, credenziali, 16 canali, 180 s | ✓ | `registry.register`, `device_uid` nel formato del §7.3 |
| RF-IOT-03 rifiuto 422/409 nella modale | ✓ | test `test_registration_rules`, `test_register_list_detail_delete`, e2e in browser |
| RF-IOT-04 menu laterale, `flyTo`, dettaglio | ✓ | `stations-sidebar`, `station-map.focus` |
| RF-IOT-05 eliminazione singola e multipla con conferma | ✓ | SweetAlert2, `DELETE /iot/stations/{id}` e `/batch` |
| RF-IOT-06 validazione, normalizzazione, dedup | ✓ | 10 cause di scarto contate; °F, kPa, klx, km/h, µS/cm, m³/m³ normalizzati |
| RF-IOT-07 tre tabelle di destinazione | ✓ | `iot_measurements`, `iot_station_positions`, `iot_station_motion` |
| RF-IOT-08 completezza periodica | ✓ | ogni 180 s simulati, transizioni nel log |
| RF-IOT-09 sorgente con fallback | ✓ | `GET /iot/lands/{id}/data-source`, colore del terreno sulla mappa |
| RF-IOT-10 indicatori NRT e allerte | ✓ | solo per `ATTIVO_COMPLETO` |
| RF-IOT-11 API con ownership | ✓ | ownership banale (utente demo unico) |
| RF-IOT-12 dettaglio con ultimi valori, stress, allerte | ✓ | `station-detail-panel` |
| RF-IOT-13 aggregati orari media/somma/circolare | ✓ | `test_hourly_aggregates_use_the_right_function` |
| RF-IOT-14 stazione spostata, inclinata, urto | ✓ | `test_integrity_moved_tilted_and_shock` |
| RF-IOT-15 partizioni future e retention | ✓ emulato | 2 mesi in anticipo, retention di 8 giorni in memoria |
| RF-IOT-16 misure sospette | ✓ | picco \|z\| > 4 su 2 h, costante > 6 h |
| RF-IOT-17 grafici 24 h | ✓ | Chart.js, anche 7 giorni su aggregati orari |
| RF-IOT-18 contesto per l'assistente AI | — | fuori dal simulatore |

## 3. Criteri di accettazione

| AC | Esito | Verifica |
| --- | --- | --- |
| AC1 24 h senza perdite, p95 entro obiettivo | ✓ integrata | perdite 0 via `seq`; p95 ≈ 1 s (vedi §4). Da ripetere sullo stack reale |
| AC2 canale silenziato → `SATELLITE` entro 2 intervalli e ritorno | ✓ | `test_ac2_…`, e2e in browser |
| AC3 duplicati e fuori intervallo contati | ✓ | `test_validation_causes_and_dedup`, `test_fault_injection_counts` |
| AC4 stress idrico aperto in siccità, chiuso dopo la pioggia | ✓ | `test_ac4_…` (terreno sabbioso, scenario Pioggia) |
| AC5 nessuna regressione | n/a | il simulatore non tocca lo stack esistente |
| AC6 avvio documentato | ✓ | README, `docker compose up` (Compose non verificato in questo ambiente: niente daemon Docker) |
| AC7 dati entro 60 s, completa entro 2 intervalli | ✓ | `test_ac7_…`: prima misura al rilevamento, completa al primo controllo |
| AC8 clic fuori dai terreni → errore nella modale | ✓ | test API + e2e in browser |
| AC9 eliminazione: credenziali revocate, sorgente `SATELLITE` | ✓ | `test_ac9_…`: 16 PUBLISH rifiutate dal broker prima del rilevamento successivo |

## 4. Misure (modalità integrata)

Scenario S2 ridotto: 100 stazioni × 16 canali, cadenza 180 s, compressione ×20 (atteso ≈ 178 msg/s),
misure su finestre di 10 s dopo un minuto di regime.

| msg/s | p50 | p95 | p99 | perdite | scarti | RAM |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 160–189 | 0,44–0,47 s | 0,98 s | 0,99 s | 0 | 0 | 64 MB |

La latenza è dominata dal flush ogni 1 s del §8.3 e lo storage è in memoria: i numeri dicono che la
pipeline regge il carico, non sostituiscono le misure su PostgreSQL richieste dal §14.

## 5. Scostamenti da riportare (§15, punto 1)

1. **Persistenza in memoria** con le stesse chiavi e tabelle del DDL; partizioni emulate come contatori;
   retention delle misure grezze di 8 giorni invece di 12 mesi.
2. **Broker emulato** nella modalità integrata. Usa credenziali per gateway anche per il simulatore: è più
   fedele alla produzione della semplificazione S7 e rende osservabile l'AC9. In modalità `traffico`
   vale invece S7 (account unico).
3. **Autenticazione**: header `X-Ruolo` al posto del JWT di Keycloak; un solo utente demo.
4. **Celery** sostituito da uno scheduler in-process in tempo simulato.
5. **Tolleranza di pipeline nella completezza**: la soglia "entro 2 intervalli" è allungata di 2 s reali
   espressi in tempo simulato (40 s a ×20, 12 min a ×360), altrimenti a compressione alta il flush da 1 s
   farebbe sembrare mancanti canali presenti.
6. **Flag di qualità per tipo** (`controllo_picchi`, `soglia_picco`, `controllo_costante`) non presenti nel
   DDL: senza, pioggia e radiazione notturna (a 0 per ore) e l'umidità del suolo dopo un temporale darebbero
   falsi sospetti. Proposta: aggiungerli come colonne di `iot_sensor_types`.
7. **Isteresi** sulle chiusure delle allerte: stress idrico chiuso con RAW ≥ 0,52, spostamento sotto 40 m,
   gelata sopra 1 °C, caldo sotto 33 °C; `urto` si chiude da solo dopo un'ora senza nuovi urti.
8. **Pesi della zona radicale** 0,6 (10–20 cm) e 0,4 (30–40 cm), configurabili in `nrt.Thresholds`.
9. **GPS "coerente con il terreno"** interpretato come entro 5 km dalla posizione registrata: oltre è un fix
   errato e si scarta; tra 50 m e 5 km è una stazione spostata e apre l'allerta.
10. **Estensioni di API** per la dashboard: `GET /iot/stations/{id}/sparklines`, `GET /iot/stations/{id}/status-log`,
    `GET /lands/my-lands` con la sorgente di ogni terreno; controlli del simulatore sotto `/api/sim`.
11. **Fase delle stazioni**: la prima misura parte al rilevamento (AC7), poi ogni stazione prende una fase
    casuale, così 100 stazioni registrate insieme non trasmettono nello stesso istante.
12. **Angular 21** in questo repository; i componenti di `features/iot` usano solo API presenti da Angular 17.3
    (standalone, signals, `input()`/`output()`, control flow) e si possono spostare nella SPA.

## 6. Punti della specifica da chiarire

- **`ATTIVO_COMPLETO → OFFLINE` non si verifica mai direttamente**: con le regole del §8.1 una stazione che
  tace manca tutti i canali dopo 2 intervalli, quindi passa da `DEGRADATA` e solo dopo 10 intervalli a `OFFLINE`.
  Va bene (la sorgente torna `SATELLITE` subito), ma il diagramma degli stati andrebbe allineato.
- **`REGISTRATA` senza uscita** se la stazione non trasmette mai: manca una transizione `REGISTRATA → OFFLINE`
  (o un timeout) per le installazioni fallite.
- **RNF-IOT-06 "entro 2 intervalli"**: da quale istante? Con controllo ogni 180 s e soglia di 2 intervalli il
  ritardo massimo è 2 intervalli dal primo campione mancante, 3 dall'ultimo ricevuto.
- **Severità** di `stazione_inclinata` e `urto` non indicate (qui: attenzione).
- **AC9 e S7**: con un account MQTT unico per il simulatore la revoca delle credenziali non produce rifiuti dal
  broker; il criterio si osserva solo con credenziali per stazione o fermando il traffico al rilevamento.
- **Dati tardivi e aggregati**: l'aggregazione oraria ricalcola le ultime 2 ore; un dato tardivo fino a 24 h
  andrebbe propagato agli aggregati con un ricalcolo mirato.
