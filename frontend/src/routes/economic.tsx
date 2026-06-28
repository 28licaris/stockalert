import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import {
  useEconomic,
  useEconomicHistory,
  type EconHistoryPoint,
  type EconIndicator,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { cn } from "@/lib/utils";

function Sparkline({ points }: { points: EconHistoryPoint[] }) {
  // points come newest-first; chart oldest→newest.
  const vals = useMemo(() => points.map((p) => p.value).reverse(), [points]);
  if (vals.length < 2) return null;
  const w = 280;
  const h = 48;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const d = vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-12 w-full" preserveAspectRatio="none">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-accent" />
    </svg>
  );
}

function ChangeBadge({ change }: { change: number | null | undefined }) {
  if (change === null || change === undefined) return null;
  const up = change >= 0;
  return (
    <span className="inline-flex items-center gap-0.5 text-xs text-fg-muted">
      {up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
      {Math.abs(change).toFixed(1)}
    </span>
  );
}

/**
 * Economic indicators — latest free government data (BLS: CPI, jobs,
 * unemployment) with per-series history. From /api/v1/economic. The data hub
 * the AI and the trader share; new releases also hit the News feed/digest.
 */
export function EconomicPage() {
  const { data, isLoading, error } = useEconomic();
  const indicators = useMemo<EconIndicator[]>(() => data ?? [], [data]);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const history = useEconomicHistory(selected);

  const selectedMeta = indicators.find((i) => i.series_id === selected);

  return (
    <div className="mx-auto max-w-3xl p-4">
      <div className="mb-3 flex items-center gap-2">
        <h1 className="text-lg font-semibold">Economic indicators</h1>
        <span className="text-xs text-fg-muted">free government data (BLS)</span>
      </div>

      {error ? <ApiErrorAlert error={error} /> : null}

      {!error && !isLoading && indicators.length === 0 ? (
        <div className="rounded-xl border border-border bg-bg-subtle p-6 text-center text-sm text-fg-muted">
          No data yet. Indicators populate once the ingest job runs.
        </div>
      ) : null}

      <div className={cn("grid grid-cols-1 gap-2.5 sm:grid-cols-3", isLoading && "opacity-60")}>
        {indicators.map((ind) => {
          const active = ind.series_id === selected;
          return (
            <button
              key={ind.series_id}
              onClick={() => setSelected(active ? undefined : ind.series_id)}
              className={cn(
                "rounded-xl border p-3 text-left transition-colors",
                active ? "border-accent bg-accent/10" : "border-border bg-bg hover:bg-bg-subtle",
              )}
            >
              <div className="text-xs text-fg-muted">{ind.name}</div>
              <div className="mt-1 text-xl font-semibold">{ind.value_label}</div>
              <div className="mt-0.5 flex items-center justify-between">
                <span className="text-[11px] text-fg-muted">{ind.period_label}</span>
                <ChangeBadge change={ind.change} />
              </div>
            </button>
          );
        })}
      </div>

      {selected && selectedMeta ? (
        <div className="mt-4 rounded-xl border border-border bg-bg p-4">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="text-sm font-semibold">{selectedMeta.name} — history</h2>
            <span className="text-xs text-fg-muted">{selectedMeta.unit}</span>
          </div>

          {history.error ? <ApiErrorAlert error={history.error} /> : null}
          {history.data && history.data.length ? <Sparkline points={history.data} /> : null}

          <div className={cn("mt-2 max-h-72 overflow-auto", history.isLoading && "opacity-60")}>
            <table className="w-full text-sm">
              <tbody>
                {(history.data ?? []).map((p) => (
                  <tr key={p.period} className="border-b border-border/50">
                    <td className="py-1 text-fg-muted">{p.period_label}</td>
                    <td className="py-1 text-right tabular-nums">{p.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
