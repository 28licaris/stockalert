import { MyWatchlists } from "@/components/watchlists/MyWatchlists";

/**
 * Watchlists — the customer's per-user named lists.
 *
 * Every symbol added is stamped as a pretend position (default 100 shares) at
 * the add-time price, so each list doubles as a "what if I'd bought when I
 * first watched it?" scoreboard. Lists are scoped to the signed-in user.
 *
 * (The legacy stream-universe watchlist — which controlled provider
 * subscriptions — was operator plumbing and now lives only on the admin
 * Stream page. Customers never see it.)
 */
export function WatchlistsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <header className="surface-panel rounded-lg p-5">
        <p className="text-xs font-semibold uppercase tracking-wider text-accent">
          market workspace
        </p>
        <h1 className="mt-2 font-display text-2xl font-semibold text-fg-base">
          Watchlists
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-fg-muted">
          Track the tickers you care about. Every symbol you add is stamped as a
          pretend position at that moment&rsquo;s price, so each list doubles as a
          &ldquo;what if I&rsquo;d bought when I first watched it?&rdquo; scoreboard.
        </p>
      </header>

      <MyWatchlists />
    </div>
  );
}
