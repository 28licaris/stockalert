import { useHealthFreshness, type FreshnessState } from "@/api/queries";
import { cn } from "@/lib/utils";

/**
 * "Is data still arriving?" — the question `/health/services` does NOT
 * answer. Every incident on 2026-07/08 had green connectivity while a
 * feed was silently dead for days, so this panel exists to make
 * staleness impossible to miss at a glance.
 *
 * Rows arrive worst-first from the API. `idle` is deliberately NOT an
 * alarm colour: an intraday feed being stale overnight is correct, and
 * a panel that cries wolf nightly gets ignored.
 */

const STATE_META: Record<
  FreshnessState,
  { dot: string; text: string; label: string }
> = {
  error: { dot: "bg-rose-500", text: "text-rose-300", label: "STALE" },
  warn: { dot: "bg-amber-400", text: "text-amber-300", label: "LATE" },
  unknown: { dot: "bg-zinc-500", text: "text-zinc-400", label: "UNKNOWN" },
  idle: { dot: "bg-sky-500/60", text: "text-sky-300/80", label: "IDLE" },
  ok: { dot: "bg-emerald-500", text: "text-emerald-300", label: "FRESH" },
};

function ago(seconds: number | null): string {
  if (seconds == null) return "—";
  const s = Math.max(seconds, 0);
  if (s < 90) return `${s.toFixed(0)}s ago`;
  if (s < 5400) return `${(s / 60).toFixed(0)}m ago`;
  if (s < 172800) return `${(s / 3600).toFixed(0)}h ago`;
  return `${(s / 86400).toFixed(0)}d ago`;
}

function cadence(seconds: number): string {
  if (seconds < 90) return `every ${seconds.toFixed(0)}s`;
  if (seconds < 5400) return `every ${(seconds / 60).toFixed(0)}m`;
  if (seconds < 172800) return `every ${(seconds / 3600).toFixed(0)}h`;
  return `every ${(seconds / 86400).toFixed(0)}d`;
}

export function DataFreshnessPanel() {
  const { data, isLoading, error } = useHealthFreshness();
  const rows = data?.rows ?? [];
  const problems = rows.filter(
    (r) => r.state === "error" || r.state === "warn",
  ).length;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-sm font-semibold uppercase tracking-wider text-fg-subtle">
          Data freshness
        </h2>
        <span className="text-xs text-muted-foreground">
          {isLoading
            ? "checking…"
            : problems > 0
              ? `${problems} source${problems > 1 ? "s" : ""} need attention`
              : "all sources current"}
        </span>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
          Freshness check unavailable: {String((error as Error).message ?? error)}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="grid grid-cols-[110px_1fr_140px_120px] items-center gap-3 border-b border-border px-4 py-2 text-[10px] uppercase tracking-widest text-muted-foreground">
          <span>State</span>
          <span>Source</span>
          <span className="text-right">Last data</span>
          <span className="text-right">Expected</span>
        </div>
        <div className="divide-y divide-border/40">
          {rows.length === 0 && !isLoading && (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No sources configured.
            </div>
          )}
          {rows.map((r) => {
            const meta = STATE_META[r.state] ?? STATE_META.unknown;
            return (
              <div
                key={r.key}
                className={cn(
                  "grid grid-cols-[110px_1fr_140px_120px] items-center gap-3 px-4 py-2.5",
                  r.state === "error" && "bg-rose-500/5",
                  r.state === "warn" && "bg-amber-500/5",
                )}
                title={r.detail}
              >
                <span className="flex items-center gap-2">
                  <span className={cn("h-2 w-2 rounded-full", meta.dot)} />
                  <span className={cn("text-[10px] font-medium tracking-wider", meta.text)}>
                    {meta.label}
                  </span>
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm">{r.label}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {r.detail}
                  </span>
                </span>
                <span
                  className={cn(
                    "text-right font-mono text-xs",
                    r.state === "error" ? meta.text : "text-muted-foreground",
                  )}
                >
                  {ago(r.age_seconds)}
                </span>
                <span className="text-right font-mono text-[11px] text-muted-foreground/70">
                  {cadence(r.cadence_seconds)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
