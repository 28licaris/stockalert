import { useHealthServices, type HealthState } from "@/api/queries";
import { cn } from "@/lib/utils";

/**
 * Persistent bottom strip. Reads the same `useHealthServices` query
 * the Status page uses — TanStack Query dedups, so both renders share
 * one HTTP round-trip every 10s.
 */

const STATE_DOT: Record<HealthState, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  error: "bg-danger",
  unknown: "bg-fg-subtle/40",
};

export function StatusBar() {
  const { data, error } = useHealthServices();

  // While loading we show grayed-out placeholders for the expected
  // headline services so layout doesn't pop.
  const services = data?.services ?? [
    { name: "ClickHouse", state: "unknown" as HealthState, detail: "Loading…", latency_ms: null },
    { name: "Iceberg", state: "unknown" as HealthState, detail: "Loading…", latency_ms: null },
    { name: "Schwab", state: "unknown" as HealthState, detail: "Loading…", latency_ms: null },
    { name: "Polygon", state: "unknown" as HealthState, detail: "Loading…", latency_ms: null },
  ];

  return (
    <footer
      className="flex h-7 shrink-0 items-center gap-4 border-t border-border bg-bg-subtle px-3 font-mono text-[11px] text-fg-subtle"
      aria-label="Platform status"
    >
      {services.map((s) => (
        <span
          key={s.name}
          className="flex items-center gap-1.5"
          title={s.detail || s.name}
        >
          <span
            aria-hidden
            className={cn("h-2 w-2 rounded-full", STATE_DOT[s.state])}
          />
          <span>{s.name}</span>
        </span>
      ))}
      {error ? (
        <span className="text-danger">probe error</span>
      ) : null}
      <span className="ml-auto">FE-1.5 · dev</span>
    </footer>
  );
}
