import { StatoStazione } from './models/iot.models';

export interface StateMeta {
  label: string;
  color: string; // variabile CSS
}

/** Colori dei marker del §10.3, tradotti nella palette (verde → salvia, blu → teal). */
export const STATES: Record<Exclude<StatoStazione, 'DISMESSA'>, StateMeta> = {
  ATTIVO_COMPLETO: { label: 'Attivo completo', color: 'var(--st-attivo)' },
  DEGRADATA: { label: 'Degradata', color: 'var(--st-degradata)' },
  OFFLINE: { label: 'Offline', color: 'var(--st-offline)' },
  REGISTRATA: { label: 'Registrata', color: 'var(--st-registrata)' },
};

export const stateMeta = (s: StatoStazione): StateMeta =>
  s === 'DISMESSA' ? { label: 'Dismessa', color: 'var(--ink-3)' } : STATES[s];

export interface ChannelMeta {
  key: string; // sensor_type[@profondità]
  label: string;
  short: string; // sigla per i chip del simulatore
  group: 'Suolo' | 'Aria' | 'Radiazione' | 'Acqua' | 'Vento' | 'Integrità';
  decimals: number;
  unit: string; // unità mostrata
}

const C = (
  key: string,
  label: string,
  short: string,
  group: ChannelMeta['group'],
  decimals: number,
  unit: string,
): ChannelMeta => ({ key, label, short, group, decimals, unit });

export const CHANNELS: ChannelMeta[] = [
  C('soil_moisture@15', 'Umidità suolo 10–20 cm', 'UMS15', 'Suolo', 1, '%vol'),
  C('soil_moisture@35', 'Umidità suolo 30–40 cm', 'UMS35', 'Suolo', 1, '%vol'),
  C('soil_temperature@15', 'Temperatura suolo', 'TS', 'Suolo', 1, '°C'),
  C('soil_ec@15', 'Conducibilità elettrica', 'EC', 'Suolo', 2, 'dS/m'),
  C('air_temperature', 'Temperatura aria', 'TA', 'Aria', 1, '°C'),
  C('air_humidity', 'Umidità relativa', 'UR', 'Aria', 0, '%'),
  C('barometric_pressure', 'Pressione', 'P', 'Aria', 1, 'hPa'),
  C('solar_radiation', 'Radiazione globale', 'RAD', 'Radiazione', 0, 'W/m²'),
  C('uv_irradiance', 'Irraggiamento UV', 'UV', 'Radiazione', 1, 'W/m²'),
  C('illuminance', 'Luminosità', 'LUX', 'Radiazione', 0, 'lx'),
  C('rain', 'Pioggia nell’intervallo', 'PLV', 'Acqua', 1, 'mm'),
  C('leaf_wetness', 'Bagnatura fogliare', 'BF', 'Acqua', 0, '%'),
  C('wind_speed', 'Velocità vento', 'VV', 'Vento', 1, 'm/s'),
  C('wind_direction', 'Direzione vento', 'DV', 'Vento', 0, '°'),
  C('gps', 'Posizione GPS', 'GPS', 'Integrità', 5, '°'),
  C('accelerometer', 'Accelerometro', 'ACC', 'Integrità', 3, 'g'),
];

export const channelKey = (type: string, depth: number | null) =>
  depth !== null ? `${type}@${depth}` : type;

const BY_KEY = new Map(CHANNELS.map((c) => [c.key, c]));
export const channelMeta = (type: string, depth: number | null): ChannelMeta =>
  BY_KEY.get(channelKey(type, depth)) ?? C(channelKey(type, depth), type, type, 'Aria', 1, '');

export const GROUPS: ChannelMeta['group'][] = [
  'Suolo',
  'Aria',
  'Radiazione',
  'Acqua',
  'Vento',
  'Integrità',
];

export const ALERT_LABELS: Record<string, string> = {
  stress_idrico: 'Stress idrico',
  rischio_gelata: 'Rischio di gelata',
  stress_termico: 'Stress termico',
  stazione_spostata: 'Stazione spostata',
  stazione_inclinata: 'Stazione inclinata',
  urto: 'Urto',
  sensore_sospetto: 'Sensore sospetto',
};

export const CADENCES = [30, 60, 120, 180, 300, 600, 900, 1800, 3600];
