import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { fmtNum } from '../../../../core/format';

/**
 * Indicatore di stress idrico (§9.2): RAW tra punto di appassimento (0) e capacità di campo (1),
 * con le soglie FAO-56 (attenzione < 1 − p = 0,5; critica < 0,3).
 */
@Component({
  selector: 'ag-water-gauge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="top">
      <div class="value">
        <span class="mono big">{{ raw() === null ? '—' : pct() }}</span>
        @if (raw() !== null) {
          <span class="unit">%</span>
        }
      </div>
      <span class="pill" [class]="'pill pill--' + level().cls">{{ level().label }}</span>
    </div>
    <div
      class="track"
      role="meter"
      aria-label="Acqua relativamente disponibile"
      aria-valuemin="0"
      aria-valuemax="100"
      [attr.aria-valuenow]="raw() === null ? null : pct()"
    >
      <span class="zone z-crit" [style.width.%]="crit() * 100"></span>
      <span class="zone z-warn" [style.width.%]="(warn() - crit()) * 100"></span>
      <span class="zone z-ok"></span>
      @if (raw() !== null) {
        <span class="fill" [class]="'fill ' + level().cls" [style.width.%]="raw()! * 100"></span>
        <span class="needle" [style.left.%]="raw()! * 100"></span>
      }
    </div>
    <div class="scale mono">
      <span style="left: 0">θWP</span>
      <span [style.left.%]="crit() * 100">30</span>
      <span [style.left.%]="warn() * 100">50</span>
      <span style="left: 100%">θFC</span>
    </div>
    <p class="detail">
      θ radicale <b class="mono">{{ num(theta(), 1) }}</b> %vol <span class="sep">·</span> θWP
      <span class="mono">{{ num(wp(), 1) }}</span> <span class="sep">·</span> θFC
      <span class="mono">{{ num(fc(), 1) }}</span>
    </p>
  `,
  styles: `
    :host {
      display: block;
    }
    .top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .value {
      display: flex;
      align-items: baseline;
      gap: 2px;
    }
    .big {
      font-size: 30px;
      font-weight: 500;
      line-height: 1;
      letter-spacing: -0.03em;
    }
    .unit {
      font-size: 15px;
      color: var(--ink-3);
    }
    .track {
      position: relative;
      display: flex;
      height: 12px;
      margin-top: 12px;
      overflow: visible;
    }
    .zone {
      height: 100%;
    }
    .z-crit {
      background: var(--brick-100);
    }
    .z-warn {
      background: var(--ochre-100);
    }
    .z-ok {
      flex: 1;
      background: var(--sage-100);
    }
    .fill {
      position: absolute;
      left: 0;
      top: 4px;
      height: 4px;
      background: var(--sage-700);
    }
    .fill.warn {
      background: var(--ochre);
    }
    .fill.crit {
      background: var(--brick);
    }
    .needle {
      position: absolute;
      top: -4px;
      bottom: -4px;
      width: 2px;
      margin-left: -1px;
      background: var(--ink);
    }
    .scale {
      position: relative;
      height: 16px;
      margin-top: 4px;
      font-size: 10.5px;
      color: var(--ink-3);
    }
    .scale span {
      position: absolute;
      transform: translateX(-50%);
    }
    .scale span:first-child {
      transform: none;
    }
    .scale span:last-child {
      transform: translateX(-100%);
    }
    .detail {
      margin-top: 6px;
      font-size: 12px;
      color: var(--ink-2);
    }
    .detail b {
      font-weight: 500;
      color: var(--ink);
    }
    .sep {
      margin: 0 4px;
      color: var(--line-strong);
    }
  `,
})
export class WaterGaugeComponent {
  readonly raw = input<number | null>(null);
  readonly theta = input<number | null>(null);
  readonly fc = input<number | null>(null);
  readonly wp = input<number | null>(null);
  readonly crit = input(0.3);
  readonly warn = input(0.5);

  protected readonly pct = computed(() => Math.round((this.raw() ?? 0) * 100));
  protected readonly level = computed(() => {
    const r = this.raw();
    if (r === null) return { cls: 'neutral', label: 'In attesa di dati' };
    if (r < this.crit()) return { cls: 'crit', label: 'Stress critico' };
    if (r < this.warn()) return { cls: 'warn', label: 'Attenzione' };
    return { cls: 'ok', label: 'Nessuno stress' };
  });
  protected readonly num = fmtNum;
}
