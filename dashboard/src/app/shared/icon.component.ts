import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Icone a tratto disegnate su griglia 16×16, stroke 1.5. */
const PATHS: Record<string, string> = {
  close: 'M4 4l8 8M12 4l-8 8',
  play: 'M5 3.5v9l7-4.5z',
  pause: 'M5.5 3.5v9M10.5 3.5v9',
  trash: 'M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4',
  search: 'M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM10.6 10.6L14 14',
  pin: 'M8 14s4.5-4.2 4.5-7.5a4.5 4.5 0 0 0-9 0C3.5 9.8 8 14 8 14zM8 8a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
  alert: 'M8 2.5l6 10.5H2zM8 6.5v3M8 11.2v.3',
  wave: 'M1.5 8h2l1.5-4 2 8 2-6 1.5 3h4',
  layers: 'M8 2.5l6 3-6 3-6-3zM2 8.5l6 3 6-3M2 11l6 3 6-3',
  arrow: 'M8 13V3M4.5 6.5L8 3l3.5 3.5',
  bolt: 'M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z',
  db: 'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2zM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
  restart: 'M3 8a5 5 0 1 0 1.5-3.6M3 2.5v2.5h2.5',
  plus: 'M8 3v10M3 8h10',
  chevron: 'M6 4l4 4-4 4',
  edit: 'M3 13l.5-2.5L10.5 3.5l2 2-7 7zM9 5l2 2',
};

@Component({
  selector: 'ag-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<svg
    [attr.width]="size()"
    [attr.height]="size()"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path [attr.d]="d()" [attr.fill]="filled() ? 'currentColor' : 'none'" />
  </svg>`,
  styles: `
    :host {
      display: inline-flex;
      flex: none;
    }
  `,
})
export class IconComponent {
  readonly name = input.required<string>();
  readonly size = input(16);
  protected readonly d = computed(() => PATHS[this.name()] ?? '');
  protected readonly filled = computed(() => this.name() === 'play' || this.name() === 'bolt');
}
