import { useState } from "react";
import { Bell, ChevronDown, ChevronRight, Loader2, Lock, Sparkles } from "lucide-react";
import {
  useLeaderboard, useLibrary, useStrategyAlerts, useStrategyOwnerStats,
  type StrategyAlert, type StrategyOwnerStats, type StrategyPublic,
} from "@/api/library";
import { cn } from "@/lib/utils";

/**
 * Strategy Library — the subscription front door. Subscribers see redacted cards
 * (track record + actionable alerts, never the recipe). An owner/dev toggle reveals
 * backtest + simulated stats for comparing and improving strategies.
 */
export function LibraryPage() {
  const q = useLibrary();
  const [owner, setOwner] = useState(false);
  const leaderboard = useLeaderboard(owner);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-auto p-4 md:p-6">
      <header className="surface-panel rounded-lg p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
              <Sparkles className="h-3.5 w-3.5" /> strategy library
            </p>
            <h1 className="mt-1 font-display text-2xl font-semibold text-fg-base">Strategy Library</h1>
            <p className="mt-1 text-sm text-fg-muted">
              Subscribe to a strategy and get its live alerts — entries, stops and targets —
              plus a verified track record. The strategy's recipe stays private.
            </p>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-xs text-fg-muted">
            <input type="checkbox" checked={owner} onChange={(e) => setOwner(e.target.checked)} />
            Owner view
          </label>
        </div>
      </header>

      {q.isLoading && (
        <div className="surface-panel flex flex-1 items-center justify-center rounded-lg p-8 text-sm text-fg-muted">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> loading strategies…
        </div>
      )}
      {q.data && q.data.length === 0 && (
        <div className="surface-panel rounded-lg p-8 text-center text-sm text-fg-muted">
          No strategies published yet. Register one with
          <code className="mx-1 rounded bg-bg-base px-1.5 py-0.5 text-xs">scripts/register_strategy.py</code>.
        </div>
      )}
      {owner && leaderboard.data && leaderboard.data.length > 0 && (
        <Leaderboard rows={leaderboard.data} />
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {q.data?.map((s) => <StrategyCard key={s.name} s={s} owner={owner} />)}
      </div>
    </div>
  );
}

function Leaderboard({ rows }: { rows: StrategyOwnerStats[] }) {
  // Rank by backtest Sharpe (in-sample R&D signal); paper is the honest forward.
  const sorted = [...rows].sort((a, b) => (b.backtest?.sharpe_ratio ?? -99) - (a.backtest?.sharpe_ratio ?? -99));
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-500">
        <Lock className="h-3.5 w-3.5" /> owner leaderboard — backtest vs simulated
      </div>
      <div className="overflow-auto">
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="text-fg-subtle">
            <tr>
              <th className="py-1 pr-3">Strategy</th>
              <th className="pr-3 text-right">BT return</th>
              <th className="pr-3 text-right">BT Sharpe</th>
              <th className="pr-3 text-right">BT PF</th>
              <th className="pr-3 text-right">BT maxDD</th>
              <th className="pr-3 text-right">BT trades</th>
              <th className="pr-3 text-right">Paper ret</th>
              <th className="pr-3 text-right">Paper days</th>
              <th className="text-right">Balance</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const b = r.backtest ?? {};
              return (
                <tr key={r.name} className="border-t border-border/50">
                  <td className="py-1 pr-3 text-fg-base">
                    {i === 0 && <span className="mr-1 text-amber-500">★</span>}{r.title}
                  </td>
                  <td className={cn("pr-3 text-right", (b.total_return ?? 0) >= 0 ? "text-up" : "text-down")}>{pct(b.total_return)}</td>
                  <td className="pr-3 text-right text-fg-base">{num(b.sharpe_ratio)}</td>
                  <td className="pr-3 text-right text-fg-base">{num(b.profit_factor)}</td>
                  <td className="pr-3 text-right text-down">{pct(b.max_drawdown)}</td>
                  <td className="pr-3 text-right text-fg-muted">{Math.round(b.n_trades ?? 0)}</td>
                  <td className={cn("pr-3 text-right", (r.paper_return ?? 0) >= 0 ? "text-up" : "text-down")}>{pct(r.paper_return)}</td>
                  <td className="pr-3 text-right text-fg-muted">{r.paper_days}</td>
                  <td className="text-right text-fg-muted">${Math.round(r.current_balance ?? 0).toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 text-[10px] text-fg-subtle">
        BT = backtest (full history, in-sample R&D). Paper = live simulated forward record (post-go-live, never used for tuning) — the honest comparison as it accrues.
      </p>
    </div>
  );
}

function StrategyCard({ s, owner }: { s: StrategyPublic; owner: boolean }) {
  const [showAlerts, setShowAlerts] = useState(false);
  const alerts = useStrategyAlerts(s.name, showAlerts);
  const ownerStats = useStrategyOwnerStats(s.name, owner);

  return (
    <div className="surface-panel flex flex-col gap-3 rounded-lg p-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="font-display text-lg font-semibold text-fg-base">{s.title}</h2>
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-fg-subtle">{s.category}</span>
        </div>
        <p className="mt-0.5 text-sm text-fg-muted">{s.tagline}</p>
      </div>

      {/* Track record (subscriber-safe results) */}
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
        <Chip label="Return" value={pct(s.forward_return)} good={(s.forward_return ?? 0) >= 0} />
        <Chip label="Win" value={s.forward_win_rate != null ? `${Math.round(s.forward_win_rate * 100)}%` : "—"} />
        <Chip label="Trades" value={String(s.forward_n_trades)} />
        <Chip label="Days live" value={String(s.days_live)} />
        <Chip label="Open" value={String(s.n_open_positions)} />
      </div>

      <button type="button" onClick={() => setShowAlerts((v) => !v)}
        className="inline-flex w-fit items-center gap-1 text-xs font-semibold text-accent">
        {showAlerts ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <Bell className="h-3.5 w-3.5" /> {showAlerts ? "Hide alerts" : "View alerts"}
      </button>

      {showAlerts && (
        <div className="rounded border border-border/60 p-2">
          {alerts.isLoading && <div className="py-3 text-center text-xs text-fg-muted">loading alerts…</div>}
          {alerts.data && <AlertsFeed alerts={alerts.data} />}
        </div>
      )}

      {owner && <OwnerPanel stats={ownerStats.data} loading={ownerStats.isLoading} name={s.name} />}
    </div>
  );
}

function AlertsFeed({ alerts }: { alerts: StrategyAlert[] }) {
  const open = alerts.filter((a) => a.status === "open");
  const closed = alerts.filter((a) => a.status === "closed");
  return (
    <div className="flex flex-col gap-2 font-mono text-[11px]">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Active signals ({open.length})</div>
      {open.length === 0 && <div className="text-fg-muted">No open positions right now.</div>}
      {open.map((a) => (
        <div key={`o-${a.symbol}`} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className={cn("font-semibold", a.direction === "long" ? "text-up" : "text-down")}>
            {a.direction.toUpperCase()} {a.symbol}
          </span>
          <span className="text-fg-muted">entry ${fmt(a.entry)}</span>
          <span className="text-down">stop ${fmt(a.stop)}</span>
          <span className="text-up">target ${fmt(a.target)}</span>
          {a.current != null && <span className="text-fg-subtle">now ${fmt(a.current)}</span>}
        </div>
      ))}
      {closed.length > 0 && (
        <>
          <div className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Recent closed ({closed.length})</div>
          {closed.slice(0, 8).map((a, i) => (
            <div key={`c-${i}`} className="flex items-center gap-x-3">
              <span className="text-fg-base">{a.symbol}</span>
              <span className="text-fg-subtle">{a.date.slice(0, 10)}</span>
              <span className={cn(a.pnl != null && a.pnl >= 0 ? "text-up" : "text-down")}>
                {a.pnl != null ? `${a.pnl >= 0 ? "+" : ""}${Math.round(a.pnl).toLocaleString()}` : "—"}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function OwnerPanel({ stats, loading, name }: { stats?: StrategyOwnerStats; loading: boolean; name: string }) {
  const b = stats?.backtest ?? null;
  return (
    <div className="rounded border border-amber-500/40 bg-amber-500/5 p-2">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-amber-500">
        <Lock className="h-3 w-3" /> owner / dev — {name}
      </div>
      {loading && <div className="py-2 text-center text-xs text-fg-muted">loading stats…</div>}
      {b && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Chip label="BT return" value={pct(b.total_return)} good={(b.total_return ?? 0) >= 0} />
          <Chip label="Sharpe" value={num(b.sharpe_ratio)} good={(b.sharpe_ratio ?? 0) >= 1} />
          <Chip label="Max DD" value={pct(b.max_drawdown)} />
          <Chip label="Profit factor" value={num(b.profit_factor)} good={(b.profit_factor ?? 0) >= 1} />
          <Chip label="Win" value={b.win_rate != null ? `${Math.round(b.win_rate * 100)}%` : "—"} />
          <Chip label="BT trades" value={String(Math.round(b.n_trades ?? 0))} />
        </div>
      )}
      {stats && (
        <p className="mt-1.5 text-[10px] text-fg-subtle">
          Backtest = full history (in-sample R&D). Simulated (paper) = {pct(stats.paper_return)} over {stats.paper_days} days,
          {" "}{stats.paper_trades} trades, balance ${Math.round(stats.current_balance ?? 0).toLocaleString()}.
          Full recipe at <code className="rounded bg-bg-base px-1">/api/v1/strategies/{name}</code>.
        </p>
      )}
    </div>
  );
}

function Chip({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="rounded bg-bg-base/60 p-2">
      <div className="text-[9px] uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className={cn("mt-0.5 font-mono text-sm", good === undefined ? "text-fg-base" : good ? "text-up" : "text-down")}>{value}</div>
    </div>
  );
}

function pct(x?: number | null): string {
  return x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
}
function num(x?: number | null): string {
  return x == null ? "—" : x.toFixed(2);
}
function fmt(x?: number | null): string {
  return x == null ? "—" : x.toFixed(2);
}
