import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { fmtCount, fmtNum, fmtTime } from '../../../../core/format';
import { RoleService } from '../../../../core/role.service';
import {
  DomainEvent,
  MessageLogEntry,
  SimStreamService,
} from '../../../../core/sim-stream.service';
import { IconComponent } from '../../../../shared/icon.component';
import { ALERT_LABELS } from '../../../iot/iot.catalog';
import { GlobalFaults, SimApiService, SimConfig } from '../../services/sim-api.service';

type Filter = 'tutti' | 'scarti' | 'rifiuti';

const SCENARI: Record<string, string> = {
  nominale: 'Nominale',
  siccita: 'Siccità',
  pioggia: 'Pioggia',
  caldo: 'Ondata di calore',
  gelata: 'Gelata',
};

const CAUSE: Record<string, string> = {
  duplicato: 'Duplicato',
  fuori_intervallo: 'Fuori intervallo',
  timestamp_futuro: 'Timestamp nel futuro',
  timestamp_scaduto: 'Più vecchio di 24 h',
  schema: 'Schema non valido',
  tipo_incoerente: 'Tipo incoerente',
  unita_non_valida: 'Unità non valida',
  topic_incoerente: 'Topic incoerente',
  canale_sconosciuto: 'Canale sconosciuto',
  canale_disattivato: 'Canale disattivato',
  non_autorizzato: 'Credenziali revocate',
  acl_negata: 'ACL negata',
  coda_piena: 'Coda del broker piena',
  troppo_grande: 'Payload troppo grande',
  picco: 'Picco',
  costante: 'Valore costante',
};

const FAULTS: { key: keyof GlobalFaults; label: string; hint: string }[] = [
  {
    key: 'duplicati_pct',
    label: 'Duplicati',
    hint: 'stesso canale e timestamp, scartati dalla chiave primaria',
  },
  { key: 'fuori_intervallo_pct', label: 'Fuori intervallo', hint: 'oltre [min, max] del catalogo' },
  {
    key: 'fuori_ordine_pct',
    label: 'Fuori ordine',
    hint: 'consegnati dopo il messaggio successivo',
  },
  { key: 'ritardo_pct', label: 'Ritardi', hint: 'dati tardivi, accettati entro 24 h' },
  { key: 'malformati_pct', label: 'Malformati', hint: 'timestamp non interpretabile' },
  {
    key: 'unita_alternative_pct',
    label: 'Unità non canoniche',
    hint: '°F, kPa, klx, km/h… normalizzate',
  },
];

/** Console del simulatore: tempo, meteo, carico, guasti e KPI del §14. */
@Component({
  selector: 'ag-simulator-console',
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './simulator-console.component.html',
  styleUrl: './simulator-console.component.css',
})
export class SimulatorConsoleComponent {
  protected readonly stream = inject(SimStreamService);
  protected readonly role = inject(RoleService);
  private readonly api = inject(SimApiService);

  protected readonly cfg = signal<SimConfig | null>(null);
  protected readonly filter = signal<Filter>('tutti');
  protected readonly bulkN = signal(10);
  protected readonly busy = signal<string | null>(null);
  protected readonly flash = signal<string | null>(null);

  protected readonly factors = [1, 20, 60, 180, 360];
  protected readonly scenari = Object.entries(SCENARI);
  protected readonly faults = FAULTS;
  protected readonly delays = [300, 900, 3600, 6 * 3600];
  protected readonly num = fmtNum;
  protected readonly count = fmtCount;
  protected readonly time = fmtTime;

  protected readonly s = this.stream.status;
  protected readonly saved = computed(() => sum(this.s()?.contatori.salvati));
  protected readonly discarded = computed(() => sum(this.s()?.contatori.scartati));
  protected readonly rejected = computed(() => sum(this.s()?.contatori.rifiutati_broker));
  protected readonly p95ok = computed(() => {
    const p = this.s()?.latenza_s.p95;
    return p === null || p === undefined ? null : p <= 5;
  });

  protected readonly causes = computed(() => {
    const st = this.s();
    if (!st) return [];
    const rows = [
      ...Object.entries(st.contatori.scartati).map(([k, n]) => ({ k, n, where: 'ingestion' })),
      ...Object.entries(st.contatori.rifiutati_broker).map(([k, n]) => ({ k, n, where: 'broker' })),
    ].sort((a, b) => b.n - a.n);
    const max = Math.max(1, ...rows.map((r) => r.n));
    return rows.map((r) => ({ ...r, label: CAUSE[r.k] ?? r.k, w: (r.n / max) * 100 }));
  });

  protected readonly histogram = computed(() => {
    const h = this.s()?.istogramma_latenza ?? [];
    const max = Math.max(1, ...h.map((x) => x.n));
    return h.map((x) => ({ ...x, h: (x.n / max) * 100 }));
  });

  protected readonly partitions = computed(() =>
    Object.entries(this.s()?.partizioni ?? {}).map(([name, n]) => ({
      name,
      n,
      isDefault: name.endsWith('_default'),
    })),
  );

  protected readonly messages = computed(() => {
    const f = this.filter();
    const all = this.stream.messages();
    const list =
      f === 'scarti'
        ? all.filter((m) => m.esito === 'scartato' || m.esito === 'sospetto')
        : f === 'rifiuti'
          ? all.filter((m) => m.esito === 'rifiutato')
          : all;
    return list.slice(-120).reverse();
  });

  protected readonly events = computed(() => this.stream.events().slice(-60).reverse());

  protected readonly spark = computed(() => {
    const h = this.stream.throughputHistory();
    if (h.length < 2) return '';
    const max = Math.max(1, ...h);
    return h
      .map(
        (v, i) =>
          `${i ? 'L' : 'M'}${((i / 89) * 160).toFixed(1)} ${(30 - (v / max) * 27).toFixed(1)}`,
      )
      .join('');
  });

  constructor() {
    this.api.config().subscribe((c) => this.cfg.set(c));
  }

  private apply(body: Parameters<SimApiService['patch']>[0], tag: string): void {
    this.busy.set(tag);
    this.api.patch(body).subscribe({
      next: (c) => {
        this.cfg.set(c);
        this.busy.set(null);
      },
      error: () => this.busy.set(null),
    });
  }

  setFactor(f: number): void {
    this.apply({ fattore: f }, 'fattore');
  }

  togglePause(): void {
    this.apply({ in_pausa: !this.stream.paused() }, 'pausa');
  }

  setScenario(s: string): void {
    this.apply({ scenario_meteo: s }, 'scenario');
  }

  setFault(key: keyof GlobalFaults, value: number): void {
    this.apply({ guasti: { [key]: value } }, key);
  }

  resetFaults(): void {
    this.apply(
      {
        guasti: {
          duplicati_pct: 0,
          fuori_intervallo_pct: 0,
          fuori_ordine_pct: 0,
          ritardo_pct: 0,
          malformati_pct: 0,
          unita_alternative_pct: 0,
        },
      },
      'reset',
    );
  }

  createStations(): void {
    const n = Math.max(1, Math.min(200, Math.round(this.bulkN())));
    this.busy.set('bulk');
    this.api.bulk(n).subscribe({
      next: (r) => this.done(`${r.create} stazioni di prova registrate`),
      error: () => this.busy.set(null),
    });
  }

  restart(): void {
    this.busy.set('restart');
    this.api.restartIngestion(5).subscribe({
      next: (r) =>
        this.done(`Ingestion riavviata: ${r.riconsegnati} messaggi riconsegnati con DUP`),
      error: () => this.busy.set(null),
    });
  }

  dbDown(): void {
    this.busy.set('db');
    this.api
      .dbDown(60)
      .subscribe({
        next: () => this.done('Database non disponibile per 60 s'),
        error: () => this.busy.set(null),
      });
  }

  private done(msg: string): void {
    this.busy.set(null);
    this.flash.set(msg);
    setTimeout(() => this.flash.set(null), 4000);
  }

  faultValue(key: keyof GlobalFaults): number {
    return this.cfg()?.guasti[key] ?? 0;
  }

  activeFaults(): number {
    const g = this.cfg()?.guasti;
    return g ? FAULTS.filter((f) => g[f.key] > 0).length : 0;
  }

  shortTopic(m: MessageLogEntry): string {
    const p = m.topic.split('/');
    return p.length === 4 ? p[2] : m.topic;
  }

  causeLabel(c: string | null): string {
    return c ? (CAUSE[c] ?? c) : '';
  }

  eventText(e: DomainEvent): string {
    if (e.tipo === 'stato') return `${e.da} → ${e.a} · ${e.messaggio}`;
    if (e.tipo === 'allerta')
      return `${ALERT_LABELS[e.allerta ?? ''] ?? e.allerta} ${e.azione} · ${e.messaggio}`;
    return e.messaggio;
  }

  scenarioLabel(k: string | undefined): string {
    return k ? (SCENARI[k] ?? k) : '';
  }
}

function sum(rec: Record<string, number> | undefined): number {
  return rec ? Object.values(rec).reduce((a, b) => a + b, 0) : 0;
}
