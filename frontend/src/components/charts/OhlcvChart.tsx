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
  type IPriceLine,
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

/** One labeled swing point of a wave count (the engine's primary pivots). */
export interface WavePivotPoint {
  ts: string;
  price: number;
  label: string;
  kind?: "high" | "low";
}

/** A horizontal level to draw (invalidation / Fib target). */
export interface WavePriceLevel {
  price: number;
  title: string;
  kind: "invalidation" | "target";
}

/** Dotted forward projection line + optional target zone. */
export interface WaveProjection {
  fromTs: string;
  fromPrice: number;
  toTs: string;
  toPrice: number;
  targetLow?: number;
  targetHigh?: number;
}

export interface WaveOverlay {
  pivots: ReadonlyArray<WavePivotPoint>;
  levels: ReadonlyArray<WavePriceLevel>;
  /** Latest bar timestamp used for the "in-progress" extension segment. */
  asOfTs?: string;
  /** Latest bar price used for the extension segment endpoint. */
  asOfPrice?: number;
  /** Forward projection to the next-move target zone. */
  projection?: WaveProjection;
}

interface OhlcvChartProps {
  bars: ReadonlyArray<Bar>;
  signals?: ReadonlyArray<Signal>;
  /** Elliott Wave overlay (pivot path, extension, projection, price levels).
   * When present, wave pivot markers take priority over signal markers. */
  wave?: WaveOverlay | null;
  /**
   * Computed indicator series from `useIndicators`. Overlay indicators
   * (SMA/EMA/WMA/Bollinger) draw on the price pane; oscillators
   * (RSI/MACD/Stochastic/TSI/ATR) each get their own pane below.
   */
  indicators?: ReadonlyArray<IndicatorSeries>;
  chartType?: ChartType;
  /** Price-pane height in px, or "fill" to occupy the parent height. */
  height?: number | "fill";
  /**
   * Changes when the caller wants the visible time scale reset to the loaded
   * data window (for example ticker / interval / range changes). Ordinary
   * polling should keep this stable so user pan/zoom is not snapped back.
   */
  fitKey?: string;
  /**
   * Optional viewport preset. The chart may receive more data than this
   * range so users can pan backward, but ticker / interval / range changes
   * should snap the initial view here.
   */
  visibleRange?: { from: string; to: string } | null;
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
  wave,
  indicators,
  chartType = "candles",
  height = 480,
  fitKey,
  visibleRange,
  timezone,
}: OhlcvChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<
    ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | ISeriesApi<"Area"> | null
  >(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const sessionShadeLayerRef = useRef<HTMLDivElement | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // Elliott Wave overlay series — created once in the chart-lifecycle
  // effect (chart-type-independent, like volume).
  const waveSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  /** Dashed line: last confirmed pivot → current price (in-progress wave). */
  const extensionSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  /** Dotted line: current price → forward target midpoint. */
  const projectionSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  // Indicator series added on the last render — removed before the next.
  const indicatorRefs = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const pendingFitKeyRef = useRef<string | null>(fitKey ?? null);
  const lastFitKeyRef = useRef<string | null>(null);
  // Resolved palette captured at create-time so data effects don't
  // need to re-read the DOM on every render.
  const paletteRef = useRef<Palette | null>(null);
  // Latest props read by series-recreate effects without making them deps
  // (would rebuild the price series on every poll).
  const barsRef = useRef(bars);
  barsRef.current = bars;
  const signalsRef = useRef(signals);
  signalsRef.current = signals;
  const waveRef = useRef(wave);
  waveRef.current = wave;

  // Total height grows with the number of oscillator panes so the price
  // pane keeps its real estate. Drives the container; ResizeObserver
  // propagates the new size to the chart.
  const oscCount = useMemo(() => countOscillatorPanes(indicators), [indicators]);
  const totalHeight =
    height === "fill" ? "100%" : height + oscCount * OSC_PANE_PX;

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

    const sessionShadeLayer = document.createElement("div");
    Object.assign(sessionShadeLayer.style, {
      position: "absolute",
      inset: "0",
      overflow: "hidden",
      pointerEvents: "none",
      zIndex: "2",
    });
    container.appendChild(sessionShadeLayer);

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

    const waveLine = chart.addSeries(LineSeries, {
      color: palette.fg,
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    const extensionLine = chart.addSeries(LineSeries, {
      color: palette.fg,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    const projectionLine = chart.addSeries(LineSeries, {
      color: palette.fg,
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    sessionShadeLayerRef.current = sessionShadeLayer;
    volumeSeriesRef.current = volume;
    waveSeriesRef.current = waveLine;
    extensionSeriesRef.current = extensionLine;
    projectionSeriesRef.current = projectionLine;

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) {
        chart.resize(rect.width, rect.height);
        scheduleExtendedHoursOverlay(chart, barsRef.current, palette, sessionShadeLayer);
      }
    });
    ro.observe(container);

    const repaintExtendedHours = () =>
      scheduleExtendedHoursOverlay(chart, barsRef.current, palette, sessionShadeLayer);
    chart.timeScale().subscribeVisibleTimeRangeChange(repaintExtendedHours);

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(repaintExtendedHours);
      ro.disconnect();
      sessionShadeLayer.remove();
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null;
      sessionShadeLayerRef.current = null;
      volumeSeriesRef.current = null;
      markersRef.current = null;
      waveSeriesRef.current = null;
      extensionSeriesRef.current = null;
      projectionSeriesRef.current = null;
      priceLinesRef.current = [];
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
      buildMarkers(signalsRef.current, waveRef.current, palette),
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
    const sessionShadeLayer = sessionShadeLayerRef.current;
    const palette = paletteRef.current;
    const chart = chartRef.current;
    if (!price || !volume || !sessionShadeLayer || !palette || !chart) return;

    setPriceData(price, bars, chartType);
    scheduleExtendedHoursOverlay(chart, bars, palette, sessionShadeLayer);
    volume.setData(
      bars.map((b) => ({
        time: toUnix(b.ts),
        value: b.volume,
        color: b.close >= b.open ? palette.upAlpha : palette.downAlpha,
      })) as HistogramData<Time>[],
    );
  }, [bars, chartType]);

  // ── Range changes — fit the viewport to the newly requested window ──
  useEffect(() => {
    if (fitKey && fitKey !== lastFitKeyRef.current) {
      pendingFitKeyRef.current = fitKey;
    }
  }, [fitKey]);

  useEffect(() => {
    const chart = chartRef.current;
    const pending = pendingFitKeyRef.current;
    if (!chart || !pending || bars.length === 0) return;
    const range = visibleRange ? toVisibleRange(visibleRange) : null;
    if (range) {
      chart.timeScale().setVisibleRange(range);
    } else {
      chart.timeScale().fitContent();
    }
    const palette = paletteRef.current;
    const sessionShadeLayer = sessionShadeLayerRef.current;
    if (palette && sessionShadeLayer) {
      scheduleExtendedHoursOverlay(chart, bars, palette, sessionShadeLayer);
    }
    lastFitKeyRef.current = pending;
    pendingFitKeyRef.current = null;
  }, [bars, visibleRange]);

  // ── Data updates — markers (wave pivots take priority over signals) ─
  useEffect(() => {
    const palette = paletteRef.current;
    if (!markersRef.current || !palette) return;
    markersRef.current.setMarkers(buildMarkers(signals, wave, palette));
  }, [signals, wave]);

  // ── Data updates — wave path + extension + projection + price lines ─
  useEffect(() => {
    const price = priceSeriesRef.current;
    const line = waveSeriesRef.current;
    const ext = extensionSeriesRef.current;
    const proj = projectionSeriesRef.current;
    const palette = paletteRef.current;
    if (!price || !line || !ext || !proj || !palette) return;

    for (const pl of priceLinesRef.current) {
      try {
        price.removePriceLine(pl);
      } catch {
        // price series was recreated (chart-type change) — line is gone.
      }
    }
    priceLinesRef.current = [];

    if (!wave) {
      line.setData([]);
      ext.setData([]);
      proj.setData([]);
      return;
    }

    // Confirmed pivot path — each segment colored by direction. LWC
    // applies the color on point[i] to the segment ending at point[i].
    const lineData: LineData<Time>[] = wave.pivots.map((p, i) => {
      let color: string | undefined;
      if (i > 0) {
        color = p.price > wave.pivots[i - 1].price ? palette.up : palette.down;
      } else if (wave.pivots.length > 1) {
        color = wave.pivots[1].price > p.price ? palette.up : palette.down;
      }
      return { time: toUnix(p.ts), value: p.price, color };
    });
    line.setData(lineData);

    // Extension: dashed line from last confirmed pivot → current bar price.
    const lastPivot = wave.pivots.at(-1);
    if (lastPivot && wave.asOfTs && wave.asOfPrice != null) {
      const extUp = wave.asOfPrice > lastPivot.price;
      const extColor = extUp ? palette.up : palette.down;
      ext.setData([
        { time: toUnix(lastPivot.ts), value: lastPivot.price, color: extColor },
        { time: toUnix(wave.asOfTs), value: wave.asOfPrice, color: extColor },
      ]);
    } else {
      ext.setData([]);
    }

    // Projection: dotted line from current price → target midpoint.
    if (wave.projection) {
      const p = wave.projection;
      const projUp = p.toPrice > p.fromPrice;
      const projColor = projUp ? palette.upAlpha : palette.downAlpha;
      proj.setData([
        { time: toUnix(p.fromTs), value: p.fromPrice, color: projColor },
        { time: toUnix(p.toTs), value: p.toPrice, color: projColor },
      ]);
    } else {
      proj.setData([]);
    }

    // Invalidation + Fib target price lines
    for (const lvl of wave.levels) {
      priceLinesRef.current.push(
        price.createPriceLine({
          price: lvl.price,
          color: lvl.kind === "invalidation" ? palette.down : palette.up,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: lvl.title,
        }),
      );
    }
  }, [wave, chartType]);

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
        const customColor =
          typeof comp.params?.color === "string" ? comp.params.color : undefined;
        const customLineWidth =
          typeof comp.params?.lineWidth === "number"
            ? (comp.params.lineWidth as 1 | 2 | 3 | 4)
            : undefined;

        let series: ISeriesApi<"Line" | "Histogram">;
        if (rc.type === "histogram") {
          series = chart.addSeries(
            HistogramSeries,
            {
              color: customColor ?? rc.color,
              priceLineVisible: false,
              lastValueVisible: false,
            },
            paneIdx,
          );
        } else {
          series = chart.addSeries(
            LineSeries,
            {
              color: customColor ?? rc.color,
              lineWidth: customLineWidth ?? rc.lineWidth ?? 2,
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
      panesNow[0].setStretchFactor(height === "fill" ? 640 : height);
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
      className="relative h-full min-h-[420px] w-full rounded-md border border-border bg-bg-base"
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

function scheduleExtendedHoursOverlay(
  chart: IChartApi,
  bars: ReadonlyArray<Bar>,
  palette: Palette,
  layer: HTMLDivElement,
) {
  window.requestAnimationFrame(() => {
    updateExtendedHoursOverlay(chart, bars, palette, layer);
  });
}

function updateExtendedHoursOverlay(
  chart: IChartApi,
  bars: ReadonlyArray<Bar>,
  palette: Palette,
  layer: HTMLDivElement,
) {
  layer.replaceChildren();
  if (bars.length === 0) return;

  const width = layer.clientWidth;
  const height = layer.clientHeight;
  if (width <= 0 || height <= 0) return;

  const ranges = buildExtendedSessionRanges(bars);
  const timeScale = chart.timeScale();
  const visibleRange = timeScale.getVisibleRange();
  if (!visibleRange) return;
  const visibleFrom = visibleRange.from as UTCTimestamp;
  const visibleTo = visibleRange.to as UTCTimestamp;

  for (const range of ranges) {
    const rangeFrom = toUnix(range.from);
    const rangeTo = toUnix(range.to);
    const clippedFrom = Math.max(rangeFrom, visibleFrom) as UTCTimestamp;
    const clippedTo = Math.min(rangeTo, visibleTo) as UTCTimestamp;
    if (clippedTo <= clippedFrom) continue;

    const from = coordinateForTime(chart, bars, clippedFrom, "after", {
      visibleFrom,
      visibleTo,
      width,
    });
    const to = coordinateForTime(chart, bars, clippedTo, "before", {
      visibleFrom,
      visibleTo,
      width,
    });
    if (from == null || to == null) continue;

    const left = Math.max(0, Math.min(from, to));
    const right = Math.min(width, Math.max(from, to));
    if (right <= 0 || left >= width || right - left < 1) continue;

    const block = document.createElement("div");
    Object.assign(block.style, {
      position: "absolute",
      top: "0",
      bottom: "0",
      left: `${left}px`,
      width: `${right - left}px`,
      background: palette.extendedSession,
      borderLeft: `1px solid ${palette.extendedSessionEdge}`,
      borderRight: `1px solid ${palette.extendedSessionEdge}`,
    });
    layer.appendChild(block);
  }
}

function coordinateForTime(
  chart: IChartApi,
  bars: ReadonlyArray<Bar>,
  target: UTCTimestamp,
  direction: "before" | "after",
  bounds: { visibleFrom: UTCTimestamp; visibleTo: UTCTimestamp; width: number },
): number | null {
  if (target <= bounds.visibleFrom) return 0;
  if (target >= bounds.visibleTo) return bounds.width;

  const exact = chart.timeScale().timeToCoordinate(target);
  if (exact != null) return exact;

  let candidate: UTCTimestamp | null = null;
  if (direction === "after") {
    for (const bar of bars) {
      const t = toUnix(bar.ts);
      if (t >= target) {
        candidate = t;
        break;
      }
    }
  } else {
    for (let i = bars.length - 1; i >= 0; i--) {
      const t = toUnix(bars[i].ts);
      if (t <= target) {
        candidate = t;
        break;
      }
    }
  }

  return candidate == null ? null : chart.timeScale().timeToCoordinate(candidate);
}

function buildExtendedSessionRanges(
  bars: ReadonlyArray<Bar>,
): Array<{ from: string; to: string }> {
  if (bars.length === 0) return [];

  const sessions = new Map<string, { year: number; month: number; day: number }>();
  for (const bar of bars) {
    const session = etDateParts(bar.ts);
    if (!session || session.weekday === "Sat" || session.weekday === "Sun") continue;
    const key = `${session.year}-${session.month}-${session.day}`;
    sessions.set(key, session);
  }

  return Array.from(sessions.values())
    .sort((a, b) =>
      a.year - b.year || a.month - b.month || a.day - b.day,
    )
    .flatMap((session) => [
      {
        from: etLocalToUtcIso(session, 4, 0),
        to: etLocalToUtcIso(session, 9, 30),
      },
      {
        from: etLocalToUtcIso(session, 16, 0),
        to: etLocalToUtcIso(session, 20, 0),
      },
    ]);
}

function buildMarkers(
  signals: ReadonlyArray<Signal> | undefined,
  wave: WaveOverlay | null | undefined,
  palette: Palette,
): SeriesMarker<Time>[] {
  if (wave && wave.pivots.length > 0) {
    return wave.pivots.map((p) => ({
      time: toUnix(p.ts),
      position:
        p.kind === "high" ? "aboveBar"
        : p.kind === "low" ? "belowBar"
        : p.label === "0" ? "belowBar"
        : "aboveBar",
      shape: "circle",
      color:
        p.kind === "high" ? palette.up
        : p.kind === "low" ? palette.down
        : palette.fg,
      text: p.label,
    }));
  }
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
  extendedSession: string;
  extendedSessionEdge: string;
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
    extendedSession: hslToken(fg, 0.09),
    extendedSessionEdge: hslToken(fg, 0.12),
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

function toVisibleRange(
  range: { from: string; to: string },
): { from: UTCTimestamp; to: UTCTimestamp } | null {
  const from = toUnix(range.from);
  const to = toUnix(range.to);
  if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) return null;
  return { from, to };
}

const ET_SESSION_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function etDateParts(ts: string):
  | {
      year: number;
      month: number;
      day: number;
      weekday: string;
      hour: number;
      minute: number;
    }
  | null {
  const parts = ET_SESSION_FORMATTER.formatToParts(new Date(toUnix(ts) * 1000));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value;
  const year = Number(part("year"));
  const month = Number(part("month"));
  const day = Number(part("day"));
  const hour = Number(part("hour"));
  const minute = Number(part("minute"));
  const weekday = part("weekday");
  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(hour) ||
    !Number.isFinite(minute) ||
    !weekday
  ) {
    return null;
  }
  return { year, month, day, weekday, hour, minute };
}

function etLocalToUtcIso(
  session: { year: number; month: number; day: number },
  hour: number,
  minute: number,
): string {
  const utcGuess = Date.UTC(session.year, session.month - 1, session.day, hour, minute);
  const firstPass = utcGuess - timeZoneOffsetMs(new Date(utcGuess));
  const offset = timeZoneOffsetMs(new Date(firstPass));
  return new Date(utcGuess - offset).toISOString();
}

function timeZoneOffsetMs(date: Date): number {
  const parts = ET_SESSION_FORMATTER.formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value;
  const asUtc = Date.UTC(
    Number(part("year")),
    Number(part("month")) - 1,
    Number(part("day")),
    Number(part("hour")),
    Number(part("minute")),
  );
  return asUtc - date.getTime();
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
