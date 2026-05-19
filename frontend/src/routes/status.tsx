import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import {
  useHealthServices,
  type HealthState,
  type ServiceHealth,
} from "@/api/queries";
import { fmtAgo, fmtInt, fmtLatency } from "@/lib/fmt";
import { cn } from "@/lib/utils";

/**
 * Live system-wide health. Polls /api/health/services every 10s
 * (configured in useHealthServices). FE-1.10 will replace polling
 * with WebSocket pushes on the same query key.
 */
export function StatusPage() {
  const query = useHealthServices();

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-fg-base">Status</h1>
          <p className="mt-1 text-sm text-fg-muted">
            System-wide health, refreshed every 10 seconds.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-fg-subtle">
          <span>
            {query.dataUpdatedAt
              ? `Updated ${fmtAgo(new Date(query.dataUpdatedAt).toISOString())}`
              : "Loading…"}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => query.refetch()}
            disabled={query.isFetching}
            aria-label="Refresh now"
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                query.isFetching && "animate-spin",
              )}
            />
            Refresh
          </Button>
        </div>
      </header>

      {query.error ? <ApiErrorAlert error={query.error} /> : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(query.data?.services ?? loadingSkeleton(4)).map((svc) => (
          <ServiceCard key={svc.name} svc={svc} />
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <SummaryCard
          title="Streaming"
          rows={[
            ["Active tickers", fmtInt(query.data?.stream?.streaming_count)],
            ["Universe size", fmtInt(query.data?.stream?.universe_count)],
            ["Provider", query.data?.stream?.provider ?? "—"],
            [
              "State",
              query.data?.stream?.provider_error
                ? "Error"
                : query.data?.stream?.provider_ready
                  ? "Ready"
                  : "Starting",
            ],
          ]}
        />
        <SummaryCard
          title="Backfill queue"
          rows={[
            ["Queued", fmtInt(query.data?.backfill.queued)],
            ["In flight", fmtInt(query.data?.backfill.in_flight)],
            ["Completed (recent)", fmtInt(query.data?.backfill.completed_recent)],
          ]}
        />
        <SummaryCard
          title="Monitors"
          rows={[
            ["Started", fmtInt(query.data?.monitors.started)],
            ["Errors", fmtInt(query.data?.monitors.errors)],
          ]}
        />
      </section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────

const STATE_BG: Record<HealthState, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  error: "bg-danger",
  unknown: "bg-fg-subtle/40",
};

const STATE_LABEL: Record<HealthState, string> = {
  ok: "Healthy",
  warn: "Warning",
  error: "Error",
  unknown: "Unknown",
};

function ServiceCard({ svc }: { svc: ServiceHealth }) {
  return (
    <div className="rounded-lg border border-border bg-bg-subtle p-4">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn("h-2.5 w-2.5 rounded-full", STATE_BG[svc.state])}
        />
        <span className="text-sm font-semibold text-fg-base">{svc.name}</span>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-fg-subtle">
          {STATE_LABEL[svc.state]}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 text-xs text-fg-muted" title={svc.detail}>
        {svc.detail || "—"}
      </div>
      <div className="mt-2 font-mono text-[11px] text-fg-subtle">
        {fmtLatency(svc.latency_ms)}
      </div>
    </div>
  );
}

function SummaryCard({
  title,
  rows,
}: {
  title: string;
  rows: ReadonlyArray<readonly [string, string]>;
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-subtle p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-subtle">
        {title}
      </h2>
      <dl className="mt-3 space-y-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between">
            <dt className="text-fg-muted">{k}</dt>
            <dd className="font-mono text-fg-base">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function loadingSkeleton(n: number): ServiceHealth[] {
  return Array.from({ length: n }, (_, i) => ({
    name: ["ClickHouse", "Iceberg", "Schwab", "Polygon"][i] ?? "—",
    state: "unknown" as HealthState,
    detail: "Loading…",
    latency_ms: null,
  }));
}
