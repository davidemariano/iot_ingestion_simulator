import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { debounceTime, forkJoin, interval } from 'rxjs';
import Swal from 'sweetalert2';

import { RoleService } from '../../../../core/role.service';
import { SimStreamService } from '../../../../core/sim-stream.service';
import { StationDetailPanelComponent } from '../../components/station-detail-panel/station-detail-panel.component';
import { StationMapComponent } from '../../components/station-map/station-map.component';
import { StationRegisterModalComponent } from '../../components/station-register-modal/station-register-modal.component';
import { StationsSidebarComponent } from '../../components/stations-sidebar/stations-sidebar.component';
import { LandCollection, Station } from '../../models/iot.models';
import { IotApiService } from '../../services/iot-api.service';

const POLL_MS = 30_000; // RNF-IOT-08: stati aggiornati ogni 30 s
const DETAIL_MS = 5_000;

/** Pagina /app/iot (§10): mappa + menu laterale + pannello di dettaglio. */
@Component({
  selector: 'ag-iot-dashboard',
  imports: [
    StationsSidebarComponent,
    StationMapComponent,
    StationDetailPanelComponent,
    StationRegisterModalComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="layout" [class.with-detail]="!!selectedId()">
      <ag-stations-sidebar
        [stations]="stations()"
        [selectedId]="selectedId()"
        [canEdit]="role.isAdmin()"
        [simNow]="stream.simNow()"
        [loading]="loading()"
        [lands]="lands()"
        (landFocus)="map().focusLand($event)"
        (select)="select($event)"
        (deleteMany)="confirmDelete($event)"
      />
      <ag-station-map
        [lands]="lands()"
        [stations]="stations()"
        [selectedId]="selectedId()"
        [canRegister]="role.isAdmin()"
        [pending]="pending()"
        (mapClick)="openRegister($event)"
        (stationSelect)="selectById($event)"
      />
      @if (selectedId(); as id) {
        <ag-station-detail-panel
          [stationId]="id"
          [simNow]="stream.simNow()"
          [canEdit]="role.isAdmin()"
          [refresh]="detailTick()"
          (closed)="selectedId.set(null)"
          (changed)="reload()"
          (deleteRequest)="confirmDelete([$event])"
        />
      }
    </div>

    @if (pending(); as p) {
      <ag-station-register-modal
        [point]="p"
        [busy]="saving()"
        [error]="registerError()"
        (save)="register($event)"
        (cancel)="closeRegister()"
      />
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    .layout {
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      height: 100%;
    }
    .layout.with-detail {
      grid-template-columns: 300px minmax(0, 1fr) 400px;
    }
    @media (max-width: 1180px) {
      .layout,
      .layout.with-detail {
        grid-template-columns: 260px minmax(0, 1fr);
      }
      ag-station-detail-panel {
        position: fixed;
        top: 48px;
        right: 0;
        bottom: 30px;
        width: min(400px, 100vw);
        z-index: 1000;
      }
    }
  `,
})
export class IotDashboardComponent {
  private readonly api = inject(IotApiService);
  protected readonly role = inject(RoleService);
  protected readonly stream = inject(SimStreamService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly map = viewChild.required(StationMapComponent);

  protected readonly stations = signal<Station[]>([]);
  protected readonly lands = signal<LandCollection | null>(null);
  protected readonly selectedId = signal<string | null>(null);
  protected readonly loading = signal(true);
  protected readonly pending = signal<{ lat: number; lon: number } | null>(null);
  protected readonly saving = signal(false);
  protected readonly registerError = signal<string | null>(null);
  protected readonly detailTick = signal(0);

  constructor() {
    this.reload();
    interval(POLL_MS)
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.reload());
    interval(DETAIL_MS)
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.selectedId() && this.detailTick.update((n) => n + 1));
    // gli eventi di dominio (registrazioni, transizioni, allerte) anticipano il polling
    this.stream.domainEvents$.pipe(debounceTime(300), takeUntilDestroyed()).subscribe((evs) => {
      if (evs.some((e) => e.tipo !== 'partizione')) {
        this.reload();
        if (evs.some((e) => e.station_id === this.selectedId()))
          this.detailTick.update((n) => n + 1);
      }
    });
    // il pannello di dettaglio cambia la larghezza della mappa
    effect(() => {
      this.selectedId();
      this.map().invalidate();
    });
    this.destroyRef.onDestroy(() => Swal.close());
  }

  reload(): void {
    forkJoin({ stations: this.api.stations(), lands: this.api.myLands() }).subscribe({
      next: ({ stations, lands }) => {
        this.stations.set(stations);
        this.lands.set(lands);
        this.loading.set(false);
        if (this.selectedId() && !stations.some((s) => s.id === this.selectedId()))
          this.selectedId.set(null);
      },
      error: () => this.loading.set(false),
    });
  }

  select(st: Station): void {
    this.selectedId.set(st.id);
    this.map().focus(st);
  }

  selectById(id: string): void {
    const st = this.stations().find((s) => s.id === id);
    if (st) this.select(st);
  }

  openRegister(p: { lat: number; lon: number }): void {
    if (this.saving()) return;
    this.registerError.set(null);
    this.pending.set(p);
  }

  closeRegister(): void {
    this.pending.set(null);
    this.registerError.set(null);
  }

  register(nome: string): void {
    const p = this.pending();
    if (!p) return;
    this.saving.set(true);
    this.registerError.set(null);
    this.api.register({ nome, lat: p.lat, lon: p.lon }).subscribe({
      next: (st) => {
        this.saving.set(false);
        this.pending.set(null);
        // nuova stazione visibile subito dopo il salvataggio (RNF-IOT-08)
        this.stations.update((list) => [...list, st]);
        this.selectedId.set(st.id);
        this.reload();
      },
      error: (e: HttpErrorResponse) => {
        this.saving.set(false);
        // 422 / 409: messaggio nella modale, che resta aperta (RF-IOT-03, AC8)
        this.registerError.set(
          typeof e.error?.detail === 'string'
            ? e.error.detail
            : `Errore ${e.status}: registrazione non riuscita`,
        );
      },
    });
  }

  async confirmDelete(list: Station[]): Promise<void> {
    if (!list.length) return;
    const many = list.length > 1;
    const names = list
      .slice(0, 6)
      .map(
        (s) =>
          `<li>${escapeHtml(s.nome)} <span style="color:var(--ink-3)">· ${escapeHtml(s.land_nome)}</span></li>`,
      )
      .join('');
    const more = list.length > 6 ? `<li>e altre ${list.length - 6}</li>` : '';
    const res = await Swal.fire({
      title: many ? `Eliminare ${list.length} stazioni?` : `Eliminare «${list[0].nome}»?`,
      html: `<ul>${names}${more}</ul><p style="margin-top:10px">Le stazioni vengono dismesse: credenziali MQTT revocate, allerte chiuse,
             terreni riportati alla sorgente SATELLITE. Lo storico delle misure resta nel database.</p>`,
      showCancelButton: true,
      confirmButtonText: many ? 'Elimina selezionate' : 'Elimina',
      cancelButtonText: 'Annulla',
      reverseButtons: true,
      focusCancel: true,
      buttonsStyling: false,
      showClass: { popup: '' },
      hideClass: { popup: '' },
      customClass: {
        container: 'ag-swal-wrap',
        popup: 'ag-swal',
        confirmButton: 'btn btn--danger',
        cancelButton: 'btn',
      },
    });
    if (!res.isConfirmed) return;
    this.api.remove(list.map((s) => s.id)).subscribe(() => {
      const gone = new Set(list.map((s) => s.id));
      this.stations.update((all) => all.filter((s) => !gone.has(s.id)));
      if (this.selectedId() && gone.has(this.selectedId()!)) this.selectedId.set(null);
      this.reload();
    });
  }
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!,
  );
}
