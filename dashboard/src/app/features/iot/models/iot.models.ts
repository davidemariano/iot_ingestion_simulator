export type StatoStazione = 'REGISTRATA' | 'ATTIVO_COMPLETO' | 'DEGRADATA' | 'OFFLINE' | 'DISMESSA';
export type Sorgente = 'IOT' | 'SATELLITE';
export type Severita = 'attenzione' | 'critica';

export interface Station {
  id: string;
  nome: string;
  land_id: string;
  land_nome: string;
  lat: number;
  lon: number;
  gateway_id: string;
  stato: StatoStazione;
  sorgente: Sorgente;
  cadenza_s: number;
  registrata_il: string;
  ultimo_contatto_il: string | null;
  allerte_aperte: number;
  allerta_max: Severita | null;
}

export interface Sensor {
  id: string;
  sensor_type: string;
  device_uid: string;
  profondita_cm: number | null;
  unit: string;
  descrizione: string;
  attivo: boolean;
  ultima_misura_il: string | null;
}

export interface StationDetail extends Station {
  sensors: Sensor[];
  theta_fc: number;
  theta_wp: number;
}

export interface GpsValue {
  lat: number;
  lon: number;
  alt_m: number | null;
  hdop: number | null;
  sats: number | null;
}

export interface MotionValue {
  ax: number;
  ay: number;
  az: number;
  inclinazione_deg: number;
}

export interface LatestChannel {
  sensor_id: string;
  sensor_type: string;
  device_uid: string;
  profondita_cm: number | null;
  unit: string;
  descrizione: string;
  measured_at: string | null;
  value: number | GpsValue | MotionValue | null;
  quality: number;
  presente: boolean;
}

export interface IndicatorValue {
  valore: number;
  calcolato_il: string;
  dettagli: Record<string, number> | null;
}

export interface Latest {
  station_id: string;
  generato_il: string;
  canali: LatestChannel[];
  indicatori: Partial<Record<string, IndicatorValue>>;
}

export interface Alert {
  id: number;
  tipo: string;
  severita: Severita;
  messaggio: string;
  aperta_il: string;
  chiusa_il: string | null;
}

export interface StatusTransition {
  da: StatoStazione | null;
  a: StatoStazione;
  motivo: string;
  avvenuto_il: string;
}

export interface SeriesPoint {
  t: string;
  v: number;
  q?: number;
  min?: number;
  max?: number;
}

export interface Series {
  sensor_type: string;
  profondita_cm: number | null;
  unit: string;
  agg: 'raw' | 'hour' | 'day';
  aggregazione: string;
  punti: SeriesPoint[];
}

export interface Sparkline {
  sensor_id: string;
  v: (number | null)[];
}

export interface LandProps {
  id: string;
  nome: string;
  coltura: string;
  area_ha: number;
  di_prova: boolean;
  theta_fc: number;
  theta_wp: number;
  sorgente: Sorgente;
  station_id: string | null;
}

export interface LandFeature {
  type: 'Feature';
  geometry: { type: 'Polygon'; coordinates: number[][][] };
  properties: LandProps;
}

export interface LandCollection {
  type: 'FeatureCollection';
  features: LandFeature[];
}

export interface StationFaults {
  offline: boolean;
  canali_silenziati: string[];
  canali_bloccati: string[];
  picchi_pct: number;
  spostata_m: number;
  inclinata_deg: number;
}
