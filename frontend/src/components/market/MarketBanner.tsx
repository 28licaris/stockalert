import { Link } from "react-router-dom";
import { useMarketBanner, type BannerItem } from "@/api/queries";
import { fmtPct, fmtPrice } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Always-visible market tape strip — index + futures last price + change.
 *
 * Wired in the AppShell above the Topbar so it appears on every cockpit
 * page. Auto-refreshes every 10 seconds (via `useMarketBanner`).
 * Clicking a chip navigates to the symbol page.
 *
 * UX behavior:
 *   - Hidden on screens narrower than `md` (768px) — the cockpit is
 *     desktop-first; a mobile tape would crowd the topbar instead.
 *   - Loading: skeleton chips so layout doesn't pop.
 *   - Empty / errored: subtle muted message; we don't shout the
 *     error because banner failures shouldn't distract from the page.
 *   - Overflow: horizontal scroll. The cockpit terminal aesthetic
 *     wins over auto-marquee animation.
 */
export function MarketBanner() {
  const query = useMarketBanner();

  return (
    <div
      role="region"
      aria-label="Market tape"
      className="hidden h-8 shrink-0 items-center border-b border-border bg-bg-subtle md:flex"
    >
      <ScrollableTape>
        <Contents query={query} />
      </ScrollableTape>
    </div>
  );
}

function ScrollableTape({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-w-0 flex-1 items-stretch gap-0 overflow-x-auto whitespace-nowrap font-mono text-[11px]">
      {children}
    </div>
  );
}

function Contents({
  query,
}: {
  query: ReturnType<typeof useMarketBanner>;
}) {
  if (query.isLoading) {
    return <SkeletonChips />;
  }
  if (query.error) {
    return (
      <span className="px-3 text-fg-subtle">market data unavailable</span>
    );
  }
  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return <span className="px-3 text-fg-subtle">no banner symbols configured</span>;
  }
  return (
    <>
      {items.map((item) => (
        <BannerChip key={item.symbol} item={item} />
      ))}
    </>
  );
}

function BannerChip({ item }: { item: BannerItem }) {
  const up = (item.change_pct ?? 0) >= 0;
  const colorClass = up ? "text-up" : "text-down";
  // Equity-style symbols route directly; index / future prefixes (the
  // `$SPX`, `/MNQM26` cases) don't have their own cockpit symbol pages
  // yet, so we degrade to a non-link chip rather than 404.
  const routable = !item.symbol.startsWith("$") && !item.symbol.startsWith("/");
  const inner = (
    <span className="flex h-full items-center gap-1.5 border-r border-border-subtle px-3 transition-colors">
      <span className="font-semibold text-fg-base">{item.label}</span>
      <span className="text-fg-muted">{fmtPrice(item.last)}</span>
      <span className={cn("font-semibold", colorClass)}>
        {fmtPct(item.change_pct)}
      </span>
    </span>
  );
  return routable ? (
    <Link
      to={`/symbol/${encodeURIComponent(item.symbol)}`}
      className="hover:bg-bg-muted"
      title={`${item.symbol} — ${item.description || "open chart"}`}
    >
      {inner}
    </Link>
  ) : (
    <span title={item.description || item.symbol}>{inner}</span>
  );
}

function SkeletonChips() {
  return (
    <>
      {Array.from({ length: 4 }, (_, i) => (
        <span
          key={i}
          className="flex h-full items-center gap-2 border-r border-border-subtle px-3"
        >
          <span className="h-2.5 w-10 animate-pulse rounded bg-bg-muted" />
          <span className="h-2.5 w-12 animate-pulse rounded bg-bg-muted" />
          <span className="h-2.5 w-10 animate-pulse rounded bg-bg-muted" />
        </span>
      ))}
    </>
  );
}
