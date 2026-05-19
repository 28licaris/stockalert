import { useEffect, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Signal } from "@/api/queries";
import { signalDirection } from "@/api/queries";

interface OhlcvChartProps {
  bars: ReadonlyArray<Bar>;
  signals?: ReadonlyArray<Signal>;
  /**
   * If provided, fixes the chart at that pixel height. Omit to let
   * the chart fill its container — pair with a parent that has a
   * concrete height (e.g. `className="flex-1"` inside a flex column).
   */
  height?: number;
}

/**
 * Lightweight Charts wrapper. Encapsulates:
 *   - chart lifecycle (create / resize / dispose)
 *   - data adaptation from our `OhlcvBar` shape to LWC's expected shape
 *   - signal markers (bullish / bearish; regular vs hidden)
 *
 * Re-renders only update the data series; we never tear down the chart
 * for prop changes (would lose pan/zoom state).
 *
 * Color note: LWC ships its own color parser that does NOT accept the
 * modern space-separated `hsl(h s% l%)` syntax — only the legacy
 * comma form. Tailwind stores our tokens as space-separated triples
 * so they work with `<alpha-value>`. We translate at this boundary
 * via `hslToken`.
 */
export function OhlcvChart({ bars, signals, height }: OhlcvChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  // Resolved palette captured at create-time so data effects don't
  // need to re-read the DOM on every render.
  const paletteRef = useRef<Palette | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const palette = readPalette();
    paletteRef.current = palette;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: palette.bg },
        textColor: palette.fg,
        fontFamily:
          "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      rightPriceScale: { borderColor: palette.grid },
      timeScale: { borderColor: palette.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const candle = chart.addCandlestickSeries({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    });

    const volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: palette.fg,
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candle;
    volumeSeriesRef.current = volume;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      paletteRef.current = null;
    };
  }, []);

  // Data updates — bars
  useEffect(() => {
    const candle = candleSeriesRef.current;
    const volume = volumeSeriesRef.current;
    const palette = paletteRef.current;
    if (!candle || !volume || !palette) return;

    const candleData: CandlestickData<Time>[] = bars.map((b) => ({
      time: toUnix(b.ts),
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));

    const volData: HistogramData<Time>[] = bars.map((b) => ({
      time: toUnix(b.ts),
      value: b.volume,
      color: b.close >= b.open ? palette.upAlpha : palette.downAlpha,
    }));

    candle.setData(candleData);
    volume.setData(volData);
  }, [bars]);

  // Data updates — markers
  useEffect(() => {
    const candle = candleSeriesRef.current;
    const palette = paletteRef.current;
    if (!candle || !palette) return;
    if (!signals || signals.length === 0) {
      candle.setMarkers([]);
      return;
    }
    const markers: SeriesMarker<Time>[] = signals.map((s) => {
      const isBull = signalDirection(s) === "bull";
      return {
        time: toUnix(s.ts),
        position: isBull ? "belowBar" : "aboveBar",
        shape: isBull ? "arrowUp" : "arrowDown",
        color: isBull ? palette.up : palette.down,
        text: `${s.type}`,
      };
    });
    candle.setMarkers(markers);
  }, [signals]);

  return (
    <div
      ref={containerRef}
      // When `height` is omitted the chart fills its parent (the
      // parent must give it a concrete height — typically via
      // `flex-1` inside a flex column). When set, fixes pixel height.
      style={height ? { height } : { height: "100%" }}
      className="w-full rounded-md border border-border bg-bg-base"
      aria-label="OHLCV candlestick chart"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// Color resolution: read Tailwind CSS-var tokens (space-separated HSL
// triples) and translate to the legacy comma-separated form that LWC
// accepts.

interface Palette {
  bg: string;
  fg: string;
  grid: string;
  up: string;
  down: string;
  upAlpha: string;
  downAlpha: string;
}

function readPalette(): Palette {
  const root = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) =>
    (root.getPropertyValue(name).trim() || fallback);

  // Token values look like "222 18% 7%" — keep them raw so we can
  // emit either hsl(...) or hsla(...) at the call site.
  const bg = token("--bg-base", "222 18% 7%");
  const fg = token("--fg-muted", "220 10% 70%");
  const grid = token("--border-subtle", "222 14% 16%");
  const up = token("--up", "142 71% 45%");
  const down = token("--down", "0 84% 60%");

  return {
    bg: hslToken(bg),
    fg: hslToken(fg),
    grid: hslToken(grid),
    up: hslToken(up),
    down: hslToken(down),
    upAlpha: hslToken(up, 0.5),
    downAlpha: hslToken(down, 0.5),
  };
}

/**
 * Convert a Tailwind-style HSL triple ("222 18% 7%") to an
 * `rgb(...)` / `rgba(...)` string. Lightweight Charts' color parser
 * rejects both modern AND legacy `hsl()` forms in some paths (its
 * grayscale conversion in the AttributionLogoWidget throws on any
 * HSL input). RGB / hex / named colors are the only universally
 * accepted formats — so we resolve HSL → RGB on our side once at
 * chart-create time.
 */
function hslToken(triple: string, alpha?: number): string {
  const parts = triple.split(/\s+/).filter(Boolean);
  if (parts.length < 3) {
    // Token missing or malformed — fall back to a safe neutral so the
    // chart still renders rather than crashing on a parse error.
    return alpha !== undefined ? "rgba(128,128,128,0.5)" : "rgb(128,128,128)";
  }
  const h = parseFloat(parts[0]);
  const s = parseFloat(parts[1]); // strips trailing '%'
  const l = parseFloat(parts[2]);
  const [r, g, b] = hslToRgb(h, s, l);
  if (alpha !== undefined) {
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * HSL → RGB conversion. `h` is in degrees [0..360); `s` and `l` are
 * percentages [0..100]. Returns three integers [0..255]. Formula
 * from the CSS Color Level 4 spec.
 */
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const sPct = s / 100;
  const lPct = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = sPct * Math.min(lPct, 1 - lPct);
  const f = (n: number) =>
    lPct - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [
    Math.round(255 * f(0)),
    Math.round(255 * f(8)),
    Math.round(255 * f(4)),
  ];
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}
