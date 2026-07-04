import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Trash2, X } from "lucide-react";
import {
  useAddMyWatchlistMembers,
  useCreateMyWatchlist,
  useDeleteMyWatchlist,
  useInstrumentLookup,
  useMyWatchlistDetail,
  useMyWatchlists,
  useRemoveMyWatchlistMember,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import { fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Per-user watchlists. Each symbol carries a pretend position (default 100
 * shares) stamped at its add-time price; the table shows the live return
 * since it was added. Company names come from /instruments/lookup.
 */
export function MyWatchlists() {
  const lists = useMyWatchlists();
  const create = useCreateMyWatchlist();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const active = selected ?? lists.data?.[0]?.name ?? null;

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const n = newName.trim();
    if (!n) return;
    create.mutate(
      { name: n },
      {
        onSuccess: (w) => {
          setSelected(w.name);
          setNewName("");
          setCreating(false);
        },
      },
    );
  };

  const isEmpty = !lists.isLoading && (lists.data?.length ?? 0) === 0;

  return (
    <div className="space-y-4">
      {/* List selector + create */}
      <div className="flex flex-wrap items-center gap-2">
        {(lists.data ?? []).map((w) => (
          <button
            key={w.id}
            type="button"
            onClick={() => setSelected(w.name)}
            className={cn(
              "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors",
              active === w.name
                ? "border-accent/60 bg-accent/10 text-fg-base shadow-[0_0_24px_rgba(46,196,255,0.08)]"
                : "border-border bg-bg-subtle/70 text-fg-muted hover:border-border hover:text-fg-base",
            )}
          >
            <span className="font-medium">{w.name}</span>
            <span className="rounded-full bg-bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-fg-subtle">
              {w.n_members}
            </span>
          </button>
        ))}

        {creating ? (
          <form onSubmit={submitCreate} className="inline-flex items-center gap-1">
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="list name"
              maxLength={64}
              className="h-8 w-36 rounded-full border border-border bg-bg-base px-3 text-sm text-fg-base focus:border-accent focus:outline-none"
            />
            <Button size="sm" type="submit" disabled={!newName.trim() || create.isPending}>
              Create
            </Button>
            <Button
              size="sm"
              variant="ghost"
              type="button"
              onClick={() => {
                setCreating(false);
                setNewName("");
              }}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />
            New list
          </Button>
        )}
      </div>

      {create.error ? <ApiErrorAlert error={create.error} /> : null}
      {lists.error ? <ApiErrorAlert error={lists.error} /> : null}

      {lists.isLoading ? (
        <div className="h-40 animate-pulse rounded-lg border border-border bg-bg-subtle/70" />
      ) : isEmpty ? (
        <div className="surface-panel rounded-lg p-10 text-center">
          <p className="text-sm text-fg-muted">
            No watchlists yet. Create your first list to start tracking symbols.
          </p>
          {!creating ? (
            <Button className="mt-3" size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" />
              New list
            </Button>
          ) : null}
        </div>
      ) : active ? (
        <WatchlistPanel key={active} name={active} onDeleted={() => setSelected(null)} />
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function WatchlistPanel({ name, onDeleted }: { name: string; onDeleted: () => void }) {
  const detail = useMyWatchlistDetail(name);
  const add = useAddMyWatchlistMembers();
  const remove = useRemoveMyWatchlistMember();
  const del = useDeleteMyWatchlist();
  const [input, setInput] = useState("");
  const [qty, setQty] = useState(100);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const members = useMemo(() => detail.data?.members ?? [], [detail.data]);
  const symbols = useMemo(() => members.map((m) => m.symbol), [members]);
  const lookup = useInstrumentLookup(symbols);
  const nameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of lookup.data?.results ?? []) {
      if (r.description) m.set(r.symbol.toUpperCase(), r.description);
    }
    return m;
  }, [lookup.data]);

  const parse = (v: string) =>
    v.split(/[,\s\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
  const doAdd = (syms: string[]) => {
    if (syms.length === 0) return;
    add.mutate({ name, symbols: syms, quantity: qty }, { onSuccess: () => setInput("") });
  };

  const total = detail.data?.total_pnl_usd ?? null;

  return (
    <div className="surface-panel overflow-hidden rounded-lg">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="font-semibold text-fg-base">{name}</h2>
          <p className="text-xs text-fg-subtle">
            {members.length} position{members.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-4">
          {total != null ? (
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-wider text-fg-subtle">
                Total P&L
              </div>
              <div
                className={cn(
                  "font-mono text-sm tabular-nums",
                  total >= 0 ? "text-up" : "text-down",
                )}
              >
                {total >= 0 ? "+" : ""}
                {total.toLocaleString(undefined, { maximumFractionDigits: 0 })} USD
              </div>
            </div>
          ) : null}
          {confirmDelete ? (
            <span className="flex items-center gap-2 text-xs">
              <span className="text-fg-muted">Delete list?</span>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => del.mutate(name, { onSuccess: onDeleted })}
                disabled={del.isPending}
              >
                Confirm
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              size="icon"
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
              aria-label="Delete watchlist"
              title="Delete this watchlist"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Add row */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <SymbolSearchInput
          value={input}
          onChange={setInput}
          onSubmit={(value, match) =>
            match ? doAdd([match.symbol]) : doAdd(parse(value))
          }
          placeholder="Add a ticker — search by symbol or company"
          className="min-w-[220px] flex-1"
        />
        <label className="flex items-center gap-1.5 text-xs text-fg-muted">
          shares
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(Number(e.target.value) || 100)}
            className="h-9 w-20 rounded-md border border-border bg-bg-base px-2 text-sm tabular-nums text-fg-base focus:border-accent focus:outline-none"
          />
        </label>
        <Button onClick={() => doAdd(parse(input))} disabled={!input.trim() || add.isPending}>
          <Plus className="h-4 w-4" />
          Add
        </Button>
      </div>
      {add.error ? (
        <div className="px-4 py-2">
          <ApiErrorAlert error={add.error} />
        </div>
      ) : null}

      {/* Positions table */}
      {detail.isLoading ? (
        <div className="px-4 py-10 text-center text-sm text-fg-subtle">Loading…</div>
      ) : members.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-fg-subtle">
          No symbols yet — add one above to start tracking a pretend position.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-bg-muted/50 text-[10px] uppercase tracking-wider text-fg-subtle">
              <tr>
                <th className="px-4 py-2 text-left font-medium">Symbol</th>
                <th className="px-4 py-2 text-right font-medium">Shares</th>
                <th className="px-4 py-2 text-left font-medium">Added</th>
                <th className="px-4 py-2 text-right font-medium">Entry</th>
                <th className="px-4 py-2 text-right font-medium">Now</th>
                <th className="px-4 py-2 text-right font-medium">P&amp;L $</th>
                <th className="px-4 py-2 text-right font-medium">P&amp;L %</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {members.map((m) => {
                const nm = nameMap.get(m.symbol.toUpperCase());
                const up = (m.pnl_usd ?? 0) >= 0;
                return (
                  <tr key={m.symbol} className="hover:bg-bg-muted/40">
                    <td className="px-4 py-2">
                      <Link
                        to={`/charts/${encodeURIComponent(m.symbol)}`}
                        className="font-medium text-fg-base hover:text-accent"
                      >
                        {m.symbol}
                      </Link>
                      <div className="max-w-[220px] truncate text-xs text-fg-subtle">
                        {nm ?? (lookup.isLoading ? "…" : "")}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-fg-muted">
                      {m.quantity.toFixed(0)}
                    </td>
                    <td className="px-4 py-2 text-xs text-fg-subtle">
                      {m.entry_at.slice(0, 10)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-fg-muted">
                      {m.entry_price != null ? fmtPrice(m.entry_price) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-fg-base">
                      {m.current_price != null ? fmtPrice(m.current_price) : "—"}
                    </td>
                    <td
                      className={cn(
                        "px-4 py-2 text-right tabular-nums",
                        up ? "text-up" : "text-down",
                      )}
                    >
                      {m.pnl_usd != null
                        ? `${up ? "+" : ""}${m.pnl_usd.toLocaleString(undefined, {
                            maximumFractionDigits: 0,
                          })}`
                        : "—"}
                    </td>
                    <td
                      className={cn(
                        "px-4 py-2 text-right tabular-nums",
                        (m.pnl_pct ?? 0) >= 0 ? "text-up" : "text-down",
                      )}
                    >
                      {m.pnl_pct != null
                        ? `${m.pnl_pct >= 0 ? "+" : ""}${(m.pnl_pct * 100).toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => remove.mutate({ name, symbol: m.symbol })}
                        disabled={remove.isPending}
                        aria-label={`Remove ${m.symbol}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="border-t border-border px-4 py-2 text-[11px] text-fg-subtle">
        Each position is simulated from the price when the symbol was added;
        entry price, current price, and P&amp;L update automatically.
      </div>
      {remove.error ? (
        <div className="px-4 pb-2">
          <ApiErrorAlert error={remove.error} />
        </div>
      ) : null}
    </div>
  );
}
