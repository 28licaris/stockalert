/**
 * Moving-average overlay model + timeframe helpers, shared by the chart
 * toolbar, the symbol route, and the query layer. Kept out of the toolbar
 * component file so react-refresh stays happy (component files should only
 * export components).
 */

export type MovingAverageKind = "sma" | "ema" | "wma";

export interface MovingAverageOverlay {
  id: string;
  kind: MovingAverageKind;
  period: number;
  color: string;
  /**
   * Source aggregation the MA is computed over. Undefined (or "chart")
   * means the display interval — the line moves with the chart's zoom
   * (200 bars = 200 candles). A coarser interval like "1d" pins it to a
   * structural level: a true 200-day SMA, stepped onto any chart. Must be
   * coarser-or-equal to the display interval.
   */
  sourceAgg?: string;
}

/** Bar intervals a moving average can be locked to, finest → coarsest. */
export const SOURCE_INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;

const INTERVAL_SECONDS: Record<string, number> = {
  "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
};

/** Source options valid for a display interval: "chart" + coarser-or-equal bars. */
export function sourceOptions(
  displayInterval: string,
): Array<{ value: string; label: string }> {
  const floor = INTERVAL_SECONDS[displayInterval] ?? 0;
  const opts = [{ value: "chart", label: "Chart" }];
  for (const iv of SOURCE_INTERVALS) {
    if ((INTERVAL_SECONDS[iv] ?? 0) > floor) opts.push({ value: iv, label: iv });
  }
  return opts;
}

/** True when an overlay is pinned to a coarser aggregation than the chart. */
export function isCrossTimeframeMA(
  ma: MovingAverageOverlay,
  displayInterval: string,
): boolean {
  if (!ma.sourceAgg || ma.sourceAgg === "chart") return false;
  return (
    (INTERVAL_SECONDS[ma.sourceAgg] ?? 0) > (INTERVAL_SECONDS[displayInterval] ?? 0)
  );
}
