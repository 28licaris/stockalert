import { useMemo, useState } from "react";
import { ExternalLink, Lightbulb } from "lucide-react";
import { useNews, type NewsItem } from "@/api/queries";
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
export function NewsPage() {
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const { data, isLoading, error } = useNews({ types: typeFilter, limit: 100 });

  const items = useMemo(() => data ?? [], [data]);

  return (
    <div className="mx-auto max-w-3xl p-4">
      <div className="mb-3 flex items-center gap-2">
        <h1 className="text-lg font-semibold">News &amp; alerts</h1>
        <span className="text-xs text-fg-muted">official filings, AI-summarized</span>
      </div>

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

      {error ? <ApiErrorAlert error={error} /> : null}

      {!error && !isLoading && items.length === 0 ? (
        <div className="rounded-xl border border-border bg-bg-subtle p-6 text-center text-sm text-fg-muted">
          No news yet. Filings appear here once the ingest job runs.
        </div>
      ) : null}

      <div className={cn("flex flex-col gap-2.5", isLoading && "opacity-60")}>
        {items.map((item) => (
          <NewsCard key={`${item.source}:${item.id}`} item={item} />
        ))}
      </div>
    </div>
  );
}
