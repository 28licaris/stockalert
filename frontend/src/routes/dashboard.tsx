import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  ListChecks,
  Newspaper,
  Sparkles,
  Trophy,
} from "lucide-react";
import {
  useMarketBanner,
  useMyWatchlistDetail,
  useMyWatchlists,
  useNewsDigest,
  useStrategyLeaderboard,
} from "@/api/queries";
import { useChatStore } from "@/stores/chat";
import { useUserSetting } from "@/lib/storage";
import { useCurrentUser } from "@/auth/useCurrentUser";
import { Button } from "@/components/ui/button";
import { fmtPct, fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Customer home — an AI-assistant-first dashboard. The assistant is the hero;
 * glanceable widgets (watchlists, strategy track records, market, news) give
 * at-a-glance context and link to their full pages. See
 * docs/customer_surface_plan.md.
 */
export function DashboardPage() {
  const user = useCurrentUser();
  const first = (user.displayName || "").trim().split(/\s+/)[0] || "there";

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <MarketStrip />
      <AssistantHero firstName={first} />
      <div className="grid gap-4 lg:grid-cols-2">
        <WatchlistCard />
        <StrategyCard />
      </div>
      <NewsCard />
    </div>
  );
}

// ── market strip ─────────────────────────────────────────────────────

function MarketStrip() {
  const q = useMarketBanner();
  const items = q.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg border border-border bg-bg-subtle/60 px-4 py-2.5 text-sm">
      {items.slice(0, 8).map((it) => {
        const up = (it.change_pct ?? 0) >= 0;
        return (
          <span key={it.label} className="inline-flex items-center gap-2">
            <span className="font-medium text-fg-base">{it.label}</span>
            <span className="tabular-nums text-fg-muted">{fmtPrice(it.last)}</span>
            <span className={cn("tabular-nums", up ? "text-up" : "text-down")}>
              {fmtPct(it.change_pct)}
            </span>
          </span>
        );
      })}
    </div>
  );
}

// ── AI assistant hero ────────────────────────────────────────────────

const SUGGESTED = [
  "What's moving in the market today?",
  "How are my watchlists performing?",
  "Summarize the latest news on NVDA",
  "Which strategy has the best track record?",
];

function AssistantHero({ firstName }: { firstName: string }) {
  const [, setChatOpen] = useUserSetting<boolean>("ui.chat.open", false);
  const send = useChatStore((s) => s.send);
  const [q, setQ] = useState("");

  const ask = (text: string) => {
    const t = text.trim();
    if (!t) return;
    setChatOpen(true);
    void send(t);
    setQ("");
  };

  return (
    <section className="surface-panel relative overflow-hidden rounded-xl p-6 md:p-8">
      <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-accent/10 blur-3xl" />
      <div className="relative">
        <div className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
          <Sparkles className="h-3.5 w-3.5" />
          AI Assistant
        </div>
        <h1 className="mt-2 font-display text-2xl font-semibold text-fg-base md:text-3xl">
          Good to see you, {firstName}. What do you want to know?
        </h1>
        <p className="mt-1 text-sm text-fg-muted">
          Ask about any ticker, your watchlists, a strategy, or the market — the
          assistant has live data and your account context.
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(q);
          }}
          className="mt-4 flex gap-2"
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask anything… e.g. “Is NVDA overextended right now?”"
            className="h-11 flex-1 rounded-lg border border-border bg-bg-base/70 px-4 text-sm text-fg-base focus:border-accent focus:outline-none"
          />
          <Button type="submit" size="lg" disabled={!q.trim()}>
            Ask
            <ArrowRight className="h-4 w-4" />
          </Button>
        </form>

        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTED.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => ask(s)}
              className="rounded-full border border-border bg-bg-subtle/70 px-3 py-1 text-xs text-fg-muted transition-colors hover:border-accent/50 hover:text-fg-base"
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── widget shell ─────────────────────────────────────────────────────

function Card({
  title,
  icon: Icon,
  to,
  children,
}: {
  title: string;
  icon: typeof ListChecks;
  to: string;
  children: React.ReactNode;
}) {
  return (
    <section className="surface-panel flex flex-col rounded-xl">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="inline-flex items-center gap-2 text-sm font-semibold text-fg-base">
          <Icon className="h-4 w-4 text-accent" />
          {title}
        </div>
        <Link
          to={to}
          className="inline-flex items-center gap-1 text-xs text-fg-subtle hover:text-accent"
        >
          View all <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>
      <div className="flex-1 p-4">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-6 text-center text-sm text-fg-subtle">{children}</div>;
}

// ── watchlist card ───────────────────────────────────────────────────

function WatchlistCard() {
  const lists = useMyWatchlists();
  const firstName = lists.data?.[0]?.name ?? null;
  const detail = useMyWatchlistDetail(firstName);
  const members = detail.data?.members ?? [];
  const total = detail.data?.total_pnl_usd ?? null;

  return (
    <Card title="Your watchlists" icon={ListChecks} to="/watchlists">
      {lists.isLoading ? (
        <Empty>Loading…</Empty>
      ) : (lists.data?.length ?? 0) === 0 ? (
        <Empty>
          No watchlists yet.{" "}
          <Link to="/watchlists" className="text-accent hover:underline">
            Create one
          </Link>{" "}
          to start tracking.
        </Empty>
      ) : (
        <div>
          <div className="mb-3 flex items-baseline justify-between">
            <span className="text-sm text-fg-muted">{firstName}</span>
            {total != null ? (
              <span
                className={cn(
                  "font-mono text-sm tabular-nums",
                  total >= 0 ? "text-up" : "text-down",
                )}
              >
                {total >= 0 ? "+" : ""}
                {total.toLocaleString(undefined, { maximumFractionDigits: 0 })} USD
              </span>
            ) : null}
          </div>
          <ul className="space-y-1.5">
            {[...members]
              .sort((a, b) => (b.pnl_pct ?? 0) - (a.pnl_pct ?? 0))
              .slice(0, 5)
              .map((m) => {
                const up = (m.pnl_pct ?? 0) >= 0;
                return (
                  <li key={m.symbol} className="flex items-center justify-between text-sm">
                    <Link
                      to={`/charts/${encodeURIComponent(m.symbol)}`}
                      className="font-medium text-fg-base hover:text-accent"
                    >
                      {m.symbol}
                    </Link>
                    <span
                      className={cn(
                        "font-mono tabular-nums",
                        up ? "text-up" : "text-down",
                      )}
                    >
                      {m.pnl_pct != null
                        ? `${up ? "+" : ""}${(m.pnl_pct * 100).toFixed(2)}%`
                        : "—"}
                    </span>
                  </li>
                );
              })}
            {members.length === 0 ? <Empty>No positions yet.</Empty> : null}
          </ul>
        </div>
      )}
    </Card>
  );
}

// ── strategy track-record card ───────────────────────────────────────

function StrategyCard() {
  const q = useStrategyLeaderboard();
  const rows = [...(q.data ?? [])]
    .sort((a, b) => (b.paper_return ?? 0) - (a.paper_return ?? 0))
    .slice(0, 5);

  return (
    <Card title="Strategy track records" icon={Trophy} to="/library">
      {q.isLoading ? (
        <Empty>Loading…</Empty>
      ) : rows.length === 0 ? (
        <Empty>No strategies published yet.</Empty>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => {
            const up = (s.paper_return ?? 0) >= 0;
            return (
              <li key={s.name} className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-fg-base">{s.title}</span>
                <span className="flex shrink-0 items-center gap-3 font-mono text-xs tabular-nums">
                  <span className={cn(up ? "text-up" : "text-down")}>
                    {s.paper_return != null
                      ? `${up ? "+" : ""}${(s.paper_return * 100).toFixed(1)}%`
                      : "—"}
                  </span>
                  <span className="text-fg-subtle">
                    {s.paper_win_rate != null
                      ? `${(s.paper_win_rate * 100).toFixed(0)}% win`
                      : ""}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// ── news card ────────────────────────────────────────────────────────

function NewsCard() {
  const q = useNewsDigest();
  const items = q.data?.items ?? [];
  return (
    <Card title="Latest news" icon={Newspaper} to="/news">
      {q.isLoading ? (
        <Empty>Loading…</Empty>
      ) : items.length === 0 ? (
        <Empty>No recent news.</Empty>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {items.slice(0, 6).map((n, i) => (
            <li key={`${n.symbol}-${i}`} className="flex items-baseline gap-3 py-2 text-sm">
              {n.symbol ? (
                <Link
                  to={`/charts/${encodeURIComponent(n.symbol)}`}
                  className="w-14 shrink-0 font-mono text-xs font-medium text-accent hover:underline"
                >
                  {n.symbol}
                </Link>
              ) : (
                <span className="w-14 shrink-0 text-xs text-fg-subtle">—</span>
              )}
              <span className="min-w-0 flex-1 truncate text-fg-muted">{n.title}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
