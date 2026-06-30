/**
 * Indicator catalog — the single source of truth the chart toolbar and
 * the `OhlcvChart` renderer both read from.
 *
 * Registry parity: `id` values match the backend indicator registry
 * (`app/indicators/registry.py`) so a selection maps 1:1 to a
 * `/api/v1/indicators/chart-data` spec. The toolbar can send backend
 * `params` for configurable indicators (for example moving-average
 * `period`), while unchanged indicators keep their backend defaults.
 *
 * Two render targets:
 *   - overlay     → drawn on the price pane (moving averages, bands)
 *   - oscillator  → drawn in its OWN pane below price (RSI, MACD, …),
 *                   because their value ranges (0–100, ±, ATR price)
 *                   can't share the price axis.
 *
 * Multi-output indicators decompose server-side into component series
 * (`bollinger_upper`, `macd_signal`, `stochastic_k`, …). The component
 * name's prefix is its parent id — see `parentIndicatorId`.
 */

export type IndicatorKind = "overlay" | "oscillator";

export interface IndicatorDef {
  /** Registry name — matches the backend (`sma`, `bollinger`, …). */
  id: string;
  /** Short label for the toolbar / chips. */
  label: string;
  kind: IndicatorKind;
  /** One-liner shown in the picker. */
  description: string;
}

/** Catalog order = display order in the picker. */
export const INDICATOR_CATALOG: readonly IndicatorDef[] = [
  // Overlays (price pane)
  { id: "bollinger", label: "Bollinger Bands", kind: "overlay", description: "Volatility bands" },
  // Oscillators (own pane)
  { id: "rsi", label: "RSI", kind: "oscillator", description: "Relative strength index" },
  { id: "macd", label: "MACD", kind: "oscillator", description: "Moving-average convergence/divergence" },
  { id: "stochastic", label: "Stochastic", kind: "oscillator", description: "Stochastic oscillator" },
  { id: "tsi", label: "TSI", kind: "oscillator", description: "True strength index" },
  { id: "atr", label: "ATR", kind: "oscillator", description: "Average true range" },
] as const;

export const INDICATOR_BY_ID: Record<string, IndicatorDef> = Object.fromEntries(
  INDICATOR_CATALOG.map((d) => [d.id, d]),
);

/**
 * Parent indicator id for a component series name.
 * `"macd_signal"` → `"macd"`, `"bollinger_upper"` → `"bollinger"`,
 * `"sma"` → `"sma"`. The backend prefixes every multi-output component
 * with its parent id, so the first underscore-delimited token is the
 * parent for all current indicators.
 */
export function parentIndicatorId(seriesName: string): string {
  return seriesName.split("_")[0];
}

// ─────────────────────────────────────────────────────────────────────
// Per-component render config. Keyed by the exact series name the
// backend emits. Lightweight Charts accepts hex colors directly, so —
// unlike the candle palette — these need no HSL translation.

export interface ComponentRender {
  type: "line" | "histogram";
  color: string;
  lineWidth?: 1 | 2 | 3 | 4;
  /** Dashed for the outer/secondary lines (bands, signal). */
  dashed?: boolean;
}

const COMPONENT_RENDER: Record<string, ComponentRender | null> = {
  // Moving averages
  sma: { type: "line", color: "#f59e0b", lineWidth: 2 },
  ema: { type: "line", color: "#38bdf8", lineWidth: 2 },
  wma: { type: "line", color: "#c084fc", lineWidth: 2 },
  // Bollinger — middle solid, bands dashed/muted; derived stats not drawn.
  bollinger_upper: { type: "line", color: "#64748b", lineWidth: 1, dashed: true },
  bollinger_middle: { type: "line", color: "#94a3b8", lineWidth: 1 },
  bollinger_lower: { type: "line", color: "#64748b", lineWidth: 1, dashed: true },
  bollinger_bandwidth: null,
  bollinger_percent_b: null,
  // RSI
  rsi: { type: "line", color: "#22d3ee", lineWidth: 2 },
  // MACD
  macd: { type: "line", color: "#38bdf8", lineWidth: 2 },
  macd_signal: { type: "line", color: "#f59e0b", lineWidth: 1, dashed: true },
  macd_histogram: { type: "histogram", color: "#64748b" },
  // Stochastic
  stochastic_k: { type: "line", color: "#22d3ee", lineWidth: 2 },
  stochastic_d: { type: "line", color: "#f59e0b", lineWidth: 1, dashed: true },
  // TSI / ATR
  tsi: { type: "line", color: "#c084fc", lineWidth: 2 },
  atr: { type: "line", color: "#eab308", lineWidth: 2 },
};

/**
 * Render config for a component series, or `null` to skip it (derived
 * stats we don't chart). Unknown names fall back to a neutral line so a
 * future backend component still renders rather than vanishing silently.
 */
export function componentRender(seriesName: string): ComponentRender | null {
  if (seriesName.startsWith("ma_")) {
    return { type: "line", color: "#f59e0b", lineWidth: 2 };
  }
  if (seriesName in COMPONENT_RENDER) return COMPONENT_RENDER[seriesName];
  return { type: "line", color: "#94a3b8", lineWidth: 1 };
}

/**
 * Horizontal guide lines per oscillator parent (e.g. RSI 30/70).
 * Drawn as price lines on the pane's primary series.
 */
export const OSCILLATOR_GUIDES: Record<string, number[]> = {
  rsi: [30, 70],
  stochastic: [20, 80],
  macd: [0],
};

/** A short chip/legend label for a parent indicator id. */
export function indicatorChipLabel(id: string): string {
  return INDICATOR_BY_ID[id]?.label ?? id.toUpperCase();
}
