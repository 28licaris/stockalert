import { Play, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import {
  useHealthServices,
  useJobs,
  useRunJob,
  type HealthState,
  type JobMetadata,
  type JobStatus,
  type ServiceHealth,
  type StreamSummary,
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
          rows={streamingRows(query.data?.stream)}
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

      <ScheduledJobsSection />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Scheduled jobs — registry of background loops with a play button.
// ─────────────────────────────────────────────────────────────────────

function ScheduledJobsSection() {
  const jobs = useJobs();
  const run = useRunJob();

  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-fg-subtle">
            Scheduled jobs
          </h2>
          <p className="mt-1 text-xs text-fg-muted">
            Background loops that keep the data layer fresh. Click ▶ to
            trigger a manual run; the registry refuses to start a job
            that's already in flight.
          </p>
        </div>
        {jobs.isFetching ? (
          <span className="text-[10px] uppercase tracking-wider text-fg-subtle">
            Refreshing…
          </span>
        ) : null}
      </div>

      {jobs.error ? <ApiErrorAlert error={jobs.error} /> : null}
      {run.error ? <ApiErrorAlert error={run.error} /> : null}

      <div className="overflow-hidden rounded-lg border border-border bg-bg-subtle">
        <table className="w-full text-sm">
          <thead className="bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Job</th>
              <th className="px-4 py-2 text-left font-medium">Schedule</th>
              <th className="px-4 py-2 text-left font-medium">Last success</th>
              <th className="px-4 py-2 text-left font-medium">Status</th>
              <th className="px-4 py-2 text-right font-medium" aria-label="Actions" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {jobs.isLoading ? (
              <tr>
                <td colSpan={5} className="px-4 py-4 text-center text-xs text-fg-subtle">
                  Loading…
                </td>
              </tr>
            ) : (jobs.data?.jobs ?? []).length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-4 text-center text-xs text-fg-subtle">
                  No jobs registered. (Background loops may not be enabled — check your .env.)
                </td>
              </tr>
            ) : (
              (jobs.data?.jobs ?? []).map((job) => (
                <JobRow
                  key={job.name}
                  job={job}
                  onRun={() => run.mutate(job.name)}
                  triggering={run.isPending && run.variables === job.name}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const JOB_STATUS_BG: Record<JobStatus, string> = {
  ok: "bg-success",
  running: "bg-accent animate-pulse",
  error: "bg-danger",
  idle: "bg-fg-subtle/40",
  unknown: "bg-warning",
};

const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  ok: "Ready",
  running: "Running…",
  error: "Last run failed",
  idle: "Not yet run",
  unknown: "Unknown",
};

function JobRow({
  job,
  onRun,
  triggering,
}: {
  job: JobMetadata;
  onRun: () => void;
  triggering: boolean;
}) {
  const disabled = !job.runnable || job.running || triggering;
  return (
    <tr className="hover:bg-bg-muted/40">
      <td className="px-4 py-2">
        <div className="font-medium text-fg-base">{job.display_name}</div>
        <div className="font-mono text-[11px] text-fg-subtle">{job.name}</div>
      </td>
      <td
        className="px-4 py-2 text-xs text-fg-muted"
        title={job.setting_key ? `env: ${job.setting_key}` : undefined}
      >
        {job.schedule}
      </td>
      <td className="px-4 py-2 text-xs text-fg-muted">
        {job.last_success ? fmtAgo(job.last_success) : "—"}
      </td>
      <td className="px-4 py-2">
        <span className="inline-flex items-center gap-2 text-xs text-fg-base">
          <span
            aria-hidden
            className={cn("h-2 w-2 rounded-full", JOB_STATUS_BG[job.last_status])}
          />
          {JOB_STATUS_LABEL[job.last_status]}
        </span>
        {job.last_status === "error" && job.last_error ? (
          <div className="mt-1 line-clamp-2 font-mono text-[11px] text-fg-subtle" title={job.last_error}>
            {job.last_error}
          </div>
        ) : null}
      </td>
      <td className="px-4 py-2 text-right">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          onClick={onRun}
          disabled={disabled}
          aria-label={`Run ${job.display_name} now`}
          title={
            !job.runnable
              ? "No manual trigger registered for this job"
              : job.running
                ? "Job is already running"
                : `Run ${job.display_name} now`
          }
        >
          <Play className={cn("h-4 w-4", triggering && "animate-pulse")} />
        </Button>
      </td>
    </tr>
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

/**
 * Streaming tile rows.
 *
 * `streaming_count` (live Schwab subscriptions) and `universe_count`
 * (active rows in `stream_universe`) should match in normal operation
 * — the StreamService writes the CH row and subscribes Schwab in the
 * same call. They only diverge when a subscribe is rejected (invalid
 * symbol, REACHED_SYMBOL_LIMIT, token error). When they match we show
 * a single "Streaming" number to keep the tile uncluttered; when they
 * diverge we surface the drift as a warning row so the operator can
 * act.
 */
function streamingRows(
  s: StreamSummary | undefined,
): ReadonlyArray<readonly [string, string]> {
  const streaming = s?.streaming_count;
  const universe = s?.universe_count;
  const stateLabel = s?.provider_error
    ? "Error"
    : s?.provider_ready
      ? "Ready"
      : "Starting";

  const rows: Array<readonly [string, string]> = [
    ["Streaming", fmtInt(streaming)],
    ["Provider", s?.provider ?? "—"],
    ["State", stateLabel],
  ];

  if (
    streaming != null &&
    universe != null &&
    streaming !== universe
  ) {
    rows.splice(1, 0, [
      "Not subscribed",
      fmtInt(universe - streaming),
    ]);
  }

  return rows;
}

function loadingSkeleton(n: number): ServiceHealth[] {
  return Array.from({ length: n }, (_, i) => ({
    name: ["ClickHouse", "Iceberg", "Schwab", "Polygon"][i] ?? "—",
    state: "unknown" as HealthState,
    detail: "Loading…",
    latency_ms: null,
  }));
}
