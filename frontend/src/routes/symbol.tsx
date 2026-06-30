import { Link, useNavigate, useParams } from "react-router-dom";
import { useCallback, useMemo, useState } from "react";
import { Table2 } from "lucide-react";
import { OhlcvChart, type ChartType } from "@/components/charts/OhlcvChart";
import {
  ChartToolbar,
  type MovingAverageKind,
  type MovingAverageOverlay,
} from "@/components/charts/ChartToolbar";
import { BarsTable } from "@/components/tables/BarsTable";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import {
  useIndicators,
  useLakeBars,
  useSymbolSignals,
  type Bar,
  type ChartRange,
  type IndicatorSeries,
} from "@/api/queries";
import { useUserSetting } from "@/lib/storage";
import {
  DEFAULT_TZ,
  resolveZone,
  type TzSetting,
} from "@/lib/timezone";
import { fmtAgo, fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;
type Interval = (typeof INTERVALS)[number];

const DEFAULT_INTERVAL: Interval = "5m";
const DEFAULT_RANGE: ChartRange = "30D";
const DEFAULT_CHART_TYPE: ChartType = "candles";
type IndicatorSettings = Record<string, Record<string, number>>;

const DEFAULT_MA_COLORS = ["#f59e0b", "#38bdf8", "#c084fc", "#22c55e", "#f43f5e"];

/**
 * Charts page — chart + indicators (FE-2.1).
 *
 * OHLCV (candles / line / area) + volume + signal markers, with
 * configurable indicator overlays (moving averages, Bollinger) and
 * oscillator panes (RSI, MACD, Stochastic, TSI, ATR) sourced from
 * `/api/v1/indicators/chart-data`. Selections persist per user via
 * localStorage.
 *
 * Coming in subsequent phases:
 *   - per-indicator parameter editing (period, source)
 *   - coverage strip beneath the chart (FE-7)
 *   - journal-trades-on-this-ticker panel (FE-8)
 *   - adjusted/raw price toggle (once silver lands the _adj columns)
 */
export function SymbolPage() {
  const params = useParams();
  const ticker = (params.ticker ?? "").toUpperCase();

  const [interval, setInterval] = useUserSetting<Interval>(
    "charts.interval",
    DEFAULT_INTERVAL,
  );
  const [range, setRange] = useUserSetting<ChartRange>(
    "charts.range",
    DEFAULT_RANGE,
  );
  const [chartType, setChartType] = useUserSetting<ChartType>(
    "chart.type",
    DEFAULT_CHART_TYPE,
  );
  // Selected indicator registry ids (e.g. ["sma", "rsi"]).
  const [indicatorIds, setIndicatorIds] = useUserSetting<string[]>(
    "chart.indicators",
    [],
  );
  const [indicatorSettings, setIndicatorSettings] =
    useUserSetting<IndicatorSettings>("chart.indicatorSettings", {
      sma: { period: 20 },
      ema: { period: 20 },
      wma: { period: 20 },
    });
  const [movingAverages, setMovingAverages] = useUserSetting<MovingAverageOverlay[]>(
    "chart.movingAverages",
    [{ id: "ma-sma-200", kind: "sma", period: 200, color: "#f59e0b" }],
  );
  // Global display timezone for the chart axis + Recent Bars table.
  const [tz, setTz] = useUserSetting<TzSetting>("chart.timezone", DEFAULT_TZ);
  const zone = resolveZone(tz);
  const backendIndicatorIds = useMemo(
    () => indicatorIds.filter((id) => !isMovingAverageKind(id)),
    [indicatorIds],
  );

  const bars = useLakeBars(ticker || undefined, interval, range);
  const signals = useSymbolSignals(ticker || undefined, 100);
  const indicators = useIndicators(
    ticker || undefined,
    interval,
    backendIndicatorIds,
    range,
    indicatorSettings,
  );
  const movingAverageSeries = useMemo(
    () => buildMovingAverageSeries(bars.data ?? [], movingAverages, interval),
    [bars.data, movingAverages, interval],
  );
  const chartIndicators = useMemo(
    () => [...movingAverageSeries, ...(indicators.data ?? [])],
    [movingAverageSeries, indicators.data],
  );

  const toggleIndicator = useCallback(
    (id: string) =>
      setIndicatorIds((prev) =>
        prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
      ),
    [setIndicatorIds],
  );
  const clearIndicators = useCallback(
    () => setIndicatorIds([]),
    [setIndicatorIds],
  );
  const setIndicatorParam = useCallback(
    (id: string, key: string, value: number) =>
      setIndicatorSettings((prev) => ({
        ...prev,
        [id]: {
          ...(prev[id] ?? {}),
          [key]: value,
        },
      })),
    [setIndicatorSettings],
  );
  const addMovingAverage = useCallback(
    (kind: MovingAverageKind) =>
      setMovingAverages((prev) => {
        const color = DEFAULT_MA_COLORS[prev.length % DEFAULT_MA_COLORS.length];
        const period = kind === "ema" ? 50 : 20;
        return [
          ...prev,
          {
            id: `ma-${kind}-${Date.now().toString(36)}-${prev.length}`,
            kind,
            period,
            color,
          },
        ];
      }),
    [setMovingAverages],
  );
  const updateMovingAverage = useCallback(
    (id: string, patch: Partial<Omit<MovingAverageOverlay, "id">>) =>
      setMovingAverages((prev) =>
        prev.map((ma) => (ma.id === id ? { ...ma, ...patch } : ma)),
      ),
    [setMovingAverages],
  );
  const removeMovingAverage = useCallback(
    (id: string) =>
      setMovingAverages((prev) => prev.filter((ma) => ma.id !== id)),
    [setMovingAverages],
  );
  const clearMovingAverages = useCallback(
    () => setMovingAverages([]),
    [setMovingAverages],
  );
  const setRangeWithSnap = useCallback(
    (next: ChartRange) => {
      setRange(next);
      if (next === "1Y" || next === "5Y" || next === "MAX") {
        setInterval("1d");
      }
    },
    [setInterval, setRange],
  );

  if (!ticker) {
    return <SymbolPicker />;
  }

  const latest = bars.data?.at(-1);
  const prevClose = bars.data?.at(-2)?.close;
  const change =
    latest && prevClose ? ((latest.close - prevClose) / prevClose) * 100 : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 md:p-6">
      <header className="surface-panel rounded-lg p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">
              chart workspace
            </p>
            <h1 className="mt-1 font-display text-3xl font-semibold tracking-normal text-fg-base">
              {ticker}
            </h1>
            <div className="mt-1 flex items-baseline gap-3 text-sm">
              <span className="font-mono text-lg text-fg-base">
                {fmtPrice(latest?.close)}
              </span>
              {change !== null ? (
                <span
                  className={cn(
                    "font-mono",
                    change >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {change >= 0 ? "+" : ""}
                  {change.toFixed(2)}%
                </span>
              ) : null}
              <span className="text-xs text-fg-subtle">
                {bars.dataUpdatedAt
                  ? fmtAgo(new Date(bars.dataUpdatedAt).toISOString())
                  : "Loading..."}
              </span>
            </div>
          </div>
          <Button type="button" variant="outline" size="sm" asChild>
            <Link to={`/charts/${encodeURIComponent(ticker)}/bars`}>
              <Table2 className="h-4 w-4" />
              Recent bars
            </Link>
          </Button>
        </div>
      </header>

      <ChartToolbar
        interval={interval}
        intervals={INTERVALS}
        onIntervalChange={(i) => setInterval(i as Interval)}
        range={range}
        onRangeChange={setRangeWithSnap}
        chartType={chartType}
        onChartTypeChange={setChartType}
        tz={tz}
        onTzChange={setTz}
        selected={backendIndicatorIds}
        onToggleIndicator={toggleIndicator}
        onClearIndicators={clearIndicators}
        indicatorSettings={indicatorSettings}
        onIndicatorSettingChange={setIndicatorParam}
        movingAverages={movingAverages}
        onAddMovingAverage={addMovingAverage}
        onUpdateMovingAverage={updateMovingAverage}
        onRemoveMovingAverage={removeMovingAverage}
        onClearMovingAverages={clearMovingAverages}
      />

      {bars.error ? <ApiErrorAlert error={bars.error} /> : null}
      {indicators.error ? <ApiErrorAlert error={indicators.error} /> : null}

      {/* Out-of-universe symbols are fetched live from Schwab on first view,
          which takes a few seconds. Overlay the (still-mounted) chart so we
          don't fight its lifecycle, then fall back to an explicit empty state
          when the fetch settles with no data (e.g. an unknown ticker). */}
      <div className="surface-panel relative min-h-0 flex-1 overflow-visible rounded-lg p-2">
        <OhlcvChart
          bars={bars.data ?? []}
          signals={signals.data ?? []}
          indicators={chartIndicators}
          chartType={chartType}
          timezone={zone}
          height="fill"
          fitKey={`${ticker}:${interval}:${range}`}
        />
        {!bars.data || bars.data.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center rounded-md bg-bg-base/60 text-sm text-fg-muted backdrop-blur-[1px]">
            {bars.isLoading || bars.isFetching
              ? "Fetching history…"
              : `No data available for ${ticker}`}
          </div>
        ) : null}
      </div>

    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function buildMovingAverageSeries(
  bars: ReadonlyArray<Bar>,
  overlays: ReadonlyArray<MovingAverageOverlay>,
  interval: Interval,
): IndicatorSeries[] {
  if (bars.length === 0 || overlays.length === 0) return [];
  return overlays.map((ma) => {
    const values = movingAverageValues(
      bars.map((bar) => bar.close),
      ma.kind,
      ma.period,
    );
    const unit = interval === "1d" ? "D" : " bars";
    const label = `${ma.kind.toUpperCase()} ${ma.period}${unit}`;
    return {
      name: `ma_${ma.id}`,
      label,
      params: {
        kind: ma.kind,
        period: ma.period,
        color: ma.color,
        lineWidth: 2,
      },
      values: bars.map((bar, index) => ({
        timestamp: bar.ts,
        value: values[index] ?? null,
      })),
      count: bars.length,
    };
  });
}

function movingAverageValues(
  closes: number[],
  kind: MovingAverageKind,
  period: number,
): Array<number | null> {
  const n = Math.max(1, Math.round(period));
  if (kind === "ema") return emaValues(closes, n);
  if (kind === "wma") return wmaValues(closes, n);
  return smaValues(closes, n);
}

function smaValues(closes: number[], period: number): Array<number | null> {
  const out: Array<number | null> = Array(closes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function emaValues(closes: number[], period: number): Array<number | null> {
  const out: Array<number | null> = Array(closes.length).fill(null);
  if (closes.length < period) return out;
  const k = 2 / (period + 1);
  let ema = 0;
  for (let i = 0; i < period; i++) ema += closes[i];
  ema /= period;
  out[period - 1] = ema;
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

function wmaValues(closes: number[], period: number): Array<number | null> {
  const out: Array<number | null> = Array(closes.length).fill(null);
  const denom = (period * (period + 1)) / 2;
  for (let i = period - 1; i < closes.length; i++) {
    let weighted = 0;
    for (let j = 0; j < period; j++) {
      weighted += closes[i - j] * (period - j);
    }
    out[i] = weighted / denom;
  }
  return out;
}

function isMovingAverageKind(id: string): id is MovingAverageKind {
  return id === "sma" || id === "ema" || id === "wma";
}

// ─────────────────────────────────────────────────────────────────────

function SymbolPicker() {
  const navigate = useNavigate();
  const [recent, setRecent] = useUserSetting<string[]>("symbol.recent", []);
  const [input, setInput] = useState("");

  const go = (sym: string) => {
    const norm = sym.trim().toUpperCase();
    if (!norm) return;
    setRecent((prev) => {
      const next = [norm, ...prev.filter((p) => p !== norm)].slice(0, 12);
      return next;
    });
    navigate(`/charts/${encodeURIComponent(norm)}`);
  };

  return (
    <div className="mx-auto max-w-xl space-y-6 p-6 md:p-8">
      <header className="surface-panel rounded-lg p-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">
          chart workspace
        </p>
        <h1 className="mt-2 font-display text-2xl font-semibold text-fg-base">
          Charts
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Search by ticker or company name to open the full chart workspace.
        </p>
      </header>

      <div className="flex gap-2">
        <SymbolSearchInput
          value={input}
          onChange={setInput}
          onSubmit={(value, match) => go(match ? match.symbol : value)}
          placeholder="AAPL · Apple Inc · etc"
          autoFocus
          className="flex-1"
        />
        <Button type="button" onClick={() => go(input)} disabled={!input.trim()}>
          Open
        </Button>
      </div>

      {recent.length > 0 ? (
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
            Recent
          </h2>
          <div className="flex flex-wrap gap-2">
            {recent.map((sym) => (
              <Link
                key={sym}
                to={`/charts/${encodeURIComponent(sym)}`}
                className="rounded-md border border-border bg-bg-subtle px-3 py-1.5 font-mono text-xs text-fg-base hover:bg-bg-muted"
              >
                {sym}
              </Link>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function RecentBarsPage() {
  const params = useParams();
  const ticker = (params.ticker ?? "").toUpperCase();
  const [interval] = useUserSetting<Interval>("charts.interval", DEFAULT_INTERVAL);
  const [range] = useUserSetting<ChartRange>("charts.range", DEFAULT_RANGE);
  const [tz] = useUserSetting<TzSetting>("chart.timezone", DEFAULT_TZ);
  const zone = resolveZone(tz);
  const bars = useLakeBars(ticker || undefined, interval, range);

  if (!ticker) {
    return <SymbolPicker />;
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4 md:p-6">
      <header className="surface-panel rounded-lg p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">
              developer data
            </p>
            <h1 className="mt-1 font-display text-2xl font-semibold text-fg-base">
              {ticker} recent bars
            </h1>
            <p className="mt-1 text-sm text-fg-muted">
              {interval} bars shown in {tz}. This view is for development and
              production diagnostics.
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" asChild>
            <Link to={`/charts/${encodeURIComponent(ticker)}`}>
              Back to chart
            </Link>
          </Button>
        </div>
      </header>

      {bars.error ? <ApiErrorAlert error={bars.error} /> : null}

      <BarsTable
        bars={bars.data ?? []}
        limit={200}
        interval={interval}
        timeZone={zone}
      />
    </div>
  );
}
