import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  Alert,
  LandCollection,
  Latest,
  Series,
  Sparkline,
  Station,
  StationDetail,
  StationFaults,
  StatusTransition,
} from '../models/iot.models';

/** Chiamate /api/iot (§11) più i due endpoint del simulatore usati dal dettaglio. */
@Injectable({ providedIn: 'root' })
export class IotApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/iot';

  myLands(): Observable<LandCollection> {
    return this.http.get<LandCollection>('/api/lands/my-lands');
  }

  stations(): Observable<Station[]> {
    return this.http.get<Station[]>(`${this.base}/stations`);
  }

  station(id: string): Observable<StationDetail> {
    return this.http.get<StationDetail>(`${this.base}/stations/${id}`);
  }

  register(body: { nome: string; lat: number; lon: number }): Observable<StationDetail> {
    return this.http.post<StationDetail>(`${this.base}/stations`, body);
  }

  update(id: string, body: { nome?: string; cadenza_s?: number }): Observable<Station> {
    return this.http.patch<Station>(`${this.base}/stations/${id}`, body);
  }

  remove(ids: string[]): Observable<{ dismesse: string[] }> {
    return ids.length === 1
      ? this.http.delete<{ dismesse: string[] }>(`${this.base}/stations/${ids[0]}`)
      : this.http.delete<{ dismesse: string[] }>(`${this.base}/stations/batch`, { body: { ids } });
  }

  latest(id: string): Observable<Latest> {
    return this.http.get<Latest>(`${this.base}/stations/${id}/latest`);
  }

  sparklines(id: string, hours = 24): Observable<Sparkline[]> {
    return this.http.get<Sparkline[]>(`${this.base}/stations/${id}/sparklines`, {
      params: new HttpParams().set('hours', hours),
    });
  }

  measurements(
    id: string,
    type: string,
    depth: number | null,
    opts: { from?: string; to?: string; agg?: 'raw' | 'hour' | 'day' } = {},
  ): Observable<Series> {
    let params = new HttpParams().set('type', type).set('agg', opts.agg ?? 'raw');
    if (depth !== null) params = params.set('depth', depth);
    if (opts.from) params = params.set('from', opts.from);
    if (opts.to) params = params.set('to', opts.to);
    return this.http.get<Series>(`${this.base}/stations/${id}/measurements`, { params });
  }

  alerts(id: string, stato?: 'aperta' | 'chiusa'): Observable<Alert[]> {
    const params = stato ? new HttpParams().set('stato', stato) : undefined;
    return this.http.get<Alert[]>(`${this.base}/stations/${id}/alerts`, { params });
  }

  statusLog(id: string): Observable<StatusTransition[]> {
    return this.http.get<StatusTransition[]>(`${this.base}/stations/${id}/status-log`);
  }

  // --- simulatore -----------------------------------------------------------------
  faults(id: string): Observable<StationFaults> {
    return this.http.get<StationFaults>(`/api/sim/stations/${id}/faults`);
  }

  setFaults(id: string, f: StationFaults): Observable<StationFaults> {
    return this.http.put<StationFaults>(`/api/sim/stations/${id}/faults`, f);
  }

  shock(id: string): Observable<unknown> {
    return this.http.post(`/api/sim/stations/${id}/urto`, {});
  }
}
