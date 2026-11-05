/**
 * GEX derivations and formatting — the non-component half of the GEX
 * dashboard.
 *
 * Split out of `GexLadder.tsx` so that file exports components only:
 * react-refresh cannot hot-reload a module that mixes component and
 * non-component exports, which silently degrades dev-server HMR for the
 * whole GEX page.
 *
 * `deriveGexLevels` lives here (rather than inside the ladder) because the
 * header cards and the ladder must agree on flip/wall levels — one
 * derivation, two consumers.
 */
import { type GammaExposureSnapshot } from "@/api/queries";

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
