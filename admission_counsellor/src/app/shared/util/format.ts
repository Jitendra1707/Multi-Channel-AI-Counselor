import { Band, Channel, Sentiment } from '../../domain/models';

export function band(p: number): Band { return p < 40 ? 'low' : p <= 70 ? 'med' : 'high'; }

export function fmtInt(n: number): string { return n.toLocaleString('en-IN'); }
export function fmtCurrency(n: number): string {
  if (n >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
  if (n >= 1e5) return '₹' + (n / 1e5).toFixed(1) + ' L';
  return '₹' + n.toLocaleString('en-IN');
}
export function fmtMetric(value: number, format?: 'int' | 'pct' | 'currency'): string {
  switch (format) {
    case 'pct': return value.toFixed(1) + '%';
    case 'currency': return fmtCurrency(value);
    default: return fmtInt(value);
  }
}

// Frozen reference for seed/mock screens (their sample dates are authored
// around this point). Real backend-backed surfaces use the `*Live` variants.
// Parsed the SAME way as seed timestamps (see parseTs) so frozen-reference
// deltas stay consistent regardless of the viewer's timezone.
const NOW = (() => parseTs('2026-06-14T09:30:00').getTime())();

/**
 * Parse a backend/seed timestamp into a Date with the CORRECT instant.
 *
 * The backend (Postgres) emits naive UTC timestamps like
 * "2026-06-20T04:00:00" or "2026-06-20T04:00:00.123456" — a date-time with NO
 * timezone designator. Per the JS spec a date-ONLY string parses as UTC, but a
 * date-TIME string with no offset parses as LOCAL time. In IST (UTC+5:30) that
 * shifts every "last contacted" ~5.5h (shown as ~6h) into the past. We fix it
 * by treating a no-offset date-time as UTC (appending "Z"). Strings that
 * already carry "Z" or a ±HH:MM offset are left untouched.
 */
function parseTs(iso: string): Date {
  const s = (iso || '').trim();
  // Has a time component (T or space + HH:MM) but no zone (Z / +hh:mm / -hh:mm)?
  const hasTime = /\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(s);
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(s);
  if (hasTime && !hasZone) {
    return new Date(s.replace(' ', 'T') + 'Z');
  }
  return new Date(s);
}

function relPast(iso: string, now: number): string {
  const diff = now - parseTs(iso).getTime();
  const m = Math.round(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return parseTs(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}
function relFut(iso: string, now: number): string {
  const diff = parseTs(iso).getTime() - now;
  const m = Math.round(diff / 60000);
  if (m < 0) {
    const a = Math.abs(m);
    if (a < 60) return `${a}m overdue`;
    return `${Math.round(a / 60)}h overdue`;
  }
  if (m < 60) return `in ${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `in ${h}h`;
  return `in ${Math.round(h / 24)}d`;
}

/** Frozen-reference relative time — for seed/mock screens. */
export const relTime = (iso: string): string => relPast(iso, NOW);
export const relFuture = (iso: string): string => relFut(iso, NOW);
/** Live relative time — for real backend-backed data (e.g. CRM leads). */
export const relTimeLive = (iso: string): string => relPast(iso, Date.now());
export const relFutureLive = (iso: string): string => relFut(iso, Date.now());
export function fmtTime(iso: string): string {
  return parseTs(iso).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
}
export function fmtDate(iso: string): string {
  return parseTs(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export const SENTI_LABEL: Record<Sentiment, string> = {
  'very-neg': 'Very negative', 'neg': 'Negative', 'neutral': 'Neutral', 'pos': 'Positive', 'very-pos': 'Very positive',
};
export const SENTI_ICON: Record<Sentiment, string> = {
  'very-neg': 'frown', 'neg': 'frown', 'neutral': 'meh', 'pos': 'smile', 'very-pos': 'smile',
};
export const CHANNEL_LABEL: Record<Channel, string> = {
  voice: 'Voice', whatsapp: 'WhatsApp', email: 'Email', vcon: 'V-Con', web: 'Web', note: 'Note',
};
export const CHANNEL_ICON: Record<Channel, string> = {
  voice: 'phone', whatsapp: 'message-circle', email: 'mail', vcon: 'video', web: 'globe', note: 'edit',
};
