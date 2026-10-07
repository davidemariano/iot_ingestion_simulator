# Dashboard IoT

Angular 21 (componenti standalone, signals, nuovo control flow) + Leaflet 1.9 + Chart.js 4 + SweetAlert2.
Le API usate dai componenti in `src/app/features/iot/` esistono già in Angular 17.3, la versione della SPA di
Agrivalor: la cartella è pensata per essere spostata in `frontend/src/app/features/iot/` (§10.1).

```bash
npm install
npm start          # http://localhost:4200, con proxy /api → http://127.0.0.1:8000
npm run build      # dist/dashboard/browser, servita dal simulatore con --static
```

Il server di sviluppo presuppone il simulatore avviato (`iotsim serve`, vedi il README principale).
