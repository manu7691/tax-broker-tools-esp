const eurFormatter = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
const sharesFormatter = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 6 });

export function fmt(value: number | null | undefined): string {
  if (value == null) return '—';
  return eurFormatter.format(value);
}

export function fmtShares(value: number | null | undefined): string {
  if (value == null) return '—';
  return sharesFormatter.format(value);
}

export function diffColor(diff: number | null): string {
  if (diff == null) return 'text-zinc-400';
  if (diff > 0.5) return 'text-red-400';
  if (diff < -0.5) return 'text-green-400';
  return 'text-zinc-400';
}
