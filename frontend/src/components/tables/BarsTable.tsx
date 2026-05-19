import type { Bar } from "@/api/queries";
import { fmtPrice, fmtTime, fmtVol } from "@/lib/fmt";
import { cn } from "@/lib/utils";

interface BarsTableProps {
  bars: ReadonlyArray<Bar>;
  limit?: number;
}

/**
 * Most-recent bars at the bottom of the Symbol page. Reversed so the
 * latest row is on top. Color encodes up/down close vs open.
 *
 * FE-3 will replace this hand-rolled table with TanStack Table once
 * sorting/virtualization is needed; for now N≤100 stays fast.
 */
export function BarsTable({ bars, limit = 50 }: BarsTableProps) {
  const rows = [...bars].slice(-limit).reverse();
  return (
    <div className="overflow-x-auto rounded-md border border-border bg-bg-subtle">
      <table className="w-full text-sm">
        <thead className="bg-bg-muted text-xs uppercase tracking-wider text-fg-subtle">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Time</th>
            <th className="px-3 py-2 text-right font-medium">Open</th>
            <th className="px-3 py-2 text-right font-medium">High</th>
            <th className="px-3 py-2 text-right font-medium">Low</th>
            <th className="px-3 py-2 text-right font-medium">Close</th>
            <th className="px-3 py-2 text-right font-medium">Volume</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle font-mono text-xs">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={6}
                className="px-3 py-6 text-center text-fg-subtle"
              >
                No bars yet.
              </td>
            </tr>
          ) : null}
          {rows.map((b) => {
            const up = b.close >= b.open;
            return (
              <tr key={b.ts} className="hover:bg-bg-muted/40">
                <td className="px-3 py-1.5 text-fg-muted">{fmtTime(b.ts)}</td>
                <td className="px-3 py-1.5 text-right text-fg-base">
                  {fmtPrice(b.open)}
                </td>
                <td className="px-3 py-1.5 text-right text-fg-base">
                  {fmtPrice(b.high)}
                </td>
                <td className="px-3 py-1.5 text-right text-fg-base">
                  {fmtPrice(b.low)}
                </td>
                <td
                  className={cn(
                    "px-3 py-1.5 text-right font-semibold",
                    up ? "text-up" : "text-down",
                  )}
                >
                  {fmtPrice(b.close)}
                </td>
                <td className="px-3 py-1.5 text-right text-fg-muted">
                  {fmtVol(b.volume)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
