import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, Search, Upload, X } from "lucide-react";
import {
  useAddSeed,
  useImportSeed,
  useInstrumentLookup,
  useRemoveSeed,
  useSeedUniverse,
  type SeedEntry,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import { fmtAgo, fmtInt } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Seed Universe — the operator's "permanently streaming" set.
 *
 * Sticky-universe model (locked in
 * [docs/frontend_api_contracts.md §10.1]):
 *   - Adding a symbol here subscribes the Schwab stream + triggers
 *     historical backfill. The symbol becomes part of the streaming
 *     universe even if no watchlist holds it.
 *   - Removing a symbol here calls the refcount path on the default
 *     watchlist. If another watchlist holds the same symbol, it KEEPS
 *     streaming (sticky). This page is the only explicit "stop
 *     streaming this symbol" surface.
 *
 * UX:
 *   - List with search filter + count
 *   - Add single symbol
 *   - Bulk import (paste a comma- or space-separated list)
 *   - Remove single symbol (× per row, no confirm — refcount keeps
 *     other holders streaming)
 *   - Bootstrap banner: first read after CH table creation populates
 *     from SEED_SYMBOLS ∪ default-watchlist; the banner says so once.
 */
export function SeedPage() {
  const query = useSeedUniverse();
  const [filter, setFilter] = useState("");

  const filtered: SeedEntry[] = useMemo(() => {
    const items = query.data?.items ?? [];
    const needle = filter.trim().toUpperCase();
    if (!needle) return items;
    return items.filter((i) => i.symbol.toUpperCase().includes(needle));
  }, [query.data, filter]);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-fg-base">
            Seed Universe
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-fg-muted">
            The set of symbols permanently part of the streaming
            universe. Adding here subscribes the Schwab stream and
            triggers historical backfill. Removing here is the only
            explicit way to stop streaming.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-fg-subtle">
          <span>{fmtInt(query.data?.count)} symbols</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", query.isFetching && "animate-spin")}
            />
            Refresh
          </Button>
        </div>
      </header>

      {query.data?.bootstrapped ? <BootstrapNotice /> : null}
      {query.error ? <ApiErrorAlert error={query.error} /> : null}

      <AddRow />

      <SearchBar value={filter} onChange={setFilter} />

      <SeedList entries={filtered} loading={query.isLoading} />

      <ImportPanel />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function BootstrapNotice() {
  return (
    <div className="rounded-md border border-accent/40 bg-accent/10 px-4 py-3 text-sm text-fg-base">
      <span className="font-semibold">First-time setup:</span> Seed universe
      bootstrapped from the curated <code className="rounded bg-bg-muted px-1 font-mono text-xs">SEED_SYMBOLS</code> list +
      your current default watchlist. Future reads return whatever you've
      edited from here.
    </div>
  );
}

function SearchBar({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-bg-subtle px-3">
      <Search className="h-4 w-4 text-fg-subtle" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter by symbol"
        className="flex-1 bg-transparent text-sm uppercase tracking-wide text-fg-base focus:outline-none"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange("")}
          className="text-fg-subtle hover:text-fg-base"
          aria-label="Clear filter"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function AddRow() {
  const add = useAddSeed();
  const [symbol, setSymbol] = useState("");
  const [notes, setNotes] = useState("");

  const doAdd = (sym: string) => {
    const norm = sym.trim().toUpperCase();
    if (!norm) return;
    add.mutate(
      { symbol: norm, notes: notes.trim() || null },
      {
        onSuccess: () => {
          setSymbol("");
          setNotes("");
        },
      },
    );
  };

  return (
    <div className="space-y-2 rounded-md border border-border bg-bg-subtle p-4">
      <div className="flex flex-wrap gap-2">
        <SymbolSearchInput
          value={symbol}
          onChange={setSymbol}
          onSubmit={(value, match) => doAdd(match ? match.symbol : value)}
          placeholder="Search ticker"
          className="w-60"
        />
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="notes (optional)"
          maxLength={500}
          className="h-9 flex-1 rounded-md border border-border bg-bg-base px-3 text-sm text-fg-base focus:border-accent focus:outline-none"
        />
        <Button
          type="button"
          onClick={() => doAdd(symbol)}
          disabled={!symbol.trim() || add.isPending}
        >
          <Plus className="h-4 w-4" />
          Add to seed
        </Button>
      </div>
      {add.error ? <ApiErrorAlert error={add.error} /> : null}
      {add.isSuccess && add.data?.changed?.length === 0 ? (
        <p className="text-xs text-fg-subtle">
          (Already in seed universe — no change.)
        </p>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function ImportPanel() {
  const imp = useImportSeed();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const symbols = text
      .split(/[,\s\n]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (symbols.length === 0) return;
    imp.mutate(
      { symbols, notes: null },
      {
        onSuccess: () => {
          setText("");
          setOpen(false);
        },
      },
    );
  };

  if (!open) {
    return (
      <div>
        <Button type="button" variant="outline" onClick={() => setOpen(true)}>
          <Upload className="h-4 w-4" />
          Bulk import
        </Button>
      </div>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-2 rounded-md border border-border bg-bg-subtle p-4"
    >
      <div className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
        Bulk import — paste symbols (comma, space, or newline separated)
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="AAPL, NVDA, GOOGL&#10;TSLA AMZN"
        rows={4}
        className="w-full rounded-md border border-border bg-bg-base px-3 py-2 font-mono text-sm text-fg-base focus:border-accent focus:outline-none"
      />
      <div className="flex gap-2">
        <Button type="submit" disabled={!text.trim() || imp.isPending}>
          Import
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setOpen(false);
            setText("");
          }}
        >
          Cancel
        </Button>
        {imp.isSuccess ? (
          <span className="self-center text-xs text-fg-muted">
            {imp.data?.changed?.length ?? 0} added · {imp.data?.count ?? 0}{" "}
            total
          </span>
        ) : null}
      </div>
      {imp.error ? <ApiErrorAlert error={imp.error} /> : null}
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────

function SeedList({
  entries,
  loading,
}: {
  entries: ReadonlyArray<SeedEntry>;
  loading: boolean;
}) {
  const remove = useRemoveSeed();

  // Batch-lookup company descriptions for the rendered set. Memoizing
  // the symbol array keeps the hook's cache stable across re-renders.
  const symbols = useMemo(() => entries.map((e) => e.symbol), [entries]);
  const lookup = useInstrumentLookup(symbols);
  const descMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of lookup.data?.results ?? []) {
      if (r.description) m.set(r.symbol.toUpperCase(), r.description);
    }
    return m;
  }, [lookup.data]);

  if (loading) {
    return (
      <ul className="space-y-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <li
            key={i}
            className="h-10 animate-pulse rounded-md border border-border bg-bg-subtle"
          />
        ))}
      </ul>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border bg-bg-subtle p-6 text-center text-sm text-fg-subtle">
        No symbols match the filter.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border bg-bg-subtle">
      <table className="w-full text-sm">
        <thead className="bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Symbol</th>
            <th className="px-4 py-2 text-left font-medium">Description</th>
            <th className="px-4 py-2 text-left font-medium">Added</th>
            <th className="px-4 py-2 text-left font-medium">By</th>
            <th className="px-4 py-2 text-left font-medium">Notes</th>
            <th className="px-4 py-2 text-right font-medium" aria-label="Actions" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {entries.map((e) => {
            const desc = descMap.get(e.symbol.toUpperCase());
            return (
              <tr key={e.symbol} className="hover:bg-bg-muted/40">
                <td className="px-4 py-2">
                  <Link
                    to={`/symbol/${encodeURIComponent(e.symbol)}`}
                    className="font-mono font-medium text-fg-base hover:text-accent"
                  >
                    {e.symbol}
                  </Link>
                </td>
                <td className="px-4 py-2 text-xs text-fg-muted">
                  {desc ?? (lookup.isLoading ? "…" : "")}
                </td>
                <td className="px-4 py-2 text-xs text-fg-muted">
                  {fmtAgo(e.added_at)}
                </td>
                <td className="px-4 py-2 text-xs text-fg-subtle">
                  {e.added_by || "—"}
                </td>
                <td className="px-4 py-2 text-xs text-fg-muted">
                  {e.notes || ""}
                </td>
                <td className="px-4 py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => remove.mutate(e.symbol)}
                    disabled={remove.isPending}
                    aria-label={`Remove ${e.symbol} from seed universe`}
                    title="Remove from seed (decrements default-watchlist refcount)"
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {remove.error ? (
        <div className="border-t border-border p-2">
          <ApiErrorAlert error={remove.error} />
        </div>
      ) : null}
    </div>
  );
}
