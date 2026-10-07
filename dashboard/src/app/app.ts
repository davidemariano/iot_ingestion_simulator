import { HttpClient } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { fmtCount, fmtDay, fmtNum, fmtTimeSec } from './core/format';
import { RoleService, Ruolo } from './core/role.service';
import { SimStreamService } from './core/sim-stream.service';
import { IconComponent } from './shared/icon.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="top">
      <a class="brand" routerLink="/stazioni" aria-label="Agrivalor IoT">
        <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
          <path
            d="M16 6v20M10 26h12"
            stroke="var(--p-indigo)"
            stroke-width="2.4"
            stroke-linecap="round"
          />
          <path
            d="M11 11.5a7 7 0 0 1 10 0M13.2 14a3.8 3.8 0 0 1 5.6 0"
            stroke="var(--p-teal)"
            stroke-width="2.2"
            fill="none"
            stroke-linecap="round"
          />
          <rect
            x="13.3"
            y="17.3"
            width="5.4"
            height="5.4"
            transform="rotate(45 16 20)"
            fill="var(--p-sage)"
          />
        </svg>
        <span class="brand__name">Agrivalor</span>
        <span class="brand__sub">IoT</span>
      </a>
      <span
        class="sim-mark"
        title="Ambiente simulato: stazioni, broker e ingestion girano in un unico processo"
        >Simulatore PoC</span
      >

      <nav class="tabs">
        <a routerLink="/stazioni" routerLinkActive="on">Stazioni</a>
        <a routerLink="/simulatore" routerLinkActive="on">Simulatore</a>
      </nav>

      <div class="clock" [class.paused]="stream.paused()">
        <span class="eyebrow">Ora simulata</span>
        <span class="clock__day">{{ day() }}</span>
        <span class="clock__time mono">{{ time() }}</span>
        <button
          class="btn btn--ghost btn--icon clock__btn"
          type="button"
          (click)="togglePause()"
          [disabled]="!role.isAdmin()"
          [attr.aria-label]="stream.paused() ? 'Riprendi' : 'Metti in pausa'"
        >
          <ag-icon [name]="stream.paused() ? 'play' : 'pause'" [size]="14" />
        </button>
        <span class="clock__factor mono" title="Compressione temporale">×{{ factor() }}</span>
      </div>

      <div class="role" role="group" aria-label="Ruolo">
        <span class="eyebrow">Ruolo</span>
        <div class="seg">
          @for (r of roles; track r) {
            <button type="button" [class.on]="role.ruolo() === r" (click)="role.set(r)">
              {{ r }}
            </button>
          }
        </div>
      </div>
    </header>

    <main class="main">
      <router-outlet />
    </main>

    <footer class="strip" [class.off]="!stream.connected()">
      <a routerLink="/simulatore" class="strip__link">
        <span class="live" [class.on]="stream.connected() && !stream.paused()"></span>
        <span class="eyebrow">Ingestion</span>
      </a>
      @if (status(); as s) {
        <span class="kv"
          ><b class="mono">{{ num(s.throughput.msg_s, 1) }}</b> msg/s</span
        >
        <span class="kv"
          >p95
          <b class="mono">{{
            s.latenza_s.p95 === null ? '—' : num(s.latenza_s.p95, 2) + ' s'
          }}</b></span
        >
        <span class="kv"
          >salvati <b class="mono">{{ count(saved()) }}</b></span
        >
        <span class="kv" [class.warn]="discarded() > 0"
          >scartati <b class="mono">{{ count(discarded()) }}</b></span
        >
        <span class="kv" [class.crit]="s.contatori.perdite > 0"
          >perdite <b class="mono">{{ count(s.contatori.perdite) }}</b></span
        >
        <span class="kv"
          >coda broker <b class="mono">{{ count(s.broker.in_coda) }}</b></span
        >
        @if (!s.servizi.database) {
          <span class="pill pill--crit">database non disponibile</span>
        }
        @if (!s.servizi.ingestion) {
          <span class="pill pill--warn">ingestion in riavvio</span>
        }
        <span class="strip__right muted">
          {{ s.stazioni.totale }} stazioni · {{ s.stazioni.canali }} canali · seme {{ s.seme }}
        </span>
      } @else {
        <span class="muted">connessione al simulatore…</span>
      }
    </footer>
  `,
  styles: `
    :host {
      display: grid;
      grid-template-rows: 48px minmax(0, 1fr) 30px;
      height: 100vh;
    }
    .top {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 0 14px 0 16px;
      background: var(--surface);
      border-bottom: 1px solid var(--line-strong);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--ink);
      text-decoration: none;
    }
    .brand__name {
      font-weight: 600;
      font-size: 14px;
      letter-spacing: -0.01em;
    }
    .brand__sub {
      font-weight: 500;
      color: var(--ink-3);
      font-size: 14px;
    }
    .brand__sub::before {
      content: '/';
      margin-right: 6px;
      color: var(--line-strong);
    }
    .tabs {
      display: flex;
      align-self: stretch;
      margin-left: 18px;
    }
    .tabs a {
      display: flex;
      align-items: center;
      padding: 0 12px;
      color: var(--ink-2);
      font-weight: 500;
      text-decoration: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
    }
    .tabs a:hover {
      color: var(--ink);
    }
    .tabs a.on {
      color: var(--ink);
      border-bottom-color: var(--p-indigo);
    }
    .clock {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-left: auto;
      padding: 0 4px 0 12px;
      height: 32px;
      border-left: 1px solid var(--line);
    }
    .clock__day {
      color: var(--ink-2);
      text-transform: capitalize;
    }
    .clock__time {
      font-size: 15px;
      font-weight: 500;
      min-width: 70px;
    }
    .clock.paused .clock__time {
      color: var(--ink-3);
    }
    .clock__btn {
      width: 26px;
      height: 26px;
    }
    .clock__factor {
      padding: 1px 6px;
      background: var(--indigo-050);
      color: var(--indigo-700);
      border-radius: 2px;
      font-size: 12px;
    }
    .role {
      display: flex;
      align-items: center;
      gap: 8px;
      padding-left: 12px;
      border-left: 1px solid var(--line);
    }
    .role .seg button {
      text-transform: capitalize;
    }
    .main {
      min-height: 0;
    }
    .strip {
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 0 16px;
      background: var(--surface);
      border-top: 1px solid var(--line-strong);
      font-size: 12px;
      color: var(--ink-2);
      white-space: nowrap;
      overflow: hidden;
    }
    .strip__link {
      display: flex;
      align-items: center;
      gap: 7px;
      text-decoration: none;
    }
    .strip b {
      font-weight: 500;
      color: var(--ink);
    }
    .kv.warn b {
      color: var(--ochre-700);
    }
    .kv.crit b {
      color: var(--brick-700);
    }
    .strip__right {
      margin-left: auto;
    }
    .live {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--line-strong);
    }
    .live.on {
      background: var(--p-teal);
      animation: blink 2s ease-in-out infinite;
    }
    @keyframes blink {
      50% {
        opacity: 0.35;
      }
    }
    @media (max-width: 1100px) {
      .clock__day,
      .role .eyebrow,
      .strip__right {
        display: none;
      }
    }
  `,
})
export class App {
  protected readonly stream = inject(SimStreamService);
  protected readonly role = inject(RoleService);
  private readonly http = inject(HttpClient);
  protected readonly roles: Ruolo[] = ['admin', 'agricoltore'];

  protected readonly status = this.stream.status;
  protected readonly day = computed(() => fmtDay(this.stream.simNow()));
  protected readonly time = computed(() => fmtTimeSec(this.stream.simNow()));
  protected readonly factor = computed(() => fmtNum(this.stream.factor(), 0));
  protected readonly saved = computed(() => sum(this.status()?.contatori.salvati));
  protected readonly discarded = computed(() => sum(this.status()?.contatori.scartati));
  protected readonly num = fmtNum;
  protected readonly count = fmtCount;

  togglePause(): void {
    this.http.patch('/api/sim/config', { in_pausa: !this.stream.paused() }).subscribe();
  }
}

function sum(rec: Record<string, number> | undefined): number {
  return rec ? Object.values(rec).reduce((a, b) => a + b, 0) : 0;
}
