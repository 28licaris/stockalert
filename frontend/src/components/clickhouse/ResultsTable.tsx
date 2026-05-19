import type { ClickHouseQueryResponse } from "@/api/queries";
import { fmtInt } from "@/lib/fmt";
import { cn } from "@/lib/utils";

interface ResultsTableProps {
  result: ClickHouseQueryResponse;
}

/**
 * Renders the result of a CH query as a virtualized-friendly table.
 * Right-aligns numeric types, keeps strings left-aligned. Truncation
 * banner appears above the table when the engine clipped the result.
 */
export function ResultsTable({ result }: ResultsTableProps) {
  const { columns, rows, row_count, truncated, duration_ms } = result;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-bg-subtle px-3 py-1.5 text-xs text-fg-muted">
        <span>
          {fmtInt(row_count)} rows · {duration_ms.toFixed(1)} ms
        </span>
        {truncated ? (
          <span className="rounded-full bg-warning/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warning">
            Truncated · narrow your query
          </span>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {rows.length === 0 ? (
          <div className="grid h-full place-items-center text-sm text-fg-subtle">
            No rows returned.
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-bg-muted text-[10px] uppercase tracking-wider text-fg-subtle">
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.name}
                    className="border-b border-border-subtle px-3 py-1.5 text-left font-medium"
                    title={c.type}
                  >
                    <div className="flex flex-col">
                      <span className="text-fg-base normal-case">{c.name}</span>
                      <span className="text-[9px] text-fg-subtle">{c.type}</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="font-mono">
              {rows.map((row, ri) => (
                <tr
                  key={ri}
                  className={cn(
                    "border-b border-border-subtle",
                    ri % 2 === 0 ? "bg-bg-base" : "bg-bg-subtle/40",
                  )}
                >
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className={cn(
                        "whitespace-nowrap px-3 py-1",
                        isNumericType(columns[ci]?.type)
                          ? "text-right"
                          : "text-left",
                        cell === null ? "text-fg-subtle italic" : "text-fg-base",
                      )}
                    >
                      {renderCell(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isNumericType(type: string | undefined): boolean {
  if (!type) return false;
  const lower = type.toLowerCase();
  return (
    lower.startsWith("uint") ||
    lower.startsWith("int") ||
    lower.startsWith("float") ||
    lower.startsWith("decimal") ||
    lower === "double"
  );
}
