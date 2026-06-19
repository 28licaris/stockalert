import { Link, useNavigate, useParams } from "react-router-dom";
import { useState } from "react";
import { OhlcvChart } from "@/components/charts/OhlcvChart";
import { BarsTable } from "@/components/tables/BarsTable";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import {
  useLakeBars,
  useSymbolSignals,
} from "@/api/queries";
import { useUserSetting } from "@/lib/storage";
import {
  DEFAULT_TZ,
  resolveZone,
  TZ_OPTIONS,
  type TzSetting,
} from "@/lib/timezone";
import { fmtAgo, fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;
type Interval = (typeof INTERVALS)[number];

const DEFAULT_INTERVAL: Interval = "5m";

/**
 * Symbol page — FE-2 parity scaffold.
 *
 * Today: OHLCV candle + volume + signal markers + recent bars table.
 * Coming in subsequent phases:
 *   - indicator overlays (FE-2.1; the /api/indicators/series endpoint
 *     already exists)
 *   - coverage strip beneath the chart (FE-7)
 *   - journal-trades-on-this-ticker panel (FE-8)
 *   - adjusted/raw price toggle (once silver lands the _adj columns)
 */
export function SymbolPage() {
  const params = useParams();
  const ticker = (params.ticker ?? "").toUpperCase();

  const [interval, setInterval] = useUserSetting<Interval>(
    "symbol.interval",
    DEFAULT_INTERVAL,
  );
  // Global display timezone for the chart axis + Recent Bars table.
  const [tz, setTz] = useUserSetting<TzSetting>("chart.timezone", DEFAULT_TZ);
  const zone = resolveZone(tz);

  const bars = useLakeBars(ticker || undefined, interval);
  const signals = useSymbolSignals(ticker || undefined, 100);

  if (!ticker) {
    return <SymbolPicker />;
  }

  const latest = bars.data?.at(-1);
  const prevClose = bars.data?.at(-2)?.close;
  const change =
    latest && prevClose ? ((latest.close - prevClose) / prevClose) * 100 : null;

  return (
    <div className="flex h-full flex-col gap-4 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg-base">
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
                : "Loading…"}
            </span>
          </div>
        </div>
        <div className="flex items-end gap-2">
          <TimezonePicker value={tz} onChange={setTz} />
          <IntervalPicker value={interval} onChange={setInterval} />
        </div>
      </header>

      {bars.error ? <ApiErrorAlert error={bars.error} /> : null}

      {/* Out-of-universe symbols are fetched live from Schwab on first view,
          which takes a few seconds. Overlay the (still-mounted) chart so we
          don't fight its lifecycle, then fall back to an explicit empty state
          when the fetch settles with no data (e.g. an unknown ticker). */}
      <div className="relative">
        <OhlcvChart
          bars={bars.data ?? []}
          signals={signals.data ?? []}
          timezone={zone}
        />
        {!bars.data || bars.data.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center rounded-md bg-bg-base/60 text-sm text-fg-muted backdrop-blur-[1px]">
            {bars.isLoading || bars.isFetching
              ? "Fetching history…"
              : `No data available for ${ticker}`}
          </div>
        ) : null}
      </div>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          Recent bars
        </h2>
        {/* A compact "is it live?" snapshot — the chart is the main view, so we
            cap this to the latest ~20 bars rather than let it grow the page. */}
        <BarsTable
          bars={bars.data ?? []}
          limit={20}
          interval={interval}
          timeZone={zone}
        />
      </section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function IntervalPicker({
  value,
  onChange,
}: {
  value: Interval;
  onChange: (next: Interval) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Interval"
      className="inline-flex rounded-md border border-border bg-bg-subtle p-0.5"
    >
      {INTERVALS.map((i) => (
        <button
          key={i}
          type="button"
          role="tab"
          aria-selected={value === i}
          onClick={() => onChange(i)}
          className={cn(
            "rounded-sm px-2.5 py-1 font-mono text-xs",
            value === i
              ? "bg-accent text-accent-fg"
              : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
          )}
        >
          {i}
        </button>
      ))}
    </div>
  );
}

function TimezonePicker({
  value,
  onChange,
}: {
  value: TzSetting;
  onChange: (next: TzSetting) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Timezone"
      className="inline-flex rounded-md border border-border bg-bg-subtle p-0.5"
    >
      {TZ_OPTIONS.map((tz) => (
        <button
          key={tz.value}
          type="button"
          role="tab"
          aria-selected={value === tz.value}
          onClick={() => onChange(tz.value)}
          className={cn(
            "rounded-sm px-2.5 py-1 font-mono text-xs",
            value === tz.value
              ? "bg-accent text-accent-fg"
              : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
          )}
        >
          {tz.label}
        </button>
      ))}
    </div>
  );
}

function SymbolPicker() {
  const navigate = useNavigate();
  const [recent, setRecent] = useUserSetting<string[]>(
    "symbol.recent",
    [],
  );
  const [input, setInput] = useState("");

  const go = (sym: string) => {
    const norm = sym.trim().toUpperCase();
    if (!norm) return;
    setRecent((prev) => {
      const next = [norm, ...prev.filter((p) => p !== norm)].slice(0, 12);
      return next;
    });
    navigate(`/symbol/${encodeURIComponent(norm)}`);
  };

  return (
    <div className="mx-auto max-w-xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-fg-base">Symbol</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Search by ticker or company name. Pick a suggestion or hit
          Enter on what you've typed.
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
        <Button
          type="button"
          onClick={() => go(input)}
          disabled={!input.trim()}
        >
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
                to={`/symbol/${encodeURIComponent(sym)}`}
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
