import { useMemo } from "react";
import { fmtGex } from "@/components/options/GexLadder";

/**
 * GEX regime history: price line over a signed net-GEX histogram sharing
 * one time axis. The regime view — persistent negative-gamma episodes
 * (rose) mark amplified/high-vol stretches; positive (emerald) mark damped
 * ones. Pure SVG, same idiom as EquityTradeChart.
 */
export interface GexHistoryPoint {
  date: string; // YYYY-MM-DD
  gex: number;
  close: number | null;
}

const W = 1000;
const H_PRICE = 130;
const GAP = 14;
const H_GEX = 90;
const H = H_PRICE + GAP + H_GEX;

export function GexHistoryChart({
  points,
  emptyText = "No GEX history yet for this symbol.",
}: {
  points: GexHistoryPoint[];
  emptyText?: string;
}) {
  const model = useMemo(() => {
    const pts = points.filter((p) => Number.isFinite(p.gex));
    if (pts.length < 10) return null;
    const step = W / pts.length;

    const closes = pts.map((p) => p.close).filter((c): c is number => c != null);
    const cMin = Math.min(...closes), cMax = Math.max(...closes);
    const cRange = cMax - cMin || 1;
    const priceLine = pts
      .map((p, i) => {
        if (p.close == null) return null;
        const x = i * step + step / 2;
        const y = H_PRICE - ((p.close - cMin) / cRange) * H_PRICE;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .filter(Boolean)
      .map((xy, i) => `${i === 0 ? "M" : "L"}${xy}`)
      .join(" ");

    const gAbsMax = Math.max(...pts.map((p) => Math.abs(p.gex))) || 1;
    const zeroY = H_PRICE + GAP + H_GEX / 2;
    const bars = pts.map((p, i) => {
      const h = (Math.abs(p.gex) / gAbsMax) * (H_GEX / 2);
      return {
        x: i * step,
        y: p.gex >= 0 ? zeroY - h : zeroY,
        h: Math.max(h, 0.5),
        pos: p.gex >= 0,
      };
    });

    // year tick marks
    const ticks: { x: number; label: string }[] = [];
    let lastYear = "";
    pts.forEach((p, i) => {
      const yr = p.date.slice(0, 4);
      if (yr !== lastYear) {
        ticks.push({ x: i * step, label: yr });
        lastYear = yr;
      }
    });

    const latest = pts[pts.length - 1];
    return { pts, step, priceLine, bars, zeroY, ticks, latest };
  }, [points]);

  if (!model) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center text-sm text-muted-foreground">
        {emptyText}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-widest text-muted-foreground">
          Net GEX regime history · price above, signed dealer gamma below
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          latest {fmtGex(model.latest.gex)} · {model.latest.date}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
           aria-label="Net GEX history vs price">
        {model.ticks.map((t) => (
          <g key={t.label}>
            <line x1={t.x} y1={0} x2={t.x} y2={H} className="stroke-border" strokeWidth={0.5} />
            <text x={t.x + 3} y={H - 3} className="fill-muted-foreground" fontSize={9}>
              {t.label}
            </text>
          </g>
        ))}
        <path d={model.priceLine} fill="none" strokeWidth={1.3}
              className="stroke-sky-400" />
        <line x1={0} y1={model.zeroY} x2={W} y2={model.zeroY}
              className="stroke-border" strokeWidth={0.75} />
        {model.bars.map((b, i) => (
          <rect key={i} x={b.x} y={b.y} width={Math.max(model.step - 0.3, 0.7)} height={b.h}
                className={b.pos ? "fill-emerald-500/70" : "fill-rose-500/70"} />
        ))}
      </svg>
    </div>
  );
}
