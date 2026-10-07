import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';

import { fmtAgo } from '../../../../core/format';
import { IconComponent } from '../../../../shared/icon.component';
import { STATES, stateMeta } from '../../iot.catalog';
import { LandFeature, LandCollection, Station } from '../../models/iot.models';

/** Menu laterale (RF-IOT-04, RF-IOT-05): elenco, selezione singola e multipla, eliminazione. */
@Component({
  selector: 'ag-stations-sidebar',
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './stations-sidebar.component.html',
  styleUrl: './stations-sidebar.component.css',
})
export class StationsSidebarComponent {
  readonly stations = input<Station[]>([]);
  readonly selectedId = input<string | null>(null);
  readonly canEdit = input(false);
  readonly simNow = input(Date.now());
  readonly loading = input(false);
  readonly lands = input<LandCollection | null>(null);

  readonly select = output<Station>();
  readonly deleteMany = output<Station[]>();
  readonly landFocus = output<LandFeature>();

  protected readonly query = signal('');
  protected readonly checked = signal<ReadonlySet<string>>(new Set());

  protected readonly filtered = computed(() => {
    const q = this.query().trim().toLowerCase();
    const list = this.stations();
    return q
      ? list.filter((s) => `${s.nome} ${s.land_nome} ${s.gateway_id}`.toLowerCase().includes(q))
      : list;
  });

  /** Barra di composizione degli stati in testa all'elenco. */
  protected readonly composition = computed(() => {
    const list = this.stations();
    return Object.entries(STATES).map(([k, meta]) => ({
      key: k,
      label: meta.label,
      color: meta.color,
      n: list.filter((s) => s.stato === k).length,
    }));
  });

  /** Terreni senza stazione attiva: dove si può registrare (D8). I lotti di prova sono solo contati. */
  protected readonly freeLands = computed(() => {
    const free = (this.lands()?.features ?? []).filter((f) => !f.properties.station_id);
    return {
      real: free.filter((f) => !f.properties.di_prova),
      test: free.filter((f) => f.properties.di_prova).length,
    };
  });

  protected readonly checkedStations = computed(() =>
    this.stations().filter((s) => this.checked().has(s.id)),
  );
  protected readonly allChecked = computed(
    () => this.filtered().length > 0 && this.filtered().every((s) => this.checked().has(s.id)),
  );

  protected readonly meta = stateMeta;
  protected readonly ago = fmtAgo;

  constructor() {
    // le stazioni eliminate o sparite dal server escono anche dalla selezione multipla
    effect(() => {
      const ids = new Set(this.stations().map((s) => s.id));
      untracked(() => {
        const next = new Set([...this.checked()].filter((id) => ids.has(id)));
        if (next.size !== this.checked().size) this.checked.set(next);
      });
    });
  }

  toggle(id: string, ev: Event): void {
    ev.stopPropagation();
    const next = new Set(this.checked());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.checked.set(next);
  }

  toggleAll(): void {
    this.checked.set(this.allChecked() ? new Set() : new Set(this.filtered().map((s) => s.id)));
  }

  clearChecked(): void {
    this.checked.set(new Set());
  }
}
