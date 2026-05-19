import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, Trash2, X } from "lucide-react";
import {
  useAddWatchlistMembers,
  useCreateWatchlist,
  useDeleteWatchlist,
  useRemoveWatchlistMembers,
  useWatchlists,
  type Watchlist,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { fmtAgo, fmtInt } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Watchlists — CRUD over the user's named watchlists.
 *
 * The current sticky-universe model (locked in
 * [docs/frontend_api_contracts.md §10.1]): adding a symbol to a
 * watchlist that isn't already in the universe will subscribe the
 * Schwab stream + backfill history; removing from a watchlist will
 * NOT unsubscribe streaming (universe is sticky).
 *
 * UI today (FE-3 first cut):
 *   - List active watchlists; member counts; click name to expand.
 *   - Create a new watchlist.
 *   - Add / remove members on the expanded list.
 *   - Soft-delete a watchlist.
 *
 * Deferred (follow-ons):
 *   - Rename (PATCH endpoint typed; UI deferred).
 *   - Live last-price column (would call /watchlists/{name}/snapshot
 *     on a 10s tick — defer until silver→CH hot-load is done).
 *   - "Use as screener universe" button (waits for FE-3 screener page).
 *   - Drag-drop member reorder.
 */
export function WatchlistsPage() {
  const query = useWatchlists();
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-fg-base">Watchlists</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Organize symbols into named lists. Adding to a watchlist will
            subscribe the live stream for symbols not already in the universe.
          </p>
        </div>
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
      </header>

      {query.error ? <ApiErrorAlert error={query.error} /> : null}

      <CreateWatchlistForm />

      <section className="grid gap-3 md:grid-cols-3">
        <WatchlistList
          watchlists={query.data ?? []}
          selected={selected}
          onSelect={setSelected}
          isLoading={query.isLoading}
        />
        <div className="md:col-span-2">
          {selected ? (
            <WatchlistDetail
              key={selected}
              watchlist={(query.data ?? []).find((w) => w.name === selected) ?? null}
            />
          ) : (
            <EmptyDetail />
          )}
        </div>
      </section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function WatchlistList({
  watchlists,
  selected,
  onSelect,
  isLoading,
}: {
  watchlists: ReadonlyArray<Watchlist>;
  selected: string | null;
  onSelect: (name: string) => void;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <ul className="space-y-1">
        {Array.from({ length: 3 }).map((_, i) => (
          <li
            key={i}
            className="h-12 animate-pulse rounded-md border border-border bg-bg-subtle"
          />
        ))}
      </ul>
    );
  }

  if (watchlists.length === 0) {
    return (
      <div className="rounded-md border border-border bg-bg-subtle p-4 text-sm text-fg-muted">
        No watchlists yet. Create one above.
      </div>
    );
  }

  return (
    <ul className="space-y-1">
      {watchlists.map((wl) => (
        <li key={wl.name}>
          <button
            type="button"
            onClick={() => onSelect(wl.name)}
            className={cn(
              "flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors",
              selected === wl.name
                ? "border-accent bg-bg-elevated"
                : "border-border bg-bg-subtle hover:bg-bg-muted",
            )}
          >
            <span>
              <span className="font-medium text-fg-base">{wl.name}</span>
              <span className="ml-2 text-[10px] uppercase tracking-wider text-fg-subtle">
                {wl.kind}
              </span>
            </span>
            <span className="font-mono text-xs text-fg-muted">
              {fmtInt(wl.member_count)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function EmptyDetail() {
  return (
    <div className="grid h-full min-h-[200px] place-items-center rounded-md border border-dashed border-border bg-bg-subtle text-sm text-fg-subtle">
      Select a watchlist on the left
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function WatchlistDetail({ watchlist }: { watchlist: Watchlist | null }) {
  const remove = useRemoveWatchlistMembers();
  const del = useDeleteWatchlist();
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!watchlist) {
    return <EmptyDetail />;
  }
  const isDefault = watchlist.name === "default";

  return (
    <div className="rounded-lg border border-border bg-bg-subtle">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h2 className="font-semibold text-fg-base">{watchlist.name}</h2>
          <p className="text-xs text-fg-subtle">
            {watchlist.kind} · {fmtInt(watchlist.member_count)} members ·
            updated {fmtAgo(watchlist.updated_at)}
          </p>
        </div>
        {!isDefault ? (
          confirmDelete ? (
            <span className="flex items-center gap-2 text-xs">
              <span className="text-fg-muted">Delete watchlist?</span>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => del.mutate(watchlist.name)}
                disabled={del.isPending}
              >
                Confirm
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(false)}
              >
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setConfirmDelete(true)}
              aria-label="Delete watchlist"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )
        ) : (
          <span className="text-[10px] uppercase tracking-wider text-fg-subtle">
            shim · cannot delete
          </span>
        )}
      </div>

      {del.error ? (
        <div className="border-b border-border px-4 py-2">
          <ApiErrorAlert error={del.error} />
        </div>
      ) : null}

      <AddMembersForm name={watchlist.name} />

      <MembersList
        watchlistName={watchlist.name}
        members={watchlist.members ?? []}
        onRemove={(symbol) =>
          remove.mutate({ name: watchlist.name, symbols: [symbol] })
        }
        isPending={remove.isPending}
      />

      {remove.error ? (
        <div className="border-t border-border px-4 py-2">
          <ApiErrorAlert error={remove.error} />
        </div>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

function CreateWatchlistForm() {
  const create = useCreateWatchlist();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [open, setOpen] = useState(false);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate(
      { name: trimmed, kind: "user", description: description.trim() },
      {
        onSuccess: () => {
          setName("");
          setDescription("");
          setOpen(false);
        },
      },
    );
  };

  if (!open) {
    return (
      <div>
        <Button type="button" variant="outline" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" />
          New watchlist
        </Button>
      </div>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-2 rounded-md border border-border bg-bg-subtle p-4"
    >
      <div className="flex flex-wrap gap-2">
        <input
          name="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="watchlist name"
          autoFocus
          maxLength={64}
          className="h-9 flex-1 rounded-md border border-border bg-bg-base px-3 text-sm text-fg-base focus:border-accent focus:outline-none"
        />
        <input
          name="description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="description (optional)"
          maxLength={500}
          className="h-9 flex-[2] rounded-md border border-border bg-bg-base px-3 text-sm text-fg-base focus:border-accent focus:outline-none"
        />
        <Button type="submit" disabled={!name.trim() || create.isPending}>
          Create
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setOpen(false);
            setName("");
            setDescription("");
          }}
        >
          Cancel
        </Button>
      </div>
      {create.error ? <ApiErrorAlert error={create.error} /> : null}
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────

function AddMembersForm({ name }: { name: string }) {
  const add = useAddWatchlistMembers();
  const [input, setInput] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const symbols = input
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (symbols.length === 0) return;
    add.mutate(
      { name, symbols },
      {
        onSuccess: () => setInput(""),
      },
    );
  };

  return (
    <form onSubmit={submit} className="border-b border-border px-4 py-3">
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Add symbols (comma- or space-separated)"
          className="h-9 flex-1 rounded-md border border-border bg-bg-base px-3 font-mono text-sm uppercase text-fg-base focus:border-accent focus:outline-none"
        />
        <Button type="submit" disabled={!input.trim() || add.isPending}>
          <Plus className="h-4 w-4" />
          Add
        </Button>
      </div>
      {add.error ? <div className="mt-2"><ApiErrorAlert error={add.error} /></div> : null}
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────

function MembersList({
  watchlistName,
  members,
  onRemove,
  isPending,
}: {
  watchlistName: string;
  members: ReadonlyArray<string>;
  onRemove: (symbol: string) => void;
  isPending: boolean;
}) {
  if (members.length === 0) {
    return (
      <div className="px-4 py-6 text-center text-sm text-fg-subtle">
        No members yet. Add symbols above.
      </div>
    );
  }
  return (
    <ul className="divide-y divide-border-subtle">
      {members.map((symbol) => (
        <li
          key={symbol}
          className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-bg-muted/40"
        >
          <Link
            to={`/symbol/${encodeURIComponent(symbol)}`}
            className="font-mono text-sm font-medium text-fg-base hover:text-accent"
          >
            {symbol}
          </Link>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => onRemove(symbol)}
            disabled={isPending}
            aria-label={`Remove ${symbol} from ${watchlistName}`}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </li>
      ))}
    </ul>
  );
}
