import { HttpClient } from '@angular/common/http';
import { Injectable, NgZone, OnDestroy, computed, inject, signal } from '@angular/core';
import { Subject } from 'rxjs';

export interface SimStatus {
  sim_now: string;
  real_now: string;
  fattore: number;
  in_pausa: boolean;
  scenario_meteo: string;
  seme: number;
  uptime_s: number;
  stazioni: { totale: number; per_stato: Record<string, number>; canali: number };
  throughput: { msg_s: number; nominale_msg_s: number };
  latenza_s: {
    p50: number | null;
    p95: number | null;
    p99: number | null;
    max: number | null;
    campioni: number;
  };
  contatori: {
    pubblicati: number;
    ricevuti: number;
    salvati: Record<string, number>;
    scartati: Record<string, number>;
    rifiutati_broker: Record<string, number>;
    normalizzati: number;
    sospetti: number;
    perdite: number;
    riconsegnati: number;
    batch: number;
    ultimo_batch: number;
    errori_db: number;
  };
  broker: { in_coda: number; in_volo: number; ritardati: number };
  servizi: { database: boolean; ingestion: boolean };
  partizioni: Record<string, number>;
  istogramma_latenza: { fascia: string; n: number }[];
}

export interface MessageLogEntry {
  esito: 'salvato' | 'sospetto' | 'scartato' | 'rifiutato';
  causa: string | null;
  topic: string;
  tipo: string | null;
  stazione: string | null;
  valore: string;
  ts: string | null;
  latenza_ms: number | null;
  dup: boolean;
}

export interface DomainEvent {
  tipo: 'registrazione' | 'stato' | 'allerta' | 'simulatore' | 'partizione';
  station_id?: string;
  stazione?: string;
  messaggio: string;
  sim_ts: string;
  da?: string;
  a?: string;
  azione?: string;
  allerta?: string;
  severita?: string;
}

interface Tick {
  status: SimStatus;
  messaggi: MessageLogEntry[];
  eventi: DomainEvent[];
}

/**
 * Stream SSE del simulatore (/api/sim/stream): stato ogni secondo, messaggi MQTT recenti,
 * eventi di dominio. L'ora simulata viene interpolata localmente per scorrere in modo fluido.
 */
@Injectable({ providedIn: 'root' })
export class SimStreamService implements OnDestroy {
  private readonly zone = inject(NgZone);
  private readonly http = inject(HttpClient);
  private source?: EventSource;
  private retry?: ReturnType<typeof setTimeout>;
  private clockTimer?: ReturnType<typeof setInterval>;
  private anchorSim = 0;
  private anchorPerf = 0;

  readonly status = signal<SimStatus | null>(null);
  readonly connected = signal(false);
  readonly messages = signal<MessageLogEntry[]>([]);
  readonly events = signal<DomainEvent[]>([]);
  readonly throughputHistory = signal<number[]>([]);
  /** epoch ms dell'ora simulata, aggiornato 4 volte al secondo */
  readonly simNow = signal<number>(Date.now());
  readonly domainEvents$ = new Subject<DomainEvent[]>();

  readonly factor = computed(() => this.status()?.fattore ?? 1);
  readonly paused = computed(() => this.status()?.in_pausa ?? false);

  constructor() {
    // lo stream porta solo le novità: lo storico recente si carica una volta
    this.http
      .get<DomainEvent[]>('/api/sim/events')
      .subscribe((e) => this.events.update((cur) => [...e, ...cur].slice(-200)));
    this.http
      .get<MessageLogEntry[]>('/api/sim/messages', { params: { limit: 120 } })
      .subscribe((m) => this.messages.update((cur) => [...m, ...cur].slice(-400)));
    this.connect();
    this.clockTimer = setInterval(() => {
      const st = this.status();
      if (!st || !this.anchorPerf) return;
      const elapsed = st.in_pausa ? 0 : (performance.now() - this.anchorPerf) * st.fattore;
      this.simNow.set(this.anchorSim + elapsed);
    }, 250);
  }

  private connect(): void {
    this.source?.close();
    const es = new EventSource('/api/sim/stream');
    this.source = es;
    es.addEventListener('tick', (ev) => {
      const tick = JSON.parse((ev as MessageEvent<string>).data) as Tick;
      this.zone.run(() => this.apply(tick));
    });
    es.onopen = () => this.connected.set(true);
    es.onerror = () => {
      this.connected.set(false);
      es.close();
      clearTimeout(this.retry);
      this.retry = setTimeout(() => this.connect(), 2000);
    };
  }

  private apply(tick: Tick): void {
    this.status.set(tick.status);
    this.anchorSim = Date.parse(tick.status.sim_now);
    this.anchorPerf = performance.now();
    this.simNow.set(this.anchorSim);
    this.throughputHistory.update((h) => [...h.slice(-89), tick.status.throughput.msg_s]);
    if (tick.messaggi.length) {
      this.messages.update((m) => [...m, ...tick.messaggi].slice(-400));
    }
    if (tick.eventi.length) {
      this.events.update((e) => [...e, ...tick.eventi].slice(-200));
      this.domainEvents$.next(tick.eventi);
    }
  }

  ngOnDestroy(): void {
    this.source?.close();
    clearTimeout(this.retry);
    clearInterval(this.clockTimer);
  }
}
