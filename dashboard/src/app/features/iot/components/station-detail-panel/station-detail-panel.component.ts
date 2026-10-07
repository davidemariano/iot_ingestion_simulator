import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { forkJoin } from 'rxjs';

import {
  cardinal,
  fmtAgo,
  fmtDateTime,
  fmtDuration,
  fmtNum,
  fmtTime,
} from '../../../../core/format';
import { IconComponent } from '../../../../shared/icon.component';
import {
  ALERT_LABELS,
  CADENCES,
  ChannelMeta,
  GROUPS,
  channelKey,
  channelMeta,
  stateMeta,
} from '../../iot.catalog';
import {
  Alert,
  GpsValue,
  Latest,
  LatestChannel,
  MotionValue,
  StationDetail,
  StatusTransition,
} from '../../models/iot.models';
import { IotApiService } from '../../services/iot-api.service';
import { ChannelChartComponent } from '../channel-chart/channel-chart.component';
import { StationFaultsComponent } from '../station-faults/station-faults.component';
import { WaterGaugeComponent } from '../water-gauge/water-gauge.component';

interface Row {
  ch: LatestChannel;
  meta: ChannelMeta;
  value: string;
  unit: string;
  extra: string | null;
  arrow: number | null;
  spark: string;
}

/** Pannello di dettaglio (RF-IOT-12, RF-IOT-17): ultimi valori, stress idrico, allerte, grafici 24 h. */
@Component({
  selector: 'ag-station-detail-panel',
  imports: [IconComponent, WaterGaugeComponent, ChannelChartComponent, StationFaultsComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './station-detail-panel.component.html',
  styleUrl: './station-detail-panel.component.css',
})
export class StationDetailPanelComponent {
  readonly stationId = input.required<string>();
  readonly simNow = input.required<number>();
  readonly canEdit = input(false);
  readonly refresh = input(0);

  readonly closed = output<void>();
  readonly changed = output<void>();
  readonly deleteRequest = output<StationDetail>();

  private readonly api = inject(IotApiService);

  protected readonly detail = signal<StationDetail | null>(null);
  protected readonly latest = signal<Latest | null>(null);
  protected readonly sparks = signal<Map<string, (number | null)[]>>(new Map());
  protected readonly alerts = signal<Alert[]>([]);
  protected readonly log = signal<StatusTransition[]>([]);
  protected readonly chartKey = signal('soil_moisture@15');
  protected readonly range = signal<'24h' | '7g'>('24h');
  protected readonly chartRefresh = signal(0);
  protected readonly editing = signal(false);
  protected readonly draftName = signal('');

  protected readonly cadences = CADENCES;
  protected readonly ago = fmtAgo;
  protected readonly dt = fmtDateTime;
  protected readonly tm = fmtTime;
  protected readonly num = fmtNum;
  protected readonly dur = fmtDuration;
  protected readonly stateMeta = stateMeta;
  protected readonly alertLabel = (t: string) => ALERT_LABELS[t] ?? t;

  protected readonly rawInd = computed(() => this.latest()?.indicatori['raw_suolo'] ?? null);
  protected readonly present = computed(
    () => this.latest()?.canali.filter((c) => c.presente).length ?? 0,
  );

  protected readonly groups = computed(() => {
    const l = this.latest();
    const d = this.detail();
    if (!l || !d) return [];
    const rows = l.canali.map((ch) => this.row(ch, d));
    return GROUPS.map((g) => ({ name: g, rows: rows.filter((r) => r.meta.group === g) })).filter(
      (g) => g.rows.length,
    );
  });

  protected readonly chartChannel = computed(() => {
    const d = this.detail();
    const key = this.chartKey();
    const s =
      d?.sensors.find((x) => channelKey(x.sensor_type, x.profondita_cm) === key) ?? d?.sensors[0];
    return s ? { sensor: s, meta: channelMeta(s.sensor_type, s.profondita_cm) } : null;
  });

  protected readonly thresholds = computed(() => {
    const d = this.detail();
    const c = this.chartChannel();
    if (!d || c?.sensor.sensor_type !== 'soil_moisture') return [];
    return [
      { value: d.theta_fc, label: 'θFC', color: '--sage-700' },
      { value: d.theta_wp, label: 'θWP', color: '--brick' },
    ];
  });

  constructor() {
    effect(() => {
      const id = this.stationId();
      untracked(() => {
        this.detail.set(null);
        this.latest.set(null);
        this.editing.set(false);
        this.load(id);
      });
    });
    effect(() => {
      if (this.refresh() === 0) return;
      untracked(() => this.load(this.stationId(), true));
    });
  }

  private load(id: string, soft = false): void {
    forkJoin({
      detail: this.api.station(id),
      latest: this.api.latest(id),
      sparks: this.api.sparklines(id),
      alerts: this.api.alerts(id, 'aperta'),
      log: this.api.statusLog(id),
    }).subscribe({
      next: (r) => {
        if (id !== this.stationId()) return;
        this.detail.set(r.detail);
        this.latest.set(r.latest);
        this.sparks.set(new Map(r.sparks.map((s) => [s.sensor_id, s.v])));
        this.alerts.set(r.alerts);
        this.log.set(r.log.slice(0, 6));
        if (soft) this.chartRefresh.update((n) => n + 1);
      },
      error: () => {
        if (!soft) this.closed.emit();
      },
    });
  }

  private row(ch: LatestChannel, d: StationDetail): Row {
    const meta = channelMeta(ch.sensor_type, ch.profondita_cm);
    let value = '—';
    let unit = meta.unit;
    let extra: string | null = null;
    let arrow: number | null = null;
    if (ch.value !== null) {
      if (ch.sensor_type === 'gps') {
        const g = ch.value as GpsValue;
        value = fmtNum(distance(d.lat, d.lon, g.lat, g.lon), 1);
        unit = 'm';
        extra = `hdop ${fmtNum(g.hdop ?? 0, 1)} · ${g.sats ?? '—'} sat`;
      } else if (ch.sensor_type === 'accelerometer') {
        const m = ch.value as MotionValue;
        value = fmtNum(m.inclinazione_deg, 1);
        unit = '°';
        extra = `|a| ${fmtNum(Math.hypot(m.ax, m.ay, m.az), 2)} g`;
      } else {
        const v = ch.value as number;
        value = fmtNum(v, meta.decimals);
        if (ch.sensor_type === 'wind_direction') {
          unit = `° ${cardinal(v)}`;
          arrow = (v + 180) % 360; // il vento proviene da v: la freccia indica dove va
        }
      }
    }
    return {
      ch,
      meta,
      value,
      unit,
      extra,
      arrow,
      spark: sparkPath(this.sparks().get(ch.sensor_id) ?? []),
    };
  }

  selectChannel(key: string): void {
    this.chartKey.set(key);
  }

  keyOf(ch: LatestChannel): string {
    return channelKey(ch.sensor_type, ch.profondita_cm);
  }

  setCadence(value: string): void {
    const d = this.detail();
    if (!d) return;
    this.api.update(d.id, { cadenza_s: Number(value) }).subscribe(() => {
      this.load(d.id, true);
      this.changed.emit();
    });
  }

  startRename(): void {
    this.draftName.set(this.detail()?.nome ?? '');
    this.editing.set(true);
  }

  saveName(): void {
    const d = this.detail();
    const nome = this.draftName().trim();
    if (!d || !nome || nome.length > 60) return;
    this.api.update(d.id, { nome }).subscribe(() => {
      this.editing.set(false);
      this.load(d.id, true);
      this.changed.emit();
    });
  }
}

function distance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (x: number) => (x * Math.PI) / 180;
  const a =
    Math.sin(r(lat2 - lat1) / 2) ** 2 +
    Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.sqrt(a));
}

/** Path SVG 64×18 della mini-serie; i buchi (null) interrompono la linea. */
function sparkPath(v: (number | null)[]): string {
  const vals = v.filter((x): x is number => x !== null);
  if (vals.length < 2) return '';
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const w = 64;
  const h = 18;
  let d = '';
  let pen = false;
  v.forEach((x, i) => {
    if (x === null) {
      pen = false;
      return;
    }
    const px = (i / (v.length - 1)) * w;
    const py = h - 1.5 - ((x - min) / span) * (h - 3);
    d += `${pen ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`;
    pen = true;
  });
  return d;
}
