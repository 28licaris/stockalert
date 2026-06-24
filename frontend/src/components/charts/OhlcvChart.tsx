import { useEffect, useMemo, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  LineStyle,
  TickMarkType,
  CandlestickSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type CandlestickData,
  type LineData,
  type HistogramData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, IndicatorSeries, Signal } from "@/api/queries";
import { signalDirection } from "@/api/queries";
import {
  componentRender,
  INDICATOR_BY_ID,
  OSCILLATOR_GUIDES,
  parentIndicatorId,
} from "./indicatorCatalog";

export type ChartType = "candles" | "line" | "area";

interface OhlcvChartProps {
  bars: ReadonlyArray<Bar>;
  signals?: ReadonlyArray<Signal>;
  /**
   * Computed indicator series from `useIndicators`. Overlay indicators
   * (SMA/EMA/WMA/Bollinger) draw on the price pane; oscillators
   * (RSI/MACD/Stochastic/TSI/ATR) each get their own pane below.
   */
  indicators?: ReadonlyArray<IndicatorSeries>;
  chartType?: ChartType;
  /** Price-pane height in px. Oscillator panes add to the total. */
  height?: number;
  /**
   * IANA timezone for the time axis + crosshair, or `undefined` for the
   * viewer's local zone. Must stay in sync with the Recent Bars table so
   * the two surfaces show the same clock. See lib/timezone.ts.
   */
  timezone?: string;
}

/** Extra vertical space added per oscillator pane. */
const OSC_PANE_PX = 150;

/**
 * Lightweight Charts (v5) wrapper. Encapsulates:
 *   - chart lifecycle (create / resize / dispose)
 *   - price series of the selected type (candles / line / area)
 *   - volume histogram overlaid on the price pane
 *   - signal markers (bullish / bearish)
 *   - indicator overlays + oscillator panes (added/removed reactively)
 *
 * Data-only re-renders update series in place; we never tear down the
 * chart for prop changes (would lose pan/zoom state). The price series
 * is rebuilt only when `chartType` changes.
 *
 * Color note: LWC ships its own color parser that does NOT accept the
 * modern space-separated `hsl(h s% l%)` syntax. Tailwind stores our
 * tokens as space-separated triples, so we translate candle/axis colors
 * to rgb at this boundary via `hslToken`. Indicator colors are plain hex
 * (see indicatorCatalog.ts) and pass through untouched.
 */
export function OhlcvChart({
  bars,
  signals,
  indicators,
  chartType = "candles",
  height = 480,
  timezone,
}: OhlcvChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<
    ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Area"> | null
  >(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // Indicator series added on the last render — removed before the next.
  const indicatorRefs = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  // Resolved palette captured at create-time so data effects don't
  // need to re-read the DOM on every render.
  const paletteRef = useRef<Palette | null>(null);
  // Latest props read by series-recreate effects without making them deps
  // (would rebuild the price series on every poll).
  const barsRef = useRef(bars);
  barsRef.current = bars;
  const signalsRef = useRef(signals);
  signalsRef.current = signals;

  // Total height grows with the number of oscillator panes so the price
  // pane keeps its real estate. Drives the container; ResizeObserver
  // propagates the new size to the chart.
  const oscCount = useMemo(() => countOscillatorPanes(indicators), [indicators]);
  const totalHeight = height + oscCount * OSC_PANE_PX;

  // ── Chart lifecycle (create once) ──────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const palette = readPalette();
    paletteRef.current = palette;

    const container = containerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
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
      timeScale: {
        borderColor: palette.grid,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: makeTickFormatter(timezone),
      },
      localization: { timeFormatter: makeCrosshairFormatter(timezone) },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: palette.fg,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    chartRef.current = chart;
    volumeSeriesRef.current = volume;

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) {
        chart.resize(rect.width, rect.height);
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null;
      volumeSeriesRef.current = null;
      markersRef.current = null;
      indicatorRefs.current = [];
      paletteRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Price series (rebuilt on chart-type change) ────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    const palette = paletteRef.current;
    if (!chart || !palette) return;

    const series = createPriceSeries(chart, chartType, palette);
    priceSeriesRef.current = series;
    setPriceData(series, barsRef.current, chartType);

    // Markers live on the price series, so (re)create the plugin here.
    markersRef.current = createSeriesMarkers(
      series,
      buildMarkers(signalsRef.current, palette),
    );

    return () => {
      markersRef.current = null;
      try {
        chart.removeSeries(series);
      } catch {
        // Chart already disposed (unmount race) — nothing to remove.
      }
      priceSeriesRef.current = null;
    };
  }, [chartType]);

  // ── Data updates — bars + volume ───────────────────────────────────
  useEffect(() => {
    const price = priceSeriesRef.current;
    const volume = volumeSeriesRef.current;
    const palette = paletteRef.current;
    if (!price || !volume || !palette) return;

    setPriceData(price, bars, chartType);
    volume.setData(
      bars.map((b) => ({
        time: toUnix(b.ts),
        value: b.volume,
        color: b.close >= b.open ? palette.upAlpha : palette.downAlpha,
      })) as HistogramData<Time>[],
    );
  }, [bars, chartType]);

  // ── Data updates — markers ─────────────────────────────────────────
  useEffect(() => {
    const palette = paletteRef.current;
    if (!markersRef.current || !palette) return;
    markersRef.current.setMarkers(buildMarkers(signals, palette));
  }, [signals]);

  // ── Indicators — overlays + oscillator panes ───────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    const palette = paletteRef.current;
    if (!chart || !palette) return;

    // Tear down what the previous render added.
    for (const s of indicatorRefs.current) {
      try {
        chart.removeSeries(s);
      } catch {
        // already gone
      }
    }
    indicatorRefs.current = [];
    // Drop now-empty oscillator panes (price/volume live in pane 0).
    const panes = chart.panes();
    for (let i = panes.length - 1; i >= 1; i--) {
      try {
        chart.removePane(i);
      } catch {
        // pane already removed
      }
    }

    if (!indicators || indicators.length === 0) return;

    // Group component series by their parent indicator, preserving the
    // order they were selected in.
    const groups = new Map<string, IndicatorSeries[]>();
    for (const s of indicators) {
      const pid = parentIndicatorId(s.name);
      const list = groups.get(pid) ?? [];
      list.push(s);
      groups.set(pid, list);
    }

    // Oscillators each claim the next pane below price.
    let nextPane = 1;
    const oscPane = new Map<string, number>();
    for (const pid of groups.keys()) {
      if (INDICATOR_BY_ID[pid]?.kind === "oscillator") {
        oscPane.set(pid, nextPane++);
      }
    }

    const added: ISeriesApi<"Line" | "Histogram">[] = [];
    for (const [pid, comps] of groups) {
      const isOsc = INDICATOR_BY_ID[pid]?.kind === "oscillator";
      const paneIdx = isOsc ? (oscPane.get(pid) ?? 0) : 0;
      let primary: ISeriesApi<"Line" | "Histogram"> | null = null;

      for (const comp of comps) {
        const rc = componentRender(comp.name);
        if (!rc) continue; // derived stat we don't chart
        const data = comp.values
          .filter((v) => v.value != null)
          .map((v) => ({ time: toUnix(v.timestamp), value: v.value as number }));
        if (data.length === 0) continue;

        let series: ISeriesApi<"Line" | "Histogram">;
        if (rc.type === "histogram") {
          series = chart.addSeries(
            HistogramSeries,
            { color: rc.color, priceLineVisible: false, lastValueVisible: false },
            paneIdx,
          );
        } else {
          series = chart.addSeries(
            LineSeries,
            {
              color: rc.color,
              lineWidth: rc.lineWidth ?? 2,
              lineStyle: rc.dashed ? LineStyle.Dashed : LineStyle.Solid,
              priceLineVisible: false,
              lastValueVisible: isOsc,
              crosshairMarkerVisible: true,
            },
            paneIdx,
          );
        }
        series.setData(data as (LineData<Time> | HistogramData<Time>)[]);
        added.push(series);
        primary ??= series;
      }

      // Reference lines for oscillators (RSI 30/70, Stoch 20/80, MACD 0).
      if (isOsc && primary) {
        for (const level of OSCILLATOR_GUIDES[pid] ?? []) {
          primary.createPriceLine({
            price: level,
            color: palette.grid,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "",
          });
        }
      }
    }
    indicatorRefs.current = added;

    // Keep the price pane dominant; oscillator panes share the rest.
    const panesNow = chart.panes();
    if (panesNow.length > 1) {
      panesNow[0].setStretchFactor(height);
      for (let i = 1; i < panesNow.length; i++) {
        panesNow[i].setStretchFactor(OSC_PANE_PX);
      }
    }
  }, [indicators, height]);

  // ── Re-label axis + crosshair on timezone change ───────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions({
      timeScale: { tickMarkFormatter: makeTickFormatter(timezone) },
      localization: { timeFormatter: makeCrosshairFormatter(timezone) },
    });
  }, [timezone]);

  return (
    <div
      ref={containerRef}
      style={{ height: totalHeight }}
      className="w-full shrink-0 rounded-md border border-border bg-bg-base"
      aria-label="OHLCV chart"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// Price series construction + data adaptation.

function createPriceSeries(
  chart: IChartApi,
  type: ChartType,
  palette: Palette,
): ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Area"> {
  if (type === "line") {
    return chart.addSeries(LineSeries, {
      color: palette.accent,
      lineWidth: 2,
      priceLineVisible: false,
    });
  }
  if (type === "area") {
    return chart.addSeries(AreaSeries, {
      lineColor: palette.accent,
      topColor: palette.accentAlpha,
      bottomColor: "rgba(0, 0, 0, 0)",
      lineWidth: 2,
      priceLineVisible: false,
    });
  }
  return chart.addSeries(CandlestickSeries, {
    upColor: palette.up,
    downColor: palette.down,
    borderUpColor: palette.up,
    borderDownColor: palette.down,
    wickUpColor: palette.up,
    wickDownColor: palette.down,
  });
}

function setPriceData(
  series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Area">,
  bars: ReadonlyArray<Bar>,
  type: ChartType,
) {
  if (type === "candles") {
    (series as ISeriesApi<"Candlestick">).setData(
      bars.map((b) => ({
        time: toUnix(b.ts),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })) as CandlestickData<Time>[],
    );
    return;
  }
  (series as ISeriesApi<"Line">).setData(
    bars.map((b) => ({ time: toUnix(b.ts), value: b.close })) as LineData<Time>[],
  );
}

function buildMarkers(
  signals: ReadonlyArray<Signal> | undefined,
  palette: Palette,
): SeriesMarker<Time>[] {
  if (!signals || signals.length === 0) return [];
  return signals.map((s) => {
    const isBull = signalDirection(s) === "bull";
    return {
      time: toUnix(s.ts),
      position: isBull ? "belowBar" : "aboveBar",
      shape: isBull ? "arrowUp" : "arrowDown",
      color: isBull ? palette.up : palette.down,
      text: `${s.type}`,
    };
  });
}

/** Number of distinct oscillator indicators present (= extra panes). */
function countOscillatorPanes(
  indicators: ReadonlyArray<IndicatorSeries> | undefined,
): number {
  if (!indicators) return 0;
  const parents = new Set<string>();
  for (const s of indicators) {
    const pid = parentIndicatorId(s.name);
    if (INDICATOR_BY_ID[pid]?.kind === "oscillator") parents.add(pid);
  }
  return parents.size;
}

// ─────────────────────────────────────────────────────────────────────
// Color resolution: read Tailwind CSS-var tokens (space-separated HSL
// triples) and translate to the rgb form LWC's parser accepts.

interface Palette {
  bg: string;
  fg: string;
  grid: string;
  up: string;
  down: string;
  upAlpha: string;
  downAlpha: string;
  accent: string;
  accentAlpha: string;
}

function readPalette(): Palette {
  const root = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) =>
    root.getPropertyValue(name).trim() || fallback;

  const bg = token("--bg-base", "222 18% 7%");
  const fg = token("--fg-muted", "220 10% 70%");
  const grid = token("--border-subtle", "222 14% 16%");
  const up = token("--up", "142 71% 45%");
  const down = token("--down", "0 84% 60%");
  const accent = token("--accent", "239 84% 67%");

  return {
    bg: hslToken(bg),
    fg: hslToken(fg),
    grid: hslToken(grid),
    up: hslToken(up),
    down: hslToken(down),
    upAlpha: hslToken(up, 0.5),
    downAlpha: hslToken(down, 0.5),
    accent: hslToken(accent),
    accentAlpha: hslToken(accent, 0.4),
  };
}

/**
 * Convert a Tailwind-style HSL triple ("222 18% 7%") to an
 * `rgb(...)` / `rgba(...)` string. Lightweight Charts' color parser
 * rejects HSL inputs in some paths, so we resolve HSL → RGB on our side
 * once at chart-create time.
 */
function hslToken(triple: string, alpha?: number): string {
  const parts = triple.split(/\s+/).filter(Boolean);
  if (parts.length < 3) {
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
 * percentages [0..100]. Returns three integers [0..255]. Formula from
 * the CSS Color Level 4 spec.
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

/**
 * Parse an ISO timestamp to UTC epoch seconds. Offset-less strings are
 * treated as UTC (the backend emits naive-UTC timestamps in places),
 * which keeps bars and indicator series aligned on the same axis.
 */
function toUnix(iso: string): UTCTimestamp {
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  return Math.floor(new Date(s).getTime() / 1000) as UTCTimestamp;
}

// ─────────────────────────────────────────────────────────────────────
// Timezone-aware axis formatting. Bar `time` values are UTC epoch
// seconds (see toUnix); LWC would otherwise label the axis in UTC. We
// format each instant in `zone` (an IANA name, or undefined = local).

/** Axis tick labels: granularity comes from `tickMarkType`. */
function makeTickFormatter(zone: string | undefined) {
  const time = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const day = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    month: "short",
    day: "numeric",
  });
  const month = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    month: "short",
    year: "2-digit",
  });
  const year = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    year: "numeric",
  });
  return (time_: Time, tickMarkType: TickMarkType): string => {
    const d = new Date((time_ as UTCTimestamp) * 1000);
    switch (tickMarkType) {
      case TickMarkType.Year:
        return year.format(d);
      case TickMarkType.Month:
        return month.format(d);
      case TickMarkType.DayOfMonth:
        return day.format(d);
      default:
        return time.format(d);
    }
  };
}

/** Crosshair / tooltip label: full date + time in the selected zone. */
function makeCrosshairFormatter(zone: string | undefined) {
  const fmt = new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return (time_: Time): string =>
    fmt.format(new Date((time_ as UTCTimestamp) * 1000));
}
