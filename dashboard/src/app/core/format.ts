const nf = new Map<number, Intl.NumberFormat>();

export function fmtNum(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  let f = nf.get(decimals);
  if (!f) {
    f = new Intl.NumberFormat('it-IT', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    nf.set(decimals, f);
  }
  return f.format(v);
}

export function fmtCount(v: number | null | undefined): string {
  return fmtNum(v ?? 0, 0);
}

const timeFmt = new Intl.DateTimeFormat('it-IT', {
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'Europe/Rome',
});
const timeSecFmt = new Intl.DateTimeFormat('it-IT', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  timeZone: 'Europe/Rome',
});
const dayFmt = new Intl.DateTimeFormat('it-IT', {
  weekday: 'short',
  day: '2-digit',
  month: 'short',
  timeZone: 'Europe/Rome',
});
const dateTimeFmt = new Intl.DateTimeFormat('it-IT', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'Europe/Rome',
});

export const fmtTime = (t: number | string) => timeFmt.format(new Date(t));
export const fmtTimeSec = (t: number | string) => timeSecFmt.format(new Date(t));
export const fmtDay = (t: number | string) => dayFmt.format(new Date(t)).replace('.', '');
export const fmtDateTime = (t: number | string) => dateTimeFmt.format(new Date(t));

/** "adesso", "40 s fa", "12 min fa", "3 h fa", "2 g fa" rispetto all'ora simulata. */
export function fmtAgo(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return 'mai';
  const s = Math.max(0, (nowMs - Date.parse(iso)) / 1000);
  if (s < 20) return 'adesso';
  if (s < 90) return `${Math.round(s)} s fa`;
  if (s < 3600) return `${Math.round(s / 60)} min fa`;
  if (s < 86400 * 2) return `${fmtNum(s / 3600, s < 36000 ? 1 : 0)} h fa`;
  return `${Math.round(s / 86400)} g fa`;
}

export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${seconds / 60} min`;
  return `${seconds / 3600} h`;
}

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
export const cardinal = (deg: number) =>
  CARDINALS[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
