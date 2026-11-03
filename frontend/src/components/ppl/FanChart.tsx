import { useEffect, useMemo, useRef, useState } from "react";
import type { PplLevel, PplSnapshot } from "@/api/queries";

/**
 * Posterior-predictive fan — a few hundred simulated paths drawn on a canvas.
 *
 * Canvas rather than lightweight-charts: 300 overlapping line series is exactly
 * the workload a candlestick library is worst at, and the fan needs no axis
 * interaction. One 2D context, one pass, no DOM per path.
 *
 * Paths are colored by TERMINAL value on a cyan → amber → magenta ramp. That is
 * a pure visualization choice with no modeling meaning: it makes the shape of
 * the distribution legible at a glance, nothing more.
 */

const PAD = { top: 24, right: 96, bottom: 36, left: 64 };
const AXIS_TICKS = 6;

/** Cyan (high terminal) → amber (middle) → magenta (low). `t` in [0, 1]. */
function rampColor(t: number, alpha: number): string {
  const stops: [number, number, number][] = [
    [232, 74, 138], // magenta — lowest terminal
    [222, 138, 62], // amber
    [214, 198, 92], // yellow-green
    [92, 200, 190], // teal
    [86, 178, 232], // cyan — highest terminal
  ];
  const scaled = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const [r1, g1, b1] = stops[i];
  const [r2, g2, b2] = stops[i + 1];
  const r = Math.round(r1 + (r2 - r1) * f);
  const g = Math.round(g1 + (g2 - g1) * f);
  const b = Math.round(b1 + (b2 - b1) * f);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export interface FanChartProps {
  snapshot: PplSnapshot;
  height?: number;
  /** Level the user is hovering in the legend — drawn thicker. */
  highlighted?: PplLevel | null;
}

export function FanChart({ snapshot, height = 520, highlighted }: FanChartProps) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const { paths, levels, spot } = snapshot;

  // Price range across every path AND every level, so nothing is clipped.
  const range = useMemo(() => {
    let lo = spot;
    let hi = spot;
    for (const path of paths) {
      for (const p of path) {
        if (p < lo) lo = p;
        if (p > hi) hi = p;
      }
    }
    for (const l of levels) {
      if (l.price < lo) lo = l.price;
      if (l.price > hi) hi = l.price;
    }
    const padding = (hi - lo) * 0.04 || spot * 0.01;
    return { lo: lo - padding, hi: hi + padding };
  }, [paths, levels, spot]);

  // Terminal values sorted once, so each path's color rank is a lookup.
  const terminalRank = useMemo(() => {
    const terminals = paths.map((p) => p[p.length - 1]);
    const sorted = [...terminals].sort((a, b) => a - b);
    const lo = sorted[0];
    const hi = sorted[sorted.length - 1];
    const span = hi - lo || 1;
    return terminals.map((t) => (t - lo) / span);
  }, [paths]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0 || paths.length === 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const plotW = width - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    if (plotW <= 0 || plotH <= 0) return;

    const steps = paths[0].length - 1;
    const x = (i: number) => PAD.left + (i / steps) * plotW;
    const y = (price: number) =>
      PAD.top + (1 - (price - range.lo) / (range.hi - range.lo)) * plotH;

    // ── price axis ───────────────────────────────────────────────────
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= AXIS_TICKS; i++) {
      const price = range.lo + ((range.hi - range.lo) * i) / AXIS_TICKS;
      const py = y(price);
      ctx.strokeStyle = "rgba(148, 163, 184, 0.10)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD.left, py);
      ctx.lineTo(PAD.left + plotW, py);
      ctx.stroke();

      ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
      ctx.textAlign = "right";
      ctx.fillText(price.toFixed(2), PAD.left - 8, py);
    }

    // ── paths ────────────────────────────────────────────────────────
    // Thin, translucent strokes: density is the message, so overlap should
    // accumulate into brightness rather than each line shouting.
    const alpha = Math.max(0.16, Math.min(0.5, 60 / paths.length));
    ctx.lineWidth = 0.7;
    ctx.lineJoin = "round";
    paths.forEach((path, idx) => {
      ctx.strokeStyle = rampColor(terminalRank[idx], alpha);
      ctx.beginPath();
      ctx.moveTo(x(0), y(path[0]));
      for (let i = 1; i < path.length; i++) ctx.lineTo(x(i), y(path[i]));
      ctx.stroke();
    });

    // ── anchor ───────────────────────────────────────────────────────
    const spotY = y(spot);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(PAD.left, spotY);
    ctx.lineTo(PAD.left + plotW, spotY);
    ctx.stroke();

    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.textAlign = "right";
    ctx.font = "bold 11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText(spot.toFixed(2), PAD.left - 8, spotY);

    // ── levels ───────────────────────────────────────────────────────
    for (const level of levels) {
      const ly = y(level.price);
      const isUp = level.side === "up";
      const base = isUp ? "34, 197, 94" : "239, 68, 68";
      const hot = highlighted?.price === level.price;

      // Suppressed levels are drawn dashed and dim — present, visibly
      // discounted, never silently removed.
      ctx.setLineDash(level.suppressed ? [4, 4] : []);
      ctx.strokeStyle = `rgba(${base}, ${level.suppressed ? 0.4 : hot ? 1 : 0.85})`;
      ctx.lineWidth = hot ? 2.4 : level.rank === "inner" ? 1.6 : 1.2;
      ctx.beginPath();
      ctx.moveTo(PAD.left, ly);
      ctx.lineTo(PAD.left + plotW, ly);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = `rgba(${base}, ${level.suppressed ? 0.55 : 1})`;
      ctx.textAlign = "left";
      ctx.font = `bold 11px ui-monospace, SFMono-Regular, Menlo, monospace`;
      ctx.fillText(level.price.toFixed(2), PAD.left + plotW + 8, ly);
      ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillStyle = `rgba(${base}, 0.65)`;
      ctx.fillText(
        level.quantile !== null ? `q${Math.round(level.quantile * 100)}` : "mode",
        PAD.left + plotW + 8,
        ly + 12,
      );
    }

    // ── footer ───────────────────────────────────────────────────────
    ctx.fillStyle = "rgba(148, 163, 184, 0.6)";
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "left";
    ctx.fillText("now", PAD.left, height - PAD.bottom / 2);
    ctx.textAlign = "right";
    ctx.fillText(
      `+${snapshot.horizon_bars} × ${snapshot.interval}`,
      PAD.left + plotW,
      height - PAD.bottom / 2,
    );
  }, [width, height, paths, levels, spot, range, terminalRank, highlighted, snapshot]);

  return (
    <div ref={wrapRef} className="w-full">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Posterior predictive fan for ${snapshot.symbol} at ${snapshot.interval}, ${paths.length} simulated paths over ${snapshot.horizon_bars} bars`}
        className="w-full rounded-lg bg-[#080b12]"
      />
    </div>
  );
}
