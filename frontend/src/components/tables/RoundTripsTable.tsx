import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { pairRoundTrips, type TradeLeg } from "@/lib/roundTrips";

/**
 * Generic entry -> exit trade table. Strategy-agnostic: pass any run's raw
 * trade legs; opening and closing fills are paired into round trips
 * (long/short, partial closes handled). Used by the Backtest Lab and the
 * paper dashboard.
 */
export function RoundTripsTable({
  trades,
  title = "Closed trades",
  emptyText = "No closed trades in this run.",
  maxRows = 60,
}: {
  trades: TradeLeg[];
  title?: string;
  emptyText?: string;
  maxRows?: number;
}) {
  const rows = useMemo(() => pairRoundTrips(trades).slice(-maxRows).reverse(), [trades, maxRows]);
  return (
    <div className="surface-panel rounded-lg p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">
        {title} ({rows.length})
      </div>
      {rows.length === 0 ? (
        <div className="py-4 text-center text-xs text-fg-muted">{emptyText}</div>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left font-mono text-[11px]">
            <thead className="text-fg-subtle">
              <tr>
                <th className="py-1 pr-2">Symbol</th>
                <th className="pr-2">Dir</th>
                <th className="pr-2">Entry</th>
                <th className="pr-2">Exit</th>
                <th className="pr-2 text-right">P&amp;L</th>
                <th className="text-right">Held</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border/50" title={r.note || undefined}>
                  <td className="py-1 pr-2 text-fg-base">{r.symbol}</td>
                  <td className="pr-2 text-fg-muted">{r.direction}</td>
                  <td className="pr-2 text-fg-muted">
                    {r.entryDate ? r.entryDate.slice(0, 10) : "—"}
                    {Number.isFinite(r.entryPrice) ? ` @ ${r.entryPrice.toFixed(2)}` : ""}
                  </td>
                  <td className="pr-2 text-fg-muted">
                    {r.exitDate.slice(0, 10)} @ {r.exitPrice.toFixed(2)}
                  </td>
                  <td className={cn("pr-2 text-right", r.pnl >= 0 ? "text-up" : "text-down")}>
                    {r.pnl >= 0 ? "+" : ""}
                    {Math.round(r.pnl)}
                  </td>
                  <td className="text-right text-fg-muted">{Math.round(r.holdingDays)}d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
