import { useMemo, useState } from "react";
import { ExternalLink, Lightbulb } from "lucide-react";
import { useNews, useNewsDigest, type NewsItem } from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { cn } from "@/lib/utils";

const TYPE_FILTERS: { label: string; value: string | undefined }[] = [
  { label: "All types", value: undefined },
  { label: "8-K", value: "8-K" },
  { label: "Insider (Form 4)", value: "4" },
  { label: "Earnings (10-Q)", value: "10-Q" },
];

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function materialityClass(m: string): string {
  if (m === "high") return "bg-danger/15 text-danger";
  if (m === "medium") return "bg-warning/15 text-warning";
  if (m === "low") return "bg-fg-muted/15 text-fg-muted";
  return "bg-fg-muted/10 text-fg-muted"; // unrated
}

function NewsCard({ item }: { item: NewsItem }) {
  const headline = item.summary || item.title;
  return (
    <div className="rounded-xl border border-border bg-bg p-3.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        {item.symbol ? (
          <span className="text-sm font-semibold">{item.symbol}</span>
        ) : (
          <span className="text-sm font-semibold text-fg-muted">Market</span>
        )}
        <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] text-accent">
          {item.event_type}
        </span>
        {item.materiality !== "unrated" ? (
          <span className={cn("rounded-full px-2 py-0.5 text-[11px]", materialityClass(item.materiality))}>
            {item.materiality}
          </span>
        ) : null}
        <span className="ml-auto text-xs text-fg-muted">{fmtWhen(item.published_at)}</span>
      </div>

      <p className="text-sm leading-snug">{headline}</p>

      {item.why_it_matters ? (
        <p className="mt-1 flex items-start gap-1 text-[13px] leading-snug text-fg-muted">
          <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>Why it matters: {item.why_it_matters}</span>
        </p>
      ) : null}

      {item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex items-center gap-1 text-[13px] text-accent hover:underline"
        >
          View source <ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : null}
    </div>
  );
}

/**
 * News & alerts feed — official-record items (SEC EDGAR filings; govt releases
 * later), AI-summarized with a link to the source. From /api/v1/news.
 * Unenriched items fall back to the filing title until the summary lands.
 * Per-user watchlist scoping + push/digest alerts arrive in a later phase
 * (docs/news_alerts_spec.md).
 */
type Mode = "all" | "digest";

export function NewsPage() {
  const [mode, setMode] = useState<Mode>("all");
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);

  const feed = useNews({ types: typeFilter, limit: 100 });
  const digest = useNewsDigest();

  const active = mode === "digest" ? digest : feed;
  const items = useMemo<NewsItem[]>(
    () => (mode === "digest" ? digest.data?.items ?? [] : feed.data ?? []),
    [mode, digest.data, feed.data],
  );

  const emptyMsg =
    mode === "digest"
      ? "Nothing material today. The digest shows the day's high-importance items."
      : "No news yet. Filings appear here once the ingest job runs.";

  return (
    <div className="mx-auto max-w-3xl p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold">News &amp; alerts</h1>
          <span className="text-xs text-fg-muted">official filings, AI-summarized</span>
        </div>
        <div role="tablist" aria-label="View" className="flex rounded-md border border-border">
          {(["all", "digest"] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={cn(
                "px-3 py-1 text-sm transition-colors first:rounded-l-md last:rounded-r-md",
                mode === m
                  ? "bg-accent text-accent-fg"
                  : "text-fg-muted hover:bg-bg-subtle",
              )}
            >
              {m === "all" ? "All news" : "Today's digest"}
            </button>
          ))}
        </div>
      </div>

      {mode === "all" ? (
        <div className="mb-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Event type">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f.label}
              role="tab"
              aria-selected={typeFilter === f.value}
              onClick={() => setTypeFilter(f.value)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition-colors",
                typeFilter === f.value
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-border text-fg-muted hover:bg-bg-subtle",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : (
        <div className="mb-4 text-sm text-fg-muted">
          {digest.data
            ? `${digest.data.count} material ${digest.data.count === 1 ? "item" : "items"} today (${digest.data.date})`
            : "Today's material items"}
        </div>
      )}

      {active.error ? <ApiErrorAlert error={active.error} /> : null}

      {!active.error && !active.isLoading && items.length === 0 ? (
        <div className="rounded-xl border border-border bg-bg-subtle p-6 text-center text-sm text-fg-muted">
          {emptyMsg}
        </div>
      ) : null}

      <div className={cn("flex flex-col gap-2.5", active.isLoading && "opacity-60")}>
        {items.map((item) => (
          <NewsCard key={`${item.source}:${item.id}`} item={item} />
        ))}
      </div>
    </div>
  );
}
