import { useMemo, useState } from "react";
import { Bell, Loader2, Radio } from "lucide-react";
import { usePaperStatus, type EquityPoint, type PaperStatus } from "@/api/backtest";
import { cn } from "@/lib/utils";

/**
 * Paper Trading — the live forward track record. Replay the locked momentum
 * strategy forward from any start date with any starting capital (rebased on the
 * fly); the slice after the locked go-live is the real forward record.
 */
export function PaperPage() {
  const [capital, setCapital] = useState(100_000);
  const [startDate, setStartDate] = useState(""); // "" = default to locked go-live
  const q = usePaperStatus("momentum_top15", startDate || undefined, capital);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4 md:p-6">
      <header className="surface-panel rounded-lg p-4">
        <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
          <Radio className="h-3.5 w-3.5" /> live track record
        </p>
        <h1 className="mt-1 font-display text-2xl font-semibold text-fg-base">Paper Trading</h1>
        <p className="mt-1 text-sm text-fg-muted">
          The locked momentum strategy, run forward against live data. Set a starting
          balance and a start date — including a past year — to replay it forward.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Starting capital $</span>
            <input type="number" value={capital} step={10000} min={1000}
              onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n) && n > 0) setCapital(n); }}
              className="h-8 w-36 rounded border border-border bg-bg-base px-2 text-right font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Start date (replay from)</span>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
              className="h-8 rounded border border-border bg-bg-base px-2 font-mono text-[11px] text-fg-base focus:border-accent focus:outline-none" />
          </label>
          {startDate && (
            <button type="button" onClick={() => setStartDate("")}
              className="h-8 rounded border border-border px-2 text-[11px] text-fg-muted hover:text-fg-base">
              reset to go-live
            </button>
          )}
        </div>
      </header>

      {q.isLoading && (
        <div className="surface-panel flex flex-1 items-center justify-center rounded-lg p-8 text-sm text-fg-muted">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> loading track record…
        </div>
      )}
      {q.isError && (
        <div className="surface-panel rounded-lg p-8 text-center text-sm text-fg-muted">
          No paper run yet. Seed it with
          <code className="mx-1 rounded bg-bg-base px-1.5 py-0.5 text-xs">scripts/paper_trade_run.py</code>.
        </div>
      )}
      {q.data && <PaperBody s={q.data} />}
    </div>
  );
}

function PaperBody({ s }: { s: PaperStatus }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Current balance" value={money(s.current_balance)} good={s.current_balance >= s.starting_capital} big />
        <Metric label="Return" value={pct(s.forward_return)} good={s.forward_return >= 0} big />
        <Metric label="Started with" value={money(s.starting_capital)} />
        <Metric label="Trades" value={s.forward_n_trades + (s.forward_win_rate != null ? ` · ${Math.round(s.forward_win_rate * 100)}% win` : "")} />
      </div>
      <p className="text-xs text-fg-muted">
        Replaying from <span className="text-fg-base">{s.start_date.slice(0, 10)}</span>
        {Date.parse(s.start_date) < Date.parse(s.go_live)
          ? " (before go-live — includes backtest replay)"
          : " · live forward record"} · {s.days_live} days · {s.n_open_positions} positions open.
      </p>

      <TodaysSignals s={s} />

      <div className="surface-panel rounded-lg p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
            Equity curve
          </span>
          <span className="font-mono text-xs text-fg-muted">
            {money(s.starting_capital)} → {money(s.current_balance)}
          </span>
        </div>
        <EquityWithMarker points={s.equity_curve} goLive={s.go_live} />
        <p className="mt-1 text-[10px] text-fg-subtle">
          Balance from {money(s.starting_capital)} at {s.start_date.slice(0, 10)}.
          The <span className="text-accent">dashed marker</span> is the locked go-live — only the segment to its right is the real (no-look-ahead) forward record; anything left of it is backtest replay.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Holdings positions={s.open_positions} />
        <ForwardTrades trades={s.forward_trades} />
      </div>
    </>
  );
}

function TodaysSignals({ s }: { s: PaperStatus }) {
  const date = (s.computed_through ?? s.go_live).slice(0, 10);
  const entries = s.today_entries ?? [];
  const exits = s.today_exits ?? [];
  const has = entries.length > 0 || exits.length > 0;
  if (!has) {
    return (
      <div className="surface-panel rounded-lg p-3 text-xs text-fg-muted">
        <Bell className="mr-1.5 inline h-3.5 w-3.5" /> No new entry/exit signals for {date}.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-accent/40 bg-accent/10 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
        <Bell className="h-3.5 w-3.5" /> Signals for {date}
      </div>
      <div className="flex flex-wrap gap-2 font-mono text-[11px]">
        {entries.map((p) => (
          <span key={`e-${p.symbol}`} className="rounded border border-up/50 px-2 py-1 text-up">
            ENTRY {p.quantity >= 0 ? "LONG" : "SHORT"} {p.symbol} @ ${p.avg_entry_price.toFixed(2)}
          </span>
        ))}
        {exits.map((t, i) => (
          <span key={`x-${i}`} className={cn("rounded border px-2 py-1",
            t.realized_pnl >= 0 ? "border-up/50 text-up" : "border-down/50 text-down")}>
            EXIT {t.symbol} {t.realized_pnl >= 0 ? "+" : ""}{Math.round(t.realized_pnl).toLocaleString()}
          </span>
        ))}
      </div>
    </div>
  );
}

function EquityWithMarker({ points, goLive }: { points: EquityPoint[]; goLive: string }) {
  const g = useMemo(() => {
    if (points.length < 2) return null;
    const ys = points.map((p) => p.equity);
    const min = Math.min(...ys), max = Math.max(...ys);
    const range = max - min || 1;
    const W = 1000, H = 240;
    const step = W / (points.length - 1);
    const coord = (p: EquityPoint, i: number) => [i * step, H - ((p.equity - min) / range) * H] as const;
    const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${coord(p, i)[0].toFixed(1)},${coord(p, i)[1].toFixed(1)}`).join(" ");
    const area = `${line} L${W},${H} L0,${H} Z`;
    const goTs = Date.parse(goLive);
    let liveIdx = points.findIndex((p) => Date.parse(p.t) >= goTs);
    if (liveIdx < 0) liveIdx = points.length - 1;
    const markerX = liveIdx * step;
    const up = points[points.length - 1].equity >= points[0].equity;
    return { line, area, markerX, W, H, up };
  }, [points, goLive]);
  if (!g) return (
    <div className="py-8 text-center text-xs text-fg-muted">
      No forward history yet — the live record just started. Set an earlier <strong>start date</strong> above to replay the strategy forward from a past date.
    </div>
  );
  const color = g.up ? "#22c55e" : "#f43f5e";
  return (
    <svg viewBox={`0 0 ${g.W} ${g.H}`} className="h-56 w-full" preserveAspectRatio="none">
      <path d={g.area} fill={color} opacity={0.1} />
      <path d={g.line} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      <line x1={g.markerX} y1={0} x2={g.markerX} y2={g.H} stroke="var(--accent, #38bdf8)"
            strokeWidth={2} strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Holdings({ positions }: { positions: PaperStatus["open_positions"] }) {
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
        Currently holding ({positions.length})
      </div>
      {positions.length === 0 ? (
        <div className="py-4 text-center text-xs text-fg-muted">Flat — no open positions.</div>
      ) : (
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="text-fg-subtle">
            <tr><th className="py-1 pr-2">Symbol</th><th className="pr-2">Entered</th>
              <th className="pr-2 text-right">Qty</th><th className="pr-2 text-right">Entry $</th>
              <th className="text-right">Unreal. P&amp;L</th></tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol} className="border-t border-border/50">
                <td className="py-1 pr-2 text-fg-base">{p.symbol}{p.quantity < 0 ? " (short)" : ""}</td>
                <td className="pr-2 text-fg-subtle">{p.entry_time.slice(0, 10)}</td>
                <td className="pr-2 text-right text-fg-muted">{Math.round(p.quantity)}</td>
                <td className="pr-2 text-right text-fg-muted">${p.avg_entry_price.toFixed(2)}</td>
                <td className={cn("text-right", p.unrealized_pnl >= 0 ? "text-up" : "text-down")}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}{Math.round(p.unrealized_pnl).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ForwardTrades({ trades }: { trades: PaperStatus["forward_trades"] }) {
  const closed = trades.filter((t) => t.is_closing).slice(-30).reverse();
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
        Forward closed trades ({closed.length})
      </div>
      {closed.length === 0 ? (
        <div className="py-4 text-center text-xs text-fg-muted">
          No closed forward trades yet — the live record just started.
        </div>
      ) : (
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="text-fg-subtle">
            <tr><th className="py-1 pr-2">Symbol</th><th className="pr-2">Entered</th>
              <th className="pr-2">Exited</th><th className="pr-2 text-right">Held</th>
              <th className="text-right">P&amp;L</th></tr>
          </thead>
          <tbody>
            {closed.map((t, i) => (
              <tr key={i} className="border-t border-border/50">
                <td className="py-1 pr-2 text-fg-base">{t.symbol}</td>
                <td className="pr-2 text-fg-subtle">{(t.entry_date ?? "").slice(0, 10) || "—"}</td>
                <td className="pr-2 text-fg-subtle">{(t.exit_date ?? t.timestamp).slice(0, 10)}</td>
                <td className="pr-2 text-right text-fg-muted">{Math.round(t.holding_days)}d</td>
                <td className={cn("text-right", t.realized_pnl >= 0 ? "text-up" : "text-down")}>
                  {t.realized_pnl >= 0 ? "+" : ""}{Math.round(t.realized_pnl).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Metric({ label, value, good, big }: { label: string; value: string; good?: boolean; big?: boolean }) {
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className={cn(big ? "text-2xl" : "text-lg", "mt-1 font-mono",
        good === undefined ? "text-fg-base" : good ? "text-up" : "text-down")}>
        {value}
      </div>
    </div>
  );
}

function pct(x: number): string {
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`;
}

function money(x: number): string {
  return `$${Math.round(x).toLocaleString()}`;
}
