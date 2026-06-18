import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, Search, Upload, X } from "lucide-react";
import {
  useAddFutures,
  useAddSeed,
  useFuturesCatalog,
  useFuturesUniverse,
  useImportSeed,
  useInstrumentLookup,
  useLatestBars,
  useRemoveFutures,
  useRemoveSeed,
  useSeedUniverse,
  type FuturesCatalogEntry,
  type FuturesUniverseEntry,
  type SeedEntry,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { SymbolSearchInput } from "@/components/symbol/SymbolSearchInput";
import { fmtInt } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Stream Service — the operator's "permanently streaming" set.
 *
 * Sticky-universe model (locked in
 * [docs/frontend_api_contracts.md §10.1]):
 *   - Adding a symbol here subscribes the Schwab stream + triggers
 *     historical backfill. The symbol becomes part of the streaming
 *     universe even if no watchlist holds it.
 *   - Removing a symbol here unsubscribes Schwab and marks the row
 *     inactive in `stream_universe`. Other watchlists holding the
 *     same symbol do NOT keep it streaming — only an add here puts
 *     a symbol on the stream, and only a remove here takes it off.
 */
type StreamTab = "equities" | "futures";

export function StreamPage() {
  const query = useSeedUniverse();
  const futures = useFuturesUniverse();
  const [tab, setTab] = useState<StreamTab>("equities");
  const [filter, setFilter] = useState("");

  const filtered: SeedEntry[] = useMemo(() => {
    const items = query.data?.items ?? [];
    const needle = filter.trim().toUpperCase();
    if (!needle) return items;
    return items.filter((i) => i.symbol.toUpperCase().includes(needle));
  }, [query.data, filter]);

  // Refresh acts on whichever tab is showing.
  const active = tab === "equities" ? query : futures;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-fg-base">
            Stream Service
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-fg-muted">
            Symbols streaming live into ClickHouse from Schwab — equities
            and CME futures roots. The single source of truth for the
            active streaming universe.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => active.refetch()}
          disabled={active.isFetching}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", active.isFetching && "animate-spin")}
          />
          Refresh
        </Button>
      </header>

      <StreamTabs
        value={tab}
        onChange={setTab}
        equitiesCount={query.data?.count}
        futuresCount={futures.data?.count}
      />

      {tab === "equities" ? (
        <div className="space-y-6">
          {query.data?.bootstrapped ? <BootstrapNotice /> : null}
          {query.error ? <ApiErrorAlert error={query.error} /> : null}
          <AddRow />
          <SearchBar value={filter} onChange={setFilter} />
          <StreamList entries={filtered} loading={query.isLoading} />
          <ImportPanel />
        </div>
      ) : (
        <FuturesPanel />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function StreamTabs({
  value,
  onChange,
  equitiesCount,
  futuresCount,
}: {
  value: StreamTab;
  onChange: (next: StreamTab) => void;
  equitiesCount?: number;
  futuresCount?: number;
}) {
  const tabs: { id: StreamTab; label: string; count?: number }[] = [
    { id: "equities", label: "Equities", count: equitiesCount },
    { id: "futures", label: "Futures", count: futuresCount },
  ];
  return (
    <div
      role="tablist"
      aria-label="Asset class"
      className="inline-flex rounded-md border border-border bg-bg-subtle p-0.5"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "rounded-sm px-3 py-1 text-sm font-medium transition-colors",
            value === t.id
              ? "bg-accent text-accent-fg"
              : "text-fg-muted hover:bg-bg-muted hover:text-fg-base",
          )}
        >
          {t.label}
          {t.count != null ? (
            <span className="ml-1.5 text-xs opacity-70">{fmtInt(t.count)}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

/**
 * Futures tab — the continuous CME roots streamed via Schwab
 * CHART_FUTURES. Separate CH table (`futures_universe`) from the equities
 * stream universe; read-only here, with live last prices from ClickHouse.
 */
function FuturesPanel() {
  const query = useFuturesUniverse();
  const entries = query.data?.items ?? [];

  const symbolsCsv = useMemo(
    () => entries.map((e) => e.symbol).join(","),
    [entries],
  );
  const quotes = useLatestBars(symbolsCsv || undefined);
  const priceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const item of quotes.data ?? []) {
      if (item.last != null) m.set(item.symbol.toUpperCase(), item.last);
    }
    return m;
  }, [quotes.data]);

  return (
    <div className="space-y-3">
      <p className="max-w-2xl text-sm text-fg-muted">
        Continuous CME roots streaming via Schwab CHART_FUTURES into{" "}
        <code className="rounded bg-bg-muted px-1 font-mono text-xs">
          futures_ohlcv_1m
        </code>
        . Add a root to subscribe it; click a root to chart it.
      </p>

      <FuturesAddRow />

      {query.error ? <ApiErrorAlert error={query.error} /> : null}

      <FuturesList
        entries={entries}
        loading={query.isLoading}
        priceMap={priceMap}
        pricesLoading={quotes.isLoading}
      />
    </div>
  );
}

function FuturesAddRow() {
  const add = useAddFutures();
  const catalog = useFuturesCatalog();
  const [symbol, setSymbol] = useState("");

  const doAdd = (raw: string) => {
    const norm = raw.trim().toUpperCase();
    if (!norm) return;
    const root = norm.startsWith("/") ? norm : `/${norm}`;
    add.mutate(root, { onSuccess: () => setSymbol("") });
  };

  return (
    <div className="space-y-2 rounded-md border border-border bg-bg-subtle p-4">
      <div className="flex flex-wrap items-start gap-2">
        <FuturesSearchInput
          value={symbol}
          onChange={setSymbol}
          onSubmit={doAdd}
          catalog={catalog.data?.items ?? []}
        />
        <Button
          type="button"
          onClick={() => doAdd(symbol)}
          disabled={!symbol.trim() || add.isPending}
        >
          <Plus className="h-4 w-4" />
          Add futures
        </Button>
      </div>
      {add.error ? <ApiErrorAlert error={add.error} /> : null}
    </div>
  );
}

/**
 * Autocomplete for the "add futures" field. Filters the known continuous
 * roots locally (symbol or description substring) — instant, no API call
 * per keystroke. Picking a suggestion adds it; free text still works for
 * roots outside the catalog. Mirrors the equities SymbolSearchInput UX.
 */
function FuturesSearchInput({
  value,
  onChange,
  onSubmit,
  catalog,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: (symbol: string) => void;
  catalog: ReadonlyArray<FuturesCatalogEntry>;
}) {
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const matches = useMemo(() => {
    const needle = value.trim().toUpperCase().replace(/^\//, "");
    if (!needle) return catalog.slice(0, 12);
    return catalog
      .filter(
        (c) =>
          c.symbol.replace(/^\//, "").includes(needle) ||
          c.description.toUpperCase().includes(needle),
      )
      .slice(0, 12);
  }, [value, catalog]);

  useEffect(() => setHighlight(0), [matches]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const pick = (entry: FuturesCatalogEntry) => {
    onSubmit(entry.symbol);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => (matches.length ? (h + 1) % matches.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (matches.length ? (h <= 0 ? matches.length - 1 : h - 1) : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && matches[highlight]) pick(matches[highlight]);
      else onSubmit(value);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const showDropdown = open && matches.length > 0;

  return (
    <div ref={wrapperRef} className="relative w-72">
      <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-bg-base px-3 focus-within:border-accent">
        <Search className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
        <input
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search futures, e.g. ES or Gold"
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={showDropdown}
          className="h-full flex-1 bg-transparent text-sm uppercase tracking-wide text-fg-base placeholder:normal-case placeholder:tracking-normal focus:outline-none"
        />
      </div>
      {showDropdown ? (
        <ul
          role="listbox"
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-border bg-bg-elevated shadow-lg"
        >
          {matches.map((m, idx) => (
            <li
              key={m.symbol}
              role="option"
              aria-selected={idx === highlight}
              onMouseEnter={() => setHighlight(idx)}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(m);
              }}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm",
                idx === highlight ? "bg-bg-muted" : "hover:bg-bg-muted/60",
              )}
            >
              <span className="font-mono font-semibold text-fg-base">
                {m.symbol}
              </span>
              <span className="truncate text-xs text-fg-muted">
                {m.description}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function FuturesList({
  entries,
  loading,
  priceMap,
  pricesLoading,
}: {
  entries: ReadonlyArray<FuturesUniverseEntry>;
  loading: boolean;
  priceMap: Map<string, number>;
  pricesLoading: boolean;
}) {
  const remove = useRemoveFutures();

  if (loading) {
    return (
      <ul className="space-y-1">
        {Array.from({ length: 8 }).map((_, i) => (
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
        No futures roots are streaming yet. Add one above.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border bg-bg-subtle">
      <table className="w-full text-sm">
        <thead className="bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Root</th>
            <th className="px-4 py-2 text-left font-medium">Contract</th>
            <th className="px-4 py-2 text-right font-medium">Last</th>
            <th className="px-4 py-2 text-right font-medium" aria-label="Actions" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {entries.map((e) => {
            const last = priceMap.get(e.symbol.toUpperCase());
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
                  {e.description || "—"}
                </td>
                <td className="px-4 py-2 text-right font-mono text-xs text-fg-base">
                  {last != null
                    ? last.toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })
                    : pricesLoading
                      ? "…"
                      : "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => remove.mutate(e.symbol)}
                    disabled={remove.isPending}
                    aria-label={`Remove ${e.symbol} from futures stream`}
                    title="Remove from stream — unsubscribes Schwab"
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

// ─────────────────────────────────────────────────────────────────────

function BootstrapNotice() {
  return (
    <div className="rounded-md border border-accent/40 bg-accent/10 px-4 py-3 text-sm text-fg-base">
      <span className="font-semibold">First-time setup:</span> Stream
      universe bootstrapped from the curated{" "}
      <code className="rounded bg-bg-muted px-1 font-mono text-xs">
        SEED_SYMBOLS
      </code>{" "}
      list + your current default watchlist. Future reads return whatever
      you've edited from here.
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

  const doAdd = (sym: string) => {
    const norm = sym.trim().toUpperCase();
    if (!norm) return;
    add.mutate(
      { symbol: norm, notes: null },
      {
        onSuccess: () => {
          setSymbol("");
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
        <Button
          type="button"
          onClick={() => doAdd(symbol)}
          disabled={!symbol.trim() || add.isPending}
        >
          <Plus className="h-4 w-4" />
          Add to stream
        </Button>
      </div>
      {add.error ? <ApiErrorAlert error={add.error} /> : null}
      {add.isSuccess && add.data?.changed?.length === 0 ? (
        <p className="text-xs text-fg-subtle">
          (Already streaming — no change.)
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

function StreamList({
  entries,
  loading,
}: {
  entries: ReadonlyArray<SeedEntry>;
  loading: boolean;
}) {
  const remove = useRemoveSeed();

  // Batch-lookup company descriptions for the rendered set.
  const symbols = useMemo(() => entries.map((e) => e.symbol), [entries]);
  const lookup = useInstrumentLookup(symbols);
  const descMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of lookup.data?.results ?? []) {
      if (r.description) m.set(r.symbol.toUpperCase(), r.description);
    }
    return m;
  }, [lookup.data]);

  // Last-price lookup straight from ClickHouse (the symbols are streaming
  // INTO ch, so the latest 1m close IS the last price). One fast query for
  // the whole universe — vs. the live market-banner, which fires a heavy
  // Schwab quote call per symbol and stalls at this scale.
  const symbolsCsv = useMemo(() => symbols.join(","), [symbols]);
  const quotes = useLatestBars(symbolsCsv || undefined);
  const priceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const item of quotes.data ?? []) {
      if (item.last != null) {
        m.set(item.symbol.toUpperCase(), item.last);
      }
    }
    return m;
  }, [quotes.data]);

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
            <th className="px-4 py-2 text-left font-medium">Company</th>
            <th className="px-4 py-2 text-right font-medium">Last</th>
            <th className="px-4 py-2 text-right font-medium" aria-label="Actions" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {entries.map((e) => {
            const desc = descMap.get(e.symbol.toUpperCase());
            const last = priceMap.get(e.symbol.toUpperCase());
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
                <td className="px-4 py-2 text-right font-mono text-xs text-fg-base">
                  {last != null
                    ? last.toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })
                    : quotes.isLoading
                      ? "…"
                      : "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => remove.mutate(e.symbol)}
                    disabled={remove.isPending}
                    aria-label={`Remove ${e.symbol} from stream universe`}
                    title="Remove from stream — unsubscribes Schwab"
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
