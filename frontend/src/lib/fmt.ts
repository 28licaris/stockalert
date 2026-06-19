/**
 * Display formatters. All zero-dependency and pure so they can be
 * called from anywhere (cells, hover detail, command palette).
 */

export function fmtPrice(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtPct(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(decimals)}%`;
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

/** Compact volume formatter: 12,345,678 → "12.3M". */
export function fmtVol(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e9) return (value / 1e9).toFixed(1) + "B";
  if (abs >= 1e6) return (value / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return (value / 1e3).toFixed(1) + "K";
  return value.toString();
}

export function fmtLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/** Relative time: "12s ago", "3m ago", "2h ago". */
export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const secs = Math.max(0, Math.round((now - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

/**
 * Short timestamp for table cells: "13:42:05".
 *
 * `timeZone` is an IANA zone (e.g. "America/New_York") or `undefined`
 * for the viewer's local zone. Bar timestamps are UTC instants, so this
 * is a pure display conversion. See lib/timezone.ts.
 */
export function fmtTime(
  iso: string | null | undefined,
  timeZone?: string,
): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(undefined, timeZone ? { timeZone } : undefined);
}

/**
 * Calendar date for a bar. Used for daily/1d bars where a time-of-day
 * label is meaningless. `timeZone` follows the same display setting as
 * the chart axis (see lib/timezone.ts); `undefined` = viewer's local
 * zone.
 */
export function fmtDate(
  iso: string | null | undefined,
  timeZone?: string,
): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    timeZone,
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}
