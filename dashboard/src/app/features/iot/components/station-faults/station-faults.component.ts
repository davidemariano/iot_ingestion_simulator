import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';

import { IconComponent } from '../../../../shared/icon.component';
import { channelMeta } from '../../iot.catalog';
import { Sensor, StationFaults } from '../../models/iot.models';
import { IotApiService } from '../../services/iot-api.service';

type ChipMode = 'silenzia' | 'blocca';

/** Guasti iniettabili per la singola stazione (§13.3). Non fa parte del prodotto: è marcato SIM. */
@Component({
  selector: 'ag-station-faults',
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './station-faults.component.html',
  styleUrl: './station-faults.component.css',
})
export class StationFaultsComponent {
  readonly stationId = input.required<string>();
  readonly sensors = input<Sensor[]>([]);
  readonly canEdit = input(false);

  private readonly api = inject(IotApiService);
  protected readonly f = signal<StationFaults | null>(null);
  protected readonly mode = signal<ChipMode>('silenzia');
  protected readonly shockSent = signal(false);

  protected readonly chips = computed(() =>
    this.sensors().map((s) => {
      const meta = channelMeta(s.sensor_type, s.profondita_cm);
      const f = this.f();
      return {
        uid: s.device_uid,
        short: meta.short,
        label: meta.label,
        silenced: !!f?.canali_silenziati.includes(s.device_uid),
        stuck: !!f?.canali_bloccati.includes(s.device_uid),
        stuckable: s.sensor_type !== 'gps' && s.sensor_type !== 'accelerometer',
      };
    }),
  );
  protected readonly active = computed(() => {
    const f = this.f();
    if (!f) return 0;
    return (
      +f.offline +
      +(f.spostata_m > 0) +
      +(f.inclinata_deg > 0) +
      +(f.picchi_pct > 0) +
      f.canali_silenziati.length +
      f.canali_bloccati.length
    );
  });

  constructor() {
    effect(() => {
      const id = this.stationId();
      untracked(() => {
        this.f.set(null);
        this.api.faults(id).subscribe((f) => this.f.set(f));
      });
    });
  }

  private push(patch: Partial<StationFaults>): void {
    const cur = this.f();
    if (!cur || !this.canEdit()) return;
    const next = { ...cur, ...patch };
    this.f.set(next);
    this.api
      .setFaults(this.stationId(), next)
      .subscribe({ next: (f) => this.f.set(f), error: () => this.f.set(cur) });
  }

  toggleOffline(): void {
    this.push({ offline: !this.f()?.offline });
  }

  toggleMoved(): void {
    this.push({ spostata_m: this.f()?.spostata_m ? 0 : 150 });
  }

  toggleTilt(): void {
    this.push({ inclinata_deg: this.f()?.inclinata_deg ? 0 : 30 });
  }

  toggleSpikes(): void {
    this.push({ picchi_pct: this.f()?.picchi_pct ? 0 : 4 });
  }

  toggleChip(uid: string): void {
    const f = this.f();
    if (!f) return;
    const flip = (list: string[]) =>
      list.includes(uid) ? list.filter((u) => u !== uid) : [...list, uid];
    if (this.mode() === 'silenzia') this.push({ canali_silenziati: flip(f.canali_silenziati) });
    else this.push({ canali_bloccati: flip(f.canali_bloccati) });
  }

  reset(): void {
    this.push({
      offline: false,
      canali_silenziati: [],
      canali_bloccati: [],
      picchi_pct: 0,
      spostata_m: 0,
      inclinata_deg: 0,
    });
  }

  shock(): void {
    this.api.shock(this.stationId()).subscribe(() => {
      this.shockSent.set(true);
      setTimeout(() => this.shockSent.set(false), 2500);
    });
  }
}
