import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export interface GlobalFaults {
  duplicati_pct: number;
  fuori_intervallo_pct: number;
  fuori_ordine_pct: number;
  ritardo_pct: number;
  ritardo_s: number;
  malformati_pct: number;
  unita_alternative_pct: number;
}

export interface SimConfig {
  fattore: number;
  in_pausa: boolean;
  scenario_meteo: string;
  scenari: string[];
  guasti: GlobalFaults;
}

@Injectable({ providedIn: 'root' })
export class SimApiService {
  private readonly http = inject(HttpClient);

  config(): Observable<SimConfig> {
    return this.http.get<SimConfig>('/api/sim/config');
  }

  patch(
    body: Partial<{
      fattore: number;
      in_pausa: boolean;
      scenario_meteo: string;
      guasti: Partial<GlobalFaults>;
    }>,
  ) {
    return this.http.patch<SimConfig>('/api/sim/config', body);
  }

  bulk(n: number): Observable<{ create: number }> {
    return this.http.post<{ create: number }>(
      '/api/sim/stations/bulk',
      {},
      { params: new HttpParams().set('n', n) },
    );
  }

  restartIngestion(downtime = 5): Observable<{ riconsegnati: number }> {
    return this.http.post<{ riconsegnati: number }>(
      '/api/sim/ingestion/riavvio',
      {},
      {
        params: new HttpParams().set('downtime_s', downtime),
      },
    );
  }

  dbDown(seconds = 60): Observable<unknown> {
    return this.http.post(
      '/api/sim/db/indisponibile',
      {},
      { params: new HttpParams().set('durata_s', seconds) },
    );
  }
}
