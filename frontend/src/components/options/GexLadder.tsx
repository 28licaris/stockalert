import { useEffect, useMemo, useRef } from "react";
import { Star } from "lucide-react";
import { type GammaExposureSnapshot } from "@/api/queries";
import { cn } from "@/lib/utils";

/**
 * Dealer-positioning strike ladder (GEX dashboard main panel).
 *
 * One row per strike: signed net-GEX bar (emerald = positive dealer gamma,
 * rose = negative), value with a short drift trail (last polls), and
 * structure tags — spot row (★), gamma flip (γ FLIP), call/put walls.
 * Derivations (flip = zero-cross nearest spot; walls = extreme +/- rows)
 * live in `deriveGexLevels` so the header cards share them.
 */

export function fmtGex(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "+";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

export interface GexLevels {
  spot: number | null;
  flipStrike: number | null;
  callWall: number | null;
  putWall: number | null;
  strikes: StrikeRow[];
}

export interface StrikeRow {
  strike: number;
  gex: number;
  openInterest: number | null;
}

function rowGex(r: GammaExposureSnapshot): number {
  return r.net_gamma_exposure ?? r.gamma_exposure;
}

export function deriveGexLevels(
  rows: GammaExposureSnapshot[],
  windowPct = 0.06,
): GexLevels {
  const strikeRows = rows
    .filter((r) => r.aggregation_level === "strike" && r.strike != null)
    .map((r) => ({
      strike: r.strike as number,
      gex: rowGex(r),
      openInterest: r.open_interest ?? null,
    }))
    .sort((a, b) => b.strike - a.strike);
  const spot = rows.find((r) => r.underlying_price > 0)?.underlying_price ?? null;

  // gamma flip: the sign change closest to spot (scanning down the ladder)
  let flipStrike: number | null = null;
  if (spot != null) {
    let bestDist = Infinity;
    for (let i = 1; i < strikeRows.length; i++) {
      const hi = strikeRows[i - 1];
      const lo = strikeRows[i];
      if (hi.gex >= 0 !== lo.gex >= 0) {
        const level = lo.gex >= 0 ? hi.strike : lo.strike;
        const negSide = hi.gex < 0 ? hi.strike : lo.strike;
        const dist = Math.abs(((level + negSide) / 2) - spot);
        if (dist < bestDist) {
          bestDist = dist;
          flipStrike = negSide;
        }
      }
    }
  }

  const inWindow =
    spot == null
      ? strikeRows
      : strikeRows.filter((r) => Math.abs(r.strike - spot) <= spot * windowPct);
  const visible = inWindow.length >= 8 ? inWindow : strikeRows;
  const positives = visible.filter((r) => r.gex > 0);
  const negatives = visible.filter((r) => r.gex < 0);
  const callWall = positives.length
    ? positives.reduce((a, b) => (b.gex > a.gex ? b : a)).strike
    : null;
  const putWall = negatives.length
    ? negatives.reduce((a, b) => (b.gex < a.gex ? b : a)).strike
    : null;

  return { spot, flipStrike, callWall, putWall, strikes: visible };
}

interface GexLadderProps {
  levels: GexLevels;
}

export function GexLadder({ levels }: GexLadderProps) {
  const { spot, flipStrike, callWall, putWall, strikes } = levels;
  const maxAbs = Math.max(1, ...strikes.map((r) => Math.abs(r.gex)));

  // drift trail: remember the last 4 polled values per strike
  const trailRef = useRef<Map<number, number[]>>(new Map());
  useEffect(() => {
    for (const r of strikes) {
      const t = trailRef.current.get(r.strike) ?? [];
      if (t.length === 0 || t[t.length - 1] !== r.gex) {
        trailRef.current.set(r.strike, [...t, r.gex].slice(-4));
      }
    }
  }, [strikes]);

  const spotRowStrike = useMemo(() => {
    if (spot == null || strikes.length === 0) return null;
    return strikes.reduce((a, b) =>
      Math.abs(b.strike - spot) < Math.abs(a.strike - spot) ? b : a,
    ).strike;
  }, [spot, strikes]);

  if (strikes.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted-foreground">
        No strike-level GEX rows for this symbol yet — take a chain snapshot
        first (Options page) or wait for the next scheduled pull.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="grid grid-cols-[72px_1fr_220px_110px] items-center gap-3 border-b border-border px-4 py-2 text-[10px] uppercase tracking-widest text-muted-foreground">
        <span>Strike</span>
        <span>Dealer gamma</span>
        <span className="text-right">GEX · drift</span>
        <span className="text-right">Structure</span>
      </div>
      <div className="divide-y divide-border/40">
        {strikes.map((r) => {
          const isSpot = r.strike === spotRowStrike;
          const isFlip = flipStrike != null && r.strike === flipStrike;
          const isCallWall = callWall != null && r.strike === callWall;
          const isPutWall = putWall != null && r.strike === putWall;
          const pct = Math.abs(r.gex) / maxAbs;
          const trail = trailRef.current.get(r.strike) ?? [r.gex];
          return (
            <div
              key={r.strike}
              className={cn(
                "grid grid-cols-[72px_1fr_220px_110px] items-center gap-3 px-4 py-1.5",
                isSpot && "bg-amber-500/10",
                isFlip && "bg-rose-500/5",
                (isCallWall || isPutWall) && "bg-emerald-500/5",
              )}
            >
              <span
                className={cn(
                  "font-mono text-xs",
                  r.gex >= 0 ? "text-emerald-300" : "text-rose-300",
                  isSpot && "text-amber-300",
                )}
              >
                ${r.strike % 1 === 0 ? r.strike.toFixed(0) : r.strike}
              </span>
              <div className="relative h-3 rounded-sm bg-muted/40">
                <div
                  className={cn(
                    "absolute inset-y-0 left-0 rounded-sm",
                    r.gex >= 0 ? "bg-emerald-500/80" : "bg-rose-500/80",
                    (isCallWall || isPutWall) && "shadow-[0_0_12px] shadow-emerald-500/40",
                  )}
                  style={{ width: `${Math.max(2, pct * 100)}%` }}
                />
                {isSpot && spot != null && (
                  <span className="absolute -top-0.5 right-2 flex items-center gap-1 font-mono text-[11px] text-amber-300">
                    <Star className="h-3 w-3 fill-amber-300" /> ${spot.toFixed(2)}
                  </span>
                )}
              </div>
              <div className="flex items-center justify-end gap-2 font-mono text-[11px]">
                <span className={r.gex >= 0 ? "text-emerald-300" : "text-rose-300"}>
                  {fmtGex(r.gex)}
                </span>
                <span className="text-muted-foreground/60">
                  {trail
                    .slice(0, -1)
                    .reverse()
                    .map((v, i) => (
                      <span key={i} className="ml-1">
                        {fmtGex(v)}
                      </span>
                    ))}
                </span>
              </div>
              <div className="flex justify-end gap-1">
                {isSpot && <Tag tone="amber">spot</Tag>}
                {isFlip && <Tag tone="rose">γ flip</Tag>}
                {isCallWall && <Tag tone="emerald">call wall</Tag>}
                {isPutWall && <Tag tone="rose">put wall</Tag>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Tag({
  tone,
  children,
}: {
  tone: "amber" | "rose" | "emerald";
  children: React.ReactNode;
}) {
  const cls = {
    amber: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    rose: "border-rose-500/40 bg-rose-500/10 text-rose-300",
    emerald: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  }[tone];
  return (
    <span
      className={cn(
        "rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider",
        cls,
      )}
    >
      {children}
    </span>
  );
}
