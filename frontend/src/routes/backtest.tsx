import { useMemo, useState } from "react";
import { Play, Loader2 } from "lucide-react";
import {
  useBacktestCatalog,
  useBacktestRuns,
  useRunBacktest,
  type BacktestRunResponse,
  type EquityPoint,
  type RunSummary,
  type TradeOut,
} from "@/api/backtest";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { cn } from "@/lib/utils";

/**
 * Backtest Lab — the customer-facing strategy playground.
 * Build a config, run it against the engine, and see equity curve + metrics +
 * trades, with a history of recent runs. Backed by /api/v1/backtest/*.
 */
export function BacktestPage() {
  const catalog = useBacktestCatalog();
  const run = useRunBacktest();
  const runs = useBacktestRuns(20);

  const [strategy, setStrategy] = useState("alert_driven");
  const [source, setSource] = useState("divergence");
  const [side, setSide] = useState("both");
  const [rewardRisk, setRewardRisk] = useState(3);
  const [filters, setFilters] = useState<Set<string>>(
    new Set(["trend", "regime", "rsi_bull", "macd_bull"]),
  );
  const [minScore, setMinScore] = useState(3);
  const [riskPct, setRiskPct] = useState(1);
  const [maxRiskPct, setMaxRiskPct] = useState(5);
  const [symbols, setSymbols] = useState("AAPL,MSFT,NVDA,AMD,TSLA,META,AMZN,GOOGL,AVGO,NFLX");
  const [start, setStart] = useState("2022-01-01");
  const [end, setEnd] = useState("2025-12-31");
  const [interval, setInterval] = useState("1d");
  const [cash, setCash] = useState(100000);
  const [portfolio, setPortfolio] = useState(true);
  const [heat, setHeat] = useState(10);
  const [maxConcurrent, setMaxConcurrent] = useState(6);

  const isAlert = strategy === "alert_driven";

  function toggleFilter(name: string) {
    setFilters((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function submit() {
    const fNames = [...filters];
    const strategy_params = isAlert
      ? {
          source,
          source_params: {
            reward_risk_mult: rewardRisk,
            ...(source === "divergence" ? { lookback: 60, pivot_k: 3, kind: "both", side } : {}),
          },
          filters: fNames.map((name) => ({ name, params: {} })),
          filter_mode: fNames.length ? "score" : "all",
          min_score: fNames.length ? minScore : null,
          risk_pct: riskPct / 100,
          max_risk_pct: maxRiskPct / 100,
          min_reward_risk: 0,
        }
      : {};
    run.mutate({
      strategy,
      strategy_params,
      symbols: symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
      start: new Date(start + "T00:00:00Z").toISOString(),
      end: new Date(end + "T23:59:59Z").toISOString(),
      interval,
      benchmark: "SPY",
      portfolio,
      starting_cash: cash,
      max_concurrent_positions: maxConcurrent,
      max_portfolio_heat: heat / 100,
      store: true,
    });
  }

  const result = run.data;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 md:p-6">
      <header className="surface-panel rounded-lg p-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">strategy lab</p>
        <h1 className="mt-1 font-display text-2xl font-semibold text-fg-base">Backtest</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Build a strategy, run it against historical data, and inspect the equity curve,
          metrics, and trades. Runs are stored in your history below.
        </p>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[340px_1fr]">
        {/* ── Builder ─────────────────────────────────────────── */}
        <div className="surface-panel flex flex-col gap-3 overflow-auto rounded-lg p-4">
          <Field label="Strategy">
            <Select value={strategy} onChange={setStrategy} options={catalog.data?.strategies ?? [strategy]} />
          </Field>

          {isAlert && (
            <>
              <Field label="Signal source">
                <Select value={source} onChange={setSource} options={catalog.data?.signal_sources ?? [source]} />
              </Field>
              {source === "divergence" && (
                <Field label="Direction">
                  <Select value={side} onChange={setSide} options={["long", "short", "both"]} />
                </Field>
              )}
              <Field label="Confluence filters">
                <div className="flex flex-wrap gap-1">
                  {(catalog.data?.filters ?? []).map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => toggleFilter(f)}
                      className={cn(
                        "rounded border px-2 py-1 font-mono text-[10px]",
                        filters.has(f)
                          ? "border-accent bg-accent/15 text-accent"
                          : "border-border text-fg-muted hover:text-fg-base",
                      )}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <NumField label="Reward:Risk" value={rewardRisk} onChange={setRewardRisk} step={0.5} />
                <NumField label="Min confluence" value={minScore} onChange={setMinScore} step={1} />
                <NumField label="Base risk %" value={riskPct} onChange={setRiskPct} step={0.5} />
                <NumField label="Max risk %" value={maxRiskPct} onChange={setMaxRiskPct} step={0.5} />
              </div>
            </>
          )}

          <Field label="Symbols">
            <textarea
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              rows={2}
              className="w-full rounded border border-border bg-bg-base px-2 py-1 font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start"><DateInput value={start} onChange={setStart} /></Field>
            <Field label="End"><DateInput value={end} onChange={setEnd} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Interval">
              <Select value={interval} onChange={setInterval} options={["1d", "1h", "30m", "15m", "5m"]} />
            </Field>
            <NumField label="Capital $" value={cash} onChange={setCash} step={10000} />
          </div>

          <label className="flex items-center gap-2 text-xs text-fg-muted">
            <input type="checkbox" checked={portfolio} onChange={(e) => setPortfolio(e.target.checked)} />
            Portfolio mode (shared capital + risk caps)
          </label>
          {portfolio && (
            <div className="grid grid-cols-2 gap-2">
              <NumField label="Portfolio heat %" value={heat} onChange={setHeat} step={1} />
              <NumField label="Max concurrent" value={maxConcurrent} onChange={setMaxConcurrent} step={1} />
            </div>
          )}

          <button
            type="button"
            onClick={submit}
            disabled={run.isPending}
            className="mt-1 inline-flex items-center justify-center gap-2 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-fg disabled:opacity-60"
          >
            {run.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {run.isPending ? "Running…" : "Run backtest"}
          </button>
          {run.isError && <ApiErrorAlert error={run.error as Error} />}
        </div>

        {/* ── Results ─────────────────────────────────────────── */}
        <div className="flex min-h-0 flex-col gap-3 overflow-auto">
          {result ? (
            <Results result={result} />
          ) : (
            <div className="surface-panel flex flex-1 items-center justify-center rounded-lg p-8 text-sm text-fg-muted">
              {run.isPending ? "Running backtest…" : "Configure a strategy and run it to see results."}
            </div>
          )}
          <RunHistory runs={runs.data ?? []} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function Results({ result }: { result: BacktestRunResponse }) {
  const m = result.metrics;
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Total return" value={pct(m.total_return)} good={m.total_return >= 0} />
        <Metric label="Annualized" value={pct(m.annualized_return)} good={(m.annualized_return ?? 0) >= 0} />
        <Metric label="Sharpe" value={num(m.sharpe_ratio)} good={(m.sharpe_ratio ?? 0) >= 1} />
        <Metric label="Max drawdown" value={pct(m.max_drawdown)} good={false} />
        <Metric label="Win rate" value={pct(m.win_rate)} />
        <Metric label="Profit factor" value={num(m.profit_factor)} good={(m.profit_factor ?? 0) >= 1} />
        <Metric label="Round trips" value={String(closingCount(result.trades))} />
        <Metric label="Avg hold (d)" value={num(m.avg_holding_days, 0)} />
      </div>
      <div className="surface-panel rounded-lg p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Equity curve</span>
          <span className="font-mono text-xs text-fg-muted">${Math.round(m.final_equity).toLocaleString()}</span>
        </div>
        <EquityCurve points={result.equity_curve} />
      </div>
      <TradesTable trades={result.trades} />
    </>
  );
}

function EquityCurve({ points }: { points: EquityPoint[] }) {
  const path = useMemo(() => {
    if (points.length < 2) return null;
    const ys = points.map((p) => p.equity);
    const min = Math.min(...ys), max = Math.max(...ys);
    const range = max - min || 1;
    const W = 1000, H = 220;
    const step = W / (points.length - 1);
    const coords = points.map((p, i) => {
      const x = i * step;
      const y = H - ((p.equity - min) / range) * H;
      return [x, y] as const;
    });
    const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const area = `${line} L${W},${H} L0,${H} Z`;
    const up = points[points.length - 1].equity >= points[0].equity;
    return { line, area, up, W, H };
  }, [points]);
  if (!path) return <div className="py-8 text-center text-xs text-fg-muted">No equity data.</div>;
  const color = path.up ? "#22c55e" : "#f43f5e";
  return (
    <svg viewBox={`0 0 ${path.W} ${path.H}`} className="h-48 w-full" preserveAspectRatio="none">
      <path d={path.area} fill={color} opacity={0.12} />
      <path d={path.line} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function TradesTable({ trades }: { trades: TradeOut[] }) {
  const closed = trades.filter((t) => t.is_closing).slice(-40).reverse();
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
        Closed trades ({closed.length})
      </div>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="text-fg-subtle">
            <tr>
              <th className="py-1 pr-2">Symbol</th><th className="pr-2">Exit</th>
              <th className="pr-2 text-right">P&amp;L</th><th className="pr-2 text-right">Held</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {closed.map((t, i) => (
              <tr key={i} className="border-t border-border/50">
                <td className="py-1 pr-2 text-fg-base">{t.symbol}</td>
                <td className="pr-2 text-fg-muted">{t.side === "buy" ? "cover" : "sell"}</td>
                <td className={cn("pr-2 text-right", t.realized_pnl >= 0 ? "text-up" : "text-down")}>
                  {t.realized_pnl >= 0 ? "+" : ""}{Math.round(t.realized_pnl)}
                </td>
                <td className="pr-2 text-right text-fg-muted">{Math.round(t.holding_days)}d</td>
                <td className="text-fg-subtle">{t.timestamp.slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunHistory({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">Run history</div>
      {runs.length === 0 ? (
        <div className="py-4 text-center text-xs text-fg-muted">No stored runs yet.</div>
      ) : (
        <div className="max-h-48 overflow-auto">
          <table className="w-full text-left font-mono text-[11px]">
            <thead className="text-fg-subtle">
              <tr><th className="py-1 pr-2">Strategy</th><th className="pr-2 text-right">Return</th>
                <th className="pr-2 text-right">Sharpe</th><th className="pr-2 text-right">MaxDD</th>
                <th className="text-right">Trades</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id} className="border-t border-border/50">
                  <td className="py-1 pr-2 text-fg-base">{r.strategy_name}</td>
                  <td className={cn("pr-2 text-right", (r.total_return ?? 0) >= 0 ? "text-up" : "text-down")}>
                    {pct(r.total_return)}
                  </td>
                  <td className="pr-2 text-right text-fg-muted">{num(r.sharpe_ratio)}</td>
                  <td className="pr-2 text-right text-down">{pct(r.max_drawdown)}</td>
                  <td className="text-right text-fg-muted">{r.n_trades ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── small UI helpers ────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</span>
      {children}
    </label>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded border border-border bg-bg-base px-2 font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
    >
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function NumField({ label, value, onChange, step }: { label: string; value: number; onChange: (v: number) => void; step: number }) {
  return (
    <Field label={label}>
      <input
        type="number"
        value={value}
        step={step}
        onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) onChange(n); }}
        className="h-8 rounded border border-border bg-bg-base px-2 text-right font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
      />
    </Field>
  );
}

function DateInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <input
      type="date"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded border border-border bg-bg-base px-2 font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none"
    />
  );
}

function Metric({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className={cn("mt-1 font-mono text-lg", good === undefined ? "text-fg-base" : good ? "text-up" : "text-down")}>
        {value}
      </div>
    </div>
  );
}

function pct(x?: number | null): string {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`;
}
function num(x?: number | null, digits = 2): string {
  return x === null || x === undefined ? "—" : x.toFixed(digits);
}
function closingCount(trades: TradeOut[]): number {
  return trades.filter((t) => t.is_closing).length;
}
