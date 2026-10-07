import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  afterNextRender,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import {
  BarController,
  BarElement,
  Chart,
  ChartConfiguration,
  Filler,
  LineController,
  LineElement,
  LinearScale,
  Plugin,
  PointElement,
  ScatterController,
  Tooltip,
} from 'chart.js';
import { Subscription } from 'rxjs';

import { fmtDay, fmtNum, fmtTime } from '../../../../core/format';
import { ChannelMeta } from '../../iot.catalog';
import { Series } from '../../models/iot.models';
import { IotApiService } from '../../services/iot-api.service';

Chart.register(
  LineController,
  BarController,
  ScatterController,
  LineElement,
  BarElement,
  PointElement,
  LinearScale,
  Tooltip,
  Filler,
);

interface Threshold {
  value: number;
  label: string;
  color: string;
}

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/** Linee orizzontali tratteggiate (θFC e θWP sul grafico dell'umidità del suolo). */
const thresholdPlugin: Plugin<'line' | 'bar' | 'scatter'> = {
  id: 'thresholds',
  afterDatasetsDraw(chart) {
    const th = (chart.options.plugins as { thresholds?: Threshold[] }).thresholds ?? [];
    const { ctx, chartArea, scales } = chart;
    const y = scales['y'];
    ctx.save();
    for (const t of th) {
      const py = y.getPixelForValue(t.value);
      if (py < chartArea.top || py > chartArea.bottom) continue;
      const color = t.color.startsWith('--') ? css(t.color) : t.color;
      ctx.strokeStyle = color;
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(chartArea.left, py);
      ctx.lineTo(chartArea.right, py);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = `500 10px ${css('--font-mono')}`;
      ctx.fillStyle = color;
      ctx.textAlign = 'right';
      ctx.fillText(t.label, chartArea.right - 2, py - 3);
    }
    ctx.restore();
  },
};

/** Grafico delle ultime 24 h (RF-IOT-17) o dei 7 giorni (aggregati orari) per il canale scelto. */
@Component({
  selector: 'ag-channel-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <canvas #canvas></canvas>
      @if (empty()) {
        <div class="empty">Nessuna misura nell’intervallo</div>
      }
    </div>
  `,
  styles: `
    .wrap {
      position: relative;
      height: 170px;
    }
    .empty {
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      color: var(--ink-3);
      font-size: 12px;
    }
  `,
})
export class ChannelChartComponent implements OnDestroy {
  readonly stationId = input.required<string>();
  readonly channel = input.required<ChannelMeta>();
  readonly sensorType = input.required<string>();
  readonly depth = input<number | null>(null);
  readonly range = input<'24h' | '7g'>('24h');
  readonly refresh = input(0);
  readonly simNow = input.required<number>();
  readonly origin = input<{ lat: number; lon: number } | null>(null);
  readonly thresholds = input<Threshold[]>([]);

  private readonly api = inject(IotApiService);
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private chart?: Chart;
  private sub?: Subscription;
  protected readonly empty = signal(false);
  private readonly ready = signal(false);

  constructor() {
    afterNextRender(() => this.ready.set(true));
    effect(() => {
      if (!this.ready()) return;
      const id = this.stationId();
      const type = this.sensorType();
      const depth = this.depth();
      const range = this.range();
      this.refresh();
      untracked(() => this.load(id, type, depth, range));
    });
  }

  private load(id: string, type: string, depth: number | null, range: '24h' | '7g'): void {
    const to = this.simNow();
    const from = to - (range === '24h' ? 24 : 168) * 3600_000;
    const vector = type === 'gps' || type === 'accelerometer';
    this.sub?.unsubscribe();
    this.sub = this.api
      .measurements(id, type, depth, {
        from: new Date(from).toISOString(),
        to: new Date(to).toISOString(),
        agg: range === '7g' && !vector ? 'hour' : 'raw',
      })
      .subscribe((s) => this.draw(s, from, to, range));
  }

  private toPoints(s: Series): { x: number; y: number; q: number }[] {
    if (s.sensor_type === 'gps') {
      const o = this.origin();
      return s.punti.map((p) => {
        const pt = p as unknown as { t: string; lat: number; lon: number };
        return { x: Date.parse(pt.t), y: o ? haversine(o.lat, o.lon, pt.lat, pt.lon) : 0, q: 0 };
      });
    }
    return s.punti.map((p) => ({ x: Date.parse(p.t), y: p.v, q: p.q ?? 0 }));
  }

  private draw(s: Series, from: number, to: number, range: '24h' | '7g'): void {
    const pts = this.toPoints(s);
    this.empty.set(pts.length === 0);
    const meta = this.channel();
    const teal = css('--teal-700');
    const ochre = css('--ochre');
    const ink3 = css('--ink-3');
    const line = css('--line');
    const mono = css('--font-mono');
    const isRain = s.sensor_type === 'rain';
    const isDir = s.sensor_type === 'wind_direction';
    const unit =
      s.sensor_type === 'gps' ? 'm' : s.sensor_type === 'accelerometer' ? '°' : meta.unit;
    const decimals =
      s.sensor_type === 'gps' ? 1 : s.sensor_type === 'accelerometer' ? 1 : meta.decimals;

    const cfg: ChartConfiguration<'line' | 'bar' | 'scatter'> = {
      type: isRain ? 'bar' : isDir ? 'scatter' : 'line',
      data: {
        datasets: [
          {
            data: pts,
            borderColor: teal,
            backgroundColor: isRain || isDir ? teal : 'rgba(89, 195, 195, 0.12)',
            borderWidth: isRain ? 0 : 1.5,
            fill: !isRain && !isDir ? 'origin' : false,
            tension: 0.25,
            pointRadius: pts.map((p) => (p.q ? 3 : isDir ? 1.6 : 0)),
            pointBackgroundColor: pts.map((p) => (p.q ? ochre : teal)),
            pointBorderWidth: 0,
            barThickness: range === '24h' ? 2 : 3,
          } as never,
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        normalized: true,
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        layout: { padding: { top: 6, right: 4 } },
        scales: {
          x: {
            type: 'linear',
            min: from,
            max: to,
            afterBuildTicks: (axis) => {
              axis.ticks = roundTicks(from, to, range === '24h' ? 4 * 3600_000 : 86400_000);
            },
            grid: { display: false },
            border: { color: line },
            ticks: {
              color: ink3,
              font: { family: mono, size: 10 },
              autoSkip: false,
              maxRotation: 0,
              callback: (v) => (range === '24h' ? fmtTime(Number(v)) : fmtDay(Number(v))),
            },
          },
          y: {
            type: 'linear',
            beginAtZero: isRain,
            ...(isDir ? { min: 0, max: 360 } : {}),
            grid: { color: line, drawTicks: false },
            border: { display: false },
            ticks: {
              color: ink3,
              font: { family: mono, size: 10 },
              padding: 6,
              maxTicksLimit: 5,
              ...(isDir ? { stepSize: 90 } : {}),
              callback: (v) => fmtNum(Number(v), Math.min(decimals, 1)),
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: css('--ink'),
            titleFont: { family: mono, size: 11, weight: 'normal' },
            bodyFont: { family: mono, size: 12 },
            padding: 8,
            cornerRadius: 2,
            displayColors: false,
            callbacks: {
              title: (items) =>
                items.length
                  ? fmtDay(items[0].parsed.x ?? 0) + ' ' + fmtTime(items[0].parsed.x ?? 0)
                  : '',
              label: (item) => {
                const p = item.raw as { y: number; q: number };
                return `${fmtNum(p.y, decimals)} ${unit}${p.q ? ' · sospetta' : ''}`;
              },
            },
          },
          ...({ thresholds: this.thresholds() } as object),
        },
      },
      plugins: [thresholdPlugin],
    };

    // aggiornamento in place se il tipo non cambia: niente sfarfallio ai refresh periodici
    if (this.chart && (this.chart.config as ChartConfiguration).type === cfg.type) {
      this.chart.data = cfg.data as ChartConfiguration['data'];
      this.chart.options = cfg.options as NonNullable<ChartConfiguration['options']>;
      this.chart.update('none');
      return;
    }
    this.chart?.destroy();
    this.chart = new Chart(this.canvas().nativeElement, cfg as ChartConfiguration);
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.chart?.destroy();
  }
}

/** Tick a ore piene (ogni 4 h) o a mezzanotte, nel fuso dell'Italia. */
function roundTicks(from: number, to: number, step: number): { value: number }[] {
  const offset = tzOffsetMs(from);
  const first = Math.ceil((from + offset) / step) * step - offset;
  const out: { value: number }[] = [];
  for (let t = first; t <= to; t += step) out.push({ value: t });
  return out;
}

function tzOffsetMs(t: number): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Europe/Rome',
    hour: 'numeric',
    hourCycle: 'h23',
  })
    .formatToParts(new Date(t))
    .find((p) => p.type === 'hour');
  const romeHour = Number(parts?.value ?? 0);
  const utcHour = new Date(t).getUTCHours();
  return (((romeHour - utcHour + 24) % 24) * 3600_000) as number;
}

function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const a =
    Math.sin(r(lat2 - lat1) / 2) ** 2 +
    Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.sqrt(a));
}
