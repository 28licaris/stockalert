import { Link, useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { OhlcvChart } from "@/components/charts/OhlcvChart";
import { BarsTable } from "@/components/tables/BarsTable";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import {
  useSymbolBars,
  useSymbolSignals,
} from "@/api/queries";
import { useUserSetting } from "@/lib/storage";
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

  const bars = useSymbolBars(ticker || undefined, interval, 500);
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
        <IntervalPicker value={interval} onChange={setInterval} />
      </header>

      {bars.error ? <ApiErrorAlert error={bars.error} /> : null}

      <OhlcvChart bars={bars.data ?? []} signals={signals.data ?? []} />

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          Recent bars
        </h2>
        <BarsTable bars={bars.data ?? []} limit={50} />
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

function SymbolPicker() {
  const navigate = useNavigate();
  const [recent, setRecent] = useUserSetting<string[]>(
    "symbol.recent",
    [],
  );
  const [input, setInput] = useState("");

  const go = (t: string) => {
    const sym = t.trim().toUpperCase();
    if (!sym) return;
    setRecent((prev) => {
      const next = [sym, ...prev.filter((p) => p !== sym)].slice(0, 12);
      return next;
    });
    navigate(`/symbol/${encodeURIComponent(sym)}`);
  };

  useEffect(() => {
    // Auto-focus the input on mount so keyboard flow is fast.
    const el = document.getElementById("symbol-input");
    el?.focus();
  }, []);

  return (
    <div className="mx-auto max-w-xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold text-fg-base">Symbol</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Enter a ticker to view OHLCV bars, signals, and indicators.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          go(input);
        }}
        className="flex gap-2"
      >
        <label htmlFor="symbol-input" className="sr-only">
          Ticker
        </label>
        <div className="flex flex-1 items-center gap-2 rounded-md border border-border bg-bg-subtle px-3">
          <Search className="h-4 w-4 text-fg-subtle" />
          <input
            id="symbol-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="AAPL"
            autoComplete="off"
            spellCheck={false}
            className="h-9 flex-1 bg-transparent text-sm uppercase tracking-wide text-fg-base focus:outline-none"
          />
        </div>
        <Button type="submit" disabled={!input.trim()}>
          Open
        </Button>
      </form>

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
