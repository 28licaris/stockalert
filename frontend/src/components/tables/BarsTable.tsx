import { useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Bar } from "@/api/queries";
import { useUserSetting } from "@/lib/storage";
import {
  fmtPrice,
  fmtTimeET,
  fmtVol,
  isRegularSessionET,
} from "@/lib/fmt";
import { cn } from "@/lib/utils";

interface BarsTableProps {
  bars: ReadonlyArray<Bar>;
  /** Visible rows after filter/limit are applied. Default 50. */
  limit?: number;
}

/**
 * Most-recent bars beneath the chart. Collapsible by default so the
 * chart dominates the viewport; expand for debug / detailed review.
 *
 * Two persistent settings:
 *   `chart.barsTable.open`        — expanded/collapsed
 *   `chart.barsTable.regularOnly` — filter to 09:30–16:00 ET only
 *
 * Why the filter matters: the underlying CH data is HEALTHY through
 * regular hours, but the cockpit fetches 500 raw bars which, near
 * market-close-plus-a-few-hours, include after-hours rows where
 * trades are genuinely sparse (10-30 min between bars is normal).
 * Filtering to regular session makes the table look like proper
 * trading-day data instead of "data gaps."
 */
export function BarsTable({ bars, limit = 50 }: BarsTableProps) {
  const [open, setOpen] = useUserSetting<boolean>(
    "chart.barsTable.open",
    false,
  );
  const [regularOnly, setRegularOnly] = useUserSetting<boolean>(
    "chart.barsTable.regularOnly",
    true,
  );

  const filtered = useMemo(() => {
    const stream = regularOnly
      ? bars.filter((b) => isRegularSessionET(b.ts))
      : bars;
    return [...stream].slice(-limit).reverse();
  }, [bars, regularOnly, limit]);

  return (
    <section className="rounded-md border border-border bg-bg-subtle">
      <header className="flex items-center gap-3 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((s) => !s)}
          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-fg-subtle hover:text-fg-base"
          aria-expanded={open}
          aria-controls="bars-table-body"
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          Recent bars
        </button>
        <span className="text-[10px] text-fg-subtle">
          {filtered.length} of {bars.length}
          {regularOnly ? " (regular session)" : " (all sessions)"}
        </span>
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[11px] text-fg-muted">
          <input
            type="checkbox"
            checked={regularOnly}
            onChange={(e) => setRegularOnly(e.target.checked)}
            className="h-3 w-3 accent-accent"
          />
          Regular session only (09:30–16:00 ET)
        </label>
      </header>

      {open ? (
        <div id="bars-table-body" className="overflow-x-auto border-t border-border">
          <table className="w-full text-xs">
            <thead className="bg-bg-muted uppercase tracking-wider text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 text-left font-medium">Time (ET)</th>
                <th className="px-3 py-1.5 text-right font-medium">Open</th>
                <th className="px-3 py-1.5 text-right font-medium">High</th>
                <th className="px-3 py-1.5 text-right font-medium">Low</th>
                <th className="px-3 py-1.5 text-right font-medium">Close</th>
                <th className="px-3 py-1.5 text-right font-medium">Volume</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle font-mono">
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-3 py-6 text-center text-fg-subtle"
                  >
                    No bars match the current filter.
                  </td>
                </tr>
              ) : null}
              {filtered.map((b) => {
                const up = b.close >= b.open;
                return (
                  <tr key={b.ts} className="hover:bg-bg-muted/40">
                    <td className="px-3 py-1 text-fg-muted">{fmtTimeET(b.ts)}</td>
                    <td className="px-3 py-1 text-right text-fg-base">
                      {fmtPrice(b.open)}
                    </td>
                    <td className="px-3 py-1 text-right text-fg-base">
                      {fmtPrice(b.high)}
                    </td>
                    <td className="px-3 py-1 text-right text-fg-base">
                      {fmtPrice(b.low)}
                    </td>
                    <td
                      className={cn(
                        "px-3 py-1 text-right font-semibold",
                        up ? "text-up" : "text-down",
                      )}
                    >
                      {fmtPrice(b.close)}
                    </td>
                    <td className="px-3 py-1 text-right text-fg-muted">
                      {fmtVol(b.volume)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
