import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  afterNextRender,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import * as L from 'leaflet';

import { STATES, stateMeta } from '../../iot.catalog';
import { LandCollection, LandFeature, Station } from '../../models/iot.models';

type Base = 'osm' | 'sat';

const CSS = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

@Component({
  selector: 'ag-station-map',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './station-map.component.html',
  styleUrl: './station-map.component.css',
})
export class StationMapComponent implements OnDestroy {
  readonly lands = input<LandCollection | null>(null);
  readonly stations = input<Station[]>([]);
  readonly selectedId = input<string | null>(null);
  readonly canRegister = input(false);
  readonly pending = input<{ lat: number; lon: number } | null>(null);

  readonly mapClick = output<{ lat: number; lon: number }>();
  readonly stationSelect = output<string>();

  protected readonly base = signal<Base>('osm');
  protected readonly states = Object.entries(STATES);
  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private readonly ready = signal(false);

  private map?: L.Map;
  private tiles: Record<Base, L.TileLayer> = {
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap',
    }),
    sat: L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        maxZoom: 19,
        maxNativeZoom: 18,
        attribution: 'Esri, Maxar, Earthstar Geographics',
      },
    ),
  };
  private readonly landLayer = L.featureGroup();
  private readonly stationLayer = L.layerGroup();
  private pendingMarker?: L.Marker;
  private fitted = false;

  constructor() {
    afterNextRender(() => this.init());
    effect(() => this.ready() && this.renderLands(this.lands()));
    effect(() => this.ready() && this.renderStations(this.stations(), this.selectedId()));
    effect(() => this.ready() && this.renderPending(this.pending()));
    effect(() => {
      if (!this.ready() || !this.map) return;
      const b = this.base();
      (Object.keys(this.tiles) as Base[]).forEach((k) =>
        k === b ? this.tiles[k].addTo(this.map!) : this.tiles[k].remove(),
      );
      const el = this.host().nativeElement;
      el.classList.toggle('map--osm', b === 'osm');
      el.classList.toggle('map--sat', b === 'sat');
    });
  }

  private init(): void {
    const map = L.map(this.host().nativeElement, {
      center: [40.146, 18.194],
      zoom: 15,
      zoomControl: false,
      attributionControl: true,
    });
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    map.attributionControl.setPrefix(false);
    this.landLayer.addTo(map);
    this.stationLayer.addTo(map);
    map.on('click', (e: L.LeafletMouseEvent) => {
      if (this.canRegister()) this.mapClick.emit({ lat: e.latlng.lat, lon: e.latlng.lng });
    });
    this.map = map;
    this.ready.set(true);
  }

  /** Centra la stazione (selezione dal menu laterale, RF-IOT-04). */
  focus(st: Station): void {
    this.map?.flyTo([st.lat, st.lon], Math.max(this.map.getZoom(), 16), { duration: 0.6 });
  }

  focusLand(f: LandFeature): void {
    const bounds = L.latLngBounds(
      f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon] as L.LatLngTuple),
    );
    this.map?.flyToBounds(bounds, { padding: [80, 80], maxZoom: 17, duration: 0.6 });
  }

  invalidate(): void {
    setTimeout(() => this.map?.invalidateSize({ pan: false }), 0);
  }

  private renderLands(fc: LandCollection | null): void {
    this.landLayer.clearLayers();
    if (!fc) return;
    const indigo = CSS('--p-indigo');
    const sage = CSS('--p-sage');
    const sage700 = CSS('--sage-700');
    for (const f of fc.features) {
      const p = f.properties;
      const iot = p.sorgente === 'IOT';
      const latlngs = f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon] as L.LatLngTuple);
      const base: L.PathOptions = iot
        ? { color: sage700, weight: 1.5, fillColor: sage, fillOpacity: 0.24, dashArray: undefined }
        : {
            color: indigo,
            weight: 1.4,
            fillColor: indigo,
            fillOpacity: p.station_id ? 0.08 : 0.04,
            dashArray: '5 4',
          };
      const poly = L.polygon(latlngs, { ...base, bubblingMouseEvents: true });
      poly.on('mouseover', () => poly.setStyle({ fillOpacity: (base.fillOpacity ?? 0) + 0.08 }));
      poly.on('mouseout', () => poly.setStyle({ fillOpacity: base.fillOpacity }));
      if (!p.di_prova) {
        poly.bindTooltip(p.nome, {
          permanent: true,
          direction: 'center',
          className: 'land-label',
          interactive: false,
        });
      }
      poly.addTo(this.landLayer);
    }
    if (!this.fitted && this.map && fc.features.length) {
      const real = fc.features.filter((f) => !f.properties.di_prova);
      const group = L.featureGroup(
        (real.length ? real : fc.features).map((f) =>
          L.polygon(f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon] as L.LatLngTuple)),
        ),
      );
      this.map.fitBounds(group.getBounds(), { padding: [40, 40] });
      this.fitted = true;
    }
  }

  private renderStations(stations: Station[], selected: string | null): void {
    this.stationLayer.clearLayers();
    for (const st of stations) {
      const color = stateMeta(st.stato).color;
      const alert = st.allerte_aperte
        ? `<span class="a ${st.allerta_max === 'critica' ? 'crit' : ''}"></span>`
        : '';
      const icon = L.divIcon({
        className: `st-marker${st.id === selected ? ' sel' : ''}`,
        html: `<span class="d" style="--c:${color}"></span>${alert}`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });
      const m = L.marker([st.lat, st.lon], {
        icon,
        keyboard: true,
        title: st.nome,
        riseOnHover: true,
      });
      m.bindTooltip(st.nome, { direction: 'top', offset: [0, -10], className: 'st-tip' });
      m.on('click', () => this.stationSelect.emit(st.id));
      if (st.id === selected) m.setZIndexOffset(1000);
      m.addTo(this.stationLayer);
    }
  }

  private renderPending(p: { lat: number; lon: number } | null): void {
    this.pendingMarker?.remove();
    this.pendingMarker = undefined;
    if (!p || !this.map) return;
    this.pendingMarker = L.marker([p.lat, p.lon], {
      icon: L.divIcon({
        className: 'pending-marker',
        html: '<span></span>',
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      }),
      interactive: false,
    }).addTo(this.map);
  }

  ngOnDestroy(): void {
    this.map?.remove();
  }
}
