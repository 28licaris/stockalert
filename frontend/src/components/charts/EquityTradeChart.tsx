import { useMemo } from "react";
import type { TradeLeg } from "@/lib/roundTrips";

/**
 * Generic equity-curve chart with optional trade markers and vertical
 * reference lines. Strategy-agnostic: pass any run's equity points and
 * (optionally) its trade legs — entries render as upward ticks under the
 * curve, exits as downward ticks above it, colored by realized P&L.
 * Reused by the Backtest Lab and (via vlines, e.g. go-live) the paper page.
 */
export interface EquityChartPoint {
  t: string;
  equity: number;
}

export interface VLine {
  t: string;
  label?: string;
}

const W = 1000;
const H = 220;

export function EquityTradeChart({
  points,
  trades,
  vlines,
  emptyText = "No equity data.",
}: {
  points: EquityChartPoint[];
  trades?: TradeLeg[];
  vlines?: VLine[];
  emptyText?: string;
}) {
  const model = useMemo(() => {
    if (points.length < 2) return null;
    const ys = points.map((p) => p.equity);
    const min = Math.min(...ys),
      max = Math.max(...ys);
    const range = max - min || 1;
    const step = W / (points.length - 1);
    const dates = points.map((p) => p.t.slice(0, 10));
    const xyFor = (i: number) =>
      [i * step, H - ((points[i].equity - min) / range) * H] as const;
    const coords = points.map((_, i) => xyFor(i));
    const line = coords
      .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
    const area = `${line} L${W},${H} L0,${H} Z`;
    const up = points[points.length - 1].equity >= points[0].equity;

    // Map a timestamp to the nearest equity-point index (points are sorted).
    const idxFor = (t: string) => {
      const d = t.slice(0, 10);
      let lo = 0,
        hi = dates.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (dates[mid] < d) lo = mid + 1;
        else hi = mid;
      }
      return lo;
    };

    const markers = (trades ?? []).map((tr) => {
      const [x, y] = xyFor(idxFor(tr.timestamp));
      const entry = !tr.is_closing;
      return {
        x,
        y,
        entry,
        good: entry || tr.realized_pnl >= 0,
        title: `${entry ? "entry" : "exit"} ${tr.symbol} ${tr.timestamp.slice(0, 10)} @ ${tr.price.toFixed(2)}${
          entry ? "" : ` (P&L ${tr.realized_pnl >= 0 ? "+" : ""}${Math.round(tr.realized_pnl)})`
        }`,
      };
    });

    const refs = (vlines ?? []).map((v) => ({ x: idxFor(v.t) * step, label: v.label }));
    return { line, area, up, markers, refs };
  }, [points, trades, vlines]);

  if (!model) return <div className="py-8 text-center text-xs text-fg-muted">{emptyText}</div>;
  const color = model.up ? "#22c55e" : "#f43f5e";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-48 w-full" preserveAspectRatio="none">
      <path d={model.area} fill={color} opacity={0.12} />
      <path d={model.line} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      {model.refs.map((r, i) => (
        <line
          key={`v${i}`}
          x1={r.x}
          y1={0}
          x2={r.x}
          y2={H}
          stroke="var(--accent, #38bdf8)"
          strokeWidth={2}
          strokeDasharray="6 4"
          vectorEffect="non-scaling-stroke"
        >
          {r.label ? <title>{r.label}</title> : null}
        </line>
      ))}
      {model.markers.map((m, i) => (
        <line
          key={i}
          x1={m.x}
          y1={m.entry ? m.y + 4 : m.y - 4}
          x2={m.x}
          y2={m.entry ? m.y + 16 : m.y - 16}
          stroke={m.entry ? "#38bdf8" : m.good ? "#22c55e" : "#f43f5e"}
          strokeWidth={2.5}
          vectorEffect="non-scaling-stroke"
        >
          <title>{m.title}</title>
        </line>
      ))}
    </svg>
  );
}
