import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import {
  useAddMyWatchlistMembers,
  useCreateMyWatchlist,
  useDeleteMyWatchlist,
  useMyWatchlistDetail,
  useMyWatchlists,
} from "@/api/queries";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * My watchlists — per-user lists (identity Postgres). Every added symbol
 * carries a pretend position (default 100 shares stamped at the add-time
 * price); the table shows the return since it was added. Refreshes every
 * 30s against live prices.
 */
export function MyWatchlists() {
  const lists = useMyWatchlists();
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const create = useCreateMyWatchlist();
  const del = useDeleteMyWatchlist();

  const active = selected ?? lists.data?.[0]?.name ?? null;

  return (
    <div className="surface-panel rounded-lg p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-accent">
            my watchlists
          </div>
          <p className="mt-0.5 text-xs text-fg-muted">
            Each symbol is tracked as a pretend position from the moment you add it
            (default 100 shares) — the return column answers “what if I’d bought when I
            watchlisted it?”
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="new list name"
            className="w-36 rounded border border-border bg-bg-base px-2 py-1 text-xs text-fg-base focus:border-accent focus:outline-none"
          />
          <Button
            size="sm"
            disabled={!newName.trim() || create.isPending}
            onClick={() =>
              create.mutate(
                { name: newName.trim() },
                { onSuccess: (w) => { setSelected(w.name); setNewName(""); } },
              )
            }
          >
            <Plus className="h-3.5 w-3.5" /> Create
          </Button>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {(lists.data ?? []).map((w) => (
          <button
            key={w.id}
            onClick={() => setSelected(w.name)}
            className={cn(
              "rounded border px-2.5 py-1 text-xs",
              active === w.name
                ? "border-accent bg-accent/15 text-accent"
                : "border-border text-fg-muted hover:text-fg-base",
            )}
          >
            {w.name} <span className="opacity-60">({w.n_members})</span>
          </button>
        ))}
        {lists.data?.length === 0 && (
          <span className="text-xs text-fg-muted">No lists yet — create one to start.</span>
        )}
      </div>

      {active && (
        <MyWatchlistDetailPanel
          name={active}
          onDelete={() => del.mutate(active, { onSuccess: () => setSelected(null) })}
        />
      )}
    </div>
  );
}

function MyWatchlistDetailPanel({ name, onDelete }: { name: string; onDelete: () => void }) {
  const detail = useMyWatchlistDetail(name);
  const add = useAddMyWatchlistMembers();
  const remove = useRemoveMyWatchlistMember();
  const [symbols, setSymbols] = useState("");
  const [qty, setQty] = useState<number>(100);

  const d = detail.data;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <input
          value={symbols}
          onChange={(e) => setSymbols(e.target.value)}
          placeholder="AAPL, NVDA, …"
          className="w-44 rounded border border-border bg-bg-base px-2 py-1 font-mono text-xs text-fg-base focus:border-accent focus:outline-none"
        />
        <label className="flex items-center gap-1 text-xs text-fg-muted">
          qty
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(Number(e.target.value) || 100)}
            className="w-16 rounded border border-border bg-bg-base px-2 py-1 font-mono text-xs text-fg-base focus:border-accent focus:outline-none"
          />
        </label>
        <Button
          size="sm"
          disabled={!symbols.trim() || add.isPending}
          onClick={() =>
            add.mutate(
              {
                name,
                symbols: symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
                quantity: qty,
              },
              { onSuccess: () => setSymbols("") },
            )
          }
        >
          <Plus className="h-3.5 w-3.5" /> Add
        </Button>
        <div className="flex-1" />
        {d?.total_pnl_usd != null && (
          <span
            className={cn(
              "font-mono text-sm",
              d.total_pnl_usd >= 0 ? "text-up" : "text-down",
            )}
          >
            total {d.total_pnl_usd >= 0 ? "+" : ""}
            {d.total_pnl_usd.toFixed(0)} USD
          </span>
        )}
        <Button size="sm" variant="ghost" onClick={onDelete} title="Delete this watchlist">
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="max-h-80 overflow-auto">
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="text-fg-subtle">
            <tr>
              <th className="py-1 pr-2">Symbol</th>
              <th className="pr-2 text-right">Qty</th>
              <th className="pr-2 text-right">Entry</th>
              <th className="pr-2">Added</th>
              <th className="pr-2 text-right">Now</th>
              <th className="pr-2 text-right">P&L $</th>
              <th className="pr-2 text-right">P&L %</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(d?.members ?? []).map((m) => (
              <tr key={m.symbol} className="border-t border-border/50">
                <td className="py-1 pr-2 text-fg-base">{m.symbol}</td>
                <td className="pr-2 text-right text-fg-muted">{m.quantity.toFixed(0)}</td>
                <td className="pr-2 text-right text-fg-muted">
                  {m.entry_price != null ? m.entry_price.toFixed(2) : "—"}
                </td>
                <td className="pr-2 text-fg-subtle">{m.entry_at.slice(0, 10)}</td>
                <td className="pr-2 text-right text-fg-muted">
                  {m.current_price != null ? m.current_price.toFixed(2) : "—"}
                </td>
                <td
                  className={cn(
                    "pr-2 text-right",
                    (m.pnl_usd ?? 0) >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {m.pnl_usd != null ? `${m.pnl_usd >= 0 ? "+" : ""}${m.pnl_usd.toFixed(0)}` : "—"}
                </td>
                <td
                  className={cn(
                    "pr-2 text-right",
                    (m.pnl_pct ?? 0) >= 0 ? "text-up" : "text-down",
                  )}
                >
                  {m.pnl_pct != null
                    ? `${m.pnl_pct >= 0 ? "+" : ""}${(m.pnl_pct * 100).toFixed(2)}%`
                    : "—"}
                </td>
                <td className="text-right">
                  <button
                    onClick={() => remove.mutate({ name, symbol: m.symbol })}
                    className="text-fg-subtle hover:text-down"
                    title={`Remove ${m.symbol}`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </td>
              </tr>
            ))}
            {d && d.members.length === 0 && (
              <tr>
                <td colSpan={8} className="py-4 text-center text-fg-muted">
                  Empty list — add symbols above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
