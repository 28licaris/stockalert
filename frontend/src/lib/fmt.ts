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

/** Short timestamp for table cells: "13:42:05". */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString();
}

/** ET-formatted timestamp ("MM/DD HH:mm ET") for tables that care about
 * the trading-day boundary. Uses `America/New_York` so DST auto-applies.
 * String, not Date, because table rows compare visual values. */
export function fmtTimeET(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/**
 * True iff the ISO timestamp falls within the US equity regular
 * trading session (09:30–16:00 America/New_York), Mon–Fri.
 *
 * Uses `Intl.DateTimeFormat` to extract ET parts so DST is handled
 * by the browser's timezone database — no hand-rolled offset math.
 * Cheap to call per row (~µs); BarsTable filter calls this once
 * per bar.
 */
export function isRegularSessionET(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  // Intl returns the date's components in the target TZ.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (type: string) =>
    parts.find((p) => p.type === type)?.value ?? "";
  const weekday = get("weekday");
  if (weekday === "Sat" || weekday === "Sun") return false;
  const hour = parseInt(get("hour"), 10);
  const minute = parseInt(get("minute"), 10);
  if (Number.isNaN(hour) || Number.isNaN(minute)) return false;
  const minutesFromMidnight = hour * 60 + minute;
  return minutesFromMidnight >= 9 * 60 + 30 && minutesFromMidnight < 16 * 60;
}

/**
 * ET trading-day key for a timestamp — "YYYY-MM-DD (Day)".
 * Two timestamps that share the same key belong to the same
 * trading day; the bars table uses this to render day-boundary
 * dividers so the overnight gap reads as a session change, not
 * a data gap.
 */
export function tradingDayET(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "short",
    day: "2-digit",
    weekday: "short",
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("month")} ${get("day")} ${get("year")}`;
}
