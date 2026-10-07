import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  computed,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { fmtNum } from '../../../../core/format';
import { IconComponent } from '../../../../shared/icon.component';

/** Modale "Registra una stazione" (RF-IOT-01, RF-IOT-03, §10.3). */
@Component({
  selector: 'ag-station-register-modal',
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:keydown.escape)': 'onEscape()' },
  template: `
    <div class="backdrop" (click)="cancel.emit()"></div>
    <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="reg-title">
      <header>
        <span class="eyebrow">Nuova stazione · 16 canali</span>
        <h2 id="reg-title">Registra una stazione</h2>
        <button
          class="btn btn--ghost btn--icon close"
          type="button"
          (click)="cancel.emit()"
          aria-label="Chiudi"
        >
          <ag-icon name="close" />
        </button>
      </header>

      <form (submit)="submit($event)">
        <label class="lbl" for="reg-nome">Nome <span class="req">obbligatorio</span></label>
        <input
          #nameInput
          id="reg-nome"
          class="field"
          type="text"
          maxlength="60"
          autocomplete="off"
          placeholder="es. Serrone Nord"
          [value]="nome()"
          (input)="nome.set($any($event.target).value)"
          [attr.aria-invalid]="!!error()"
          aria-describedby="reg-help"
        />
        <div id="reg-help" class="help">
          <span>Visibile nel menu laterale e nelle allerte.</span>
          <span class="mono">{{ nome().length }}/60</span>
        </div>

        <div class="coords">
          <div>
            <span class="eyebrow">Latitudine</span>
            <span class="mono">{{ lat() }}</span>
          </div>
          <div>
            <span class="eyebrow">Longitudine</span>
            <span class="mono">{{ lon() }}</span>
          </div>
          <div class="coords__note">
            Posizione del clic. Il terreno viene ricavato al salvataggio tra i tuoi terreni salvati.
          </div>
        </div>

        @if (error(); as e) {
          <div class="error" role="alert">
            <ag-icon name="alert" [size]="14" />
            <div>
              <b>Registrazione non riuscita</b>
              <p>{{ e }}</p>
            </div>
          </div>
        }

        <footer>
          <button class="btn" type="button" (click)="cancel.emit()">Annulla</button>
          <button class="btn btn--primary" type="submit" [disabled]="!valid() || busy()">
            {{ busy() ? 'Salvataggio…' : 'Salva' }}
          </button>
        </footer>
      </form>
    </section>
  `,
  styleUrl: './station-register-modal.component.css',
})
export class StationRegisterModalComponent {
  readonly point = input.required<{ lat: number; lon: number }>();
  readonly busy = input(false);
  readonly error = input<string | null>(null);
  readonly save = output<string>();
  readonly cancel = output<void>();

  protected readonly nome = signal('');
  protected readonly valid = computed(() => {
    const n = this.nome().trim();
    return n.length >= 1 && n.length <= 60;
  });
  protected readonly lat = computed(() => fmtNum(this.point().lat, 5));
  protected readonly lon = computed(() => fmtNum(this.point().lon, 5));
  private readonly nameInput = viewChild<ElementRef<HTMLInputElement>>('nameInput');

  constructor() {
    afterNextRender(() => this.nameInput()?.nativeElement.focus());
  }

  submit(ev: Event): void {
    ev.preventDefault();
    if (this.valid() && !this.busy()) this.save.emit(this.nome().trim());
  }

  onEscape(): void {
    if (!this.busy()) this.cancel.emit();
  }
}
