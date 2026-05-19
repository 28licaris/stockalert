import { useRef, useState } from "react";
import { History, Play, ShieldCheck } from "lucide-react";
import {
  useExecuteClickHouseQuery,
  type CHColumn,
  type CHTable,
  type ClickHouseQueryResponse,
} from "@/api/queries";
import { ApiErrorAlert } from "@/components/ApiErrorAlert";
import { Button } from "@/components/ui/button";
import { SchemaSidebar } from "@/components/clickhouse/SchemaSidebar";
import { ResultsTable } from "@/components/clickhouse/ResultsTable";
import { useUserSetting } from "@/lib/storage";
import { cn } from "@/lib/utils";

const STARTER_QUERY =
  "-- Read-only SQL. Try a query, or click a table on the left.\n" +
  "SELECT database, table, total_rows\n" +
  "FROM system.tables\n" +
  "WHERE database NOT IN ('system','INFORMATION_SCHEMA','information_schema')\n" +
  "ORDER BY total_rows DESC NULLS LAST\n" +
  "LIMIT 25;";

const MAX_RECENT = 10;

/**
 * ClickHouse ad-hoc query page (FE-CONTRACTS-6a).
 *
 * Layout: schema sidebar (left, fixed 256px) · editor + results stack (right).
 *
 * Behavior:
 *   - Cmd/Ctrl-Enter runs the query.
 *   - Schema sidebar table-click inserts `SELECT * FROM db.t LIMIT 100`,
 *     replacing the editor contents only if it's empty / matches the
 *     starter; otherwise inserts at the caret.
 *   - Schema sidebar column-click inserts the column name at the caret.
 *   - Recent queries persisted via `useUserSetting('clickhouse.recent', [])`.
 *   - Server enforces row cap + readonly; this page surfaces the typed
 *     ErrorResponse via <ApiErrorAlert>.
 */
export function ClickHousePage() {
  const [sql, setSql] = useState<string>(STARTER_QUERY);
  const [maxRows, setMaxRows] = useUserSetting<number>(
    "clickhouse.maxRows",
    1000,
  );
  const [recent, setRecent] = useUserSetting<string[]>(
    "clickhouse.recent",
    [],
  );
  const [showRecent, setShowRecent] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const exec = useExecuteClickHouseQuery();

  const run = () => {
    const trimmed = sql.trim();
    if (!trimmed) return;
    exec.mutate(
      { sql: trimmed, max_rows: maxRows, timeout_seconds: 30 },
      {
        onSuccess: () => {
          setRecent((prev) =>
            [trimmed, ...prev.filter((q) => q !== trimmed)].slice(0, MAX_RECENT),
          );
        },
      },
    );
  };

  const insertAtCaret = (text: string, replaceStarter: boolean = false) => {
    const el = textareaRef.current;
    if (!el) {
      setSql(text);
      return;
    }
    if (replaceStarter && (sql.trim() === "" || sql === STARTER_QUERY)) {
      setSql(text);
      // Move caret to end on next tick.
      setTimeout(() => {
        el.focus();
        el.setSelectionRange(text.length, text.length);
      }, 0);
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const before = sql.slice(0, start);
    const after = sql.slice(end);
    const next = before + text + after;
    setSql(next);
    setTimeout(() => {
      el.focus();
      const caret = start + text.length;
      el.setSelectionRange(caret, caret);
    }, 0);
  };

  return (
    <div className="flex h-full min-h-0">
      <SchemaSidebar
        onTableClick={(t: CHTable) =>
          insertAtCaret(
            `SELECT * FROM ${t.database}.${t.name} LIMIT 100`,
            true,
          )
        }
        onColumnClick={(_t: CHTable, c: CHColumn) => insertAtCaret(c.name)}
      />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Header */}
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-bg-base px-4">
          <h1 className="text-sm font-semibold text-fg-base">
            ClickHouse Query
          </h1>
          <span
            className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-fg-subtle"
            title="Server applies readonly=1, max_bytes_to_read=1 GiB, max_memory_usage=4 GiB, max_execution_time=30s"
          >
            <ShieldCheck className="h-3 w-3" />
            read-only
          </span>

          <div className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
            <label className="flex items-center gap-1.5">
              <span>Max rows</span>
              <input
                type="number"
                value={maxRows}
                onChange={(e) =>
                  setMaxRows(
                    Math.max(
                      1,
                      Math.min(30_000, Number(e.target.value) || 1000),
                    ),
                  )
                }
                min={1}
                max={30_000}
                className="h-7 w-20 rounded border border-border bg-bg-subtle px-2 text-right font-mono"
              />
            </label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowRecent((s) => !s)}
              title="Recent queries"
            >
              <History className="h-3.5 w-3.5" />
              Recent
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={run}
              disabled={exec.isPending || !sql.trim()}
            >
              <Play className="h-3.5 w-3.5" />
              {exec.isPending ? "Running…" : "Run (⌘↵)"}
            </Button>
          </div>
        </div>

        {/* Recent queries dropdown */}
        {showRecent ? (
          <RecentQueries
            recent={recent}
            onPick={(q) => {
              setSql(q);
              setShowRecent(false);
            }}
            onClear={() => {
              setRecent([]);
              setShowRecent(false);
            }}
          />
        ) : null}

        {/* Editor */}
        <div className="shrink-0 border-b border-border">
          <textarea
            ref={textareaRef}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                run();
              }
            }}
            spellCheck={false}
            className="block h-44 w-full resize-y bg-bg-base p-3 font-mono text-sm text-fg-base focus:outline-none"
            placeholder="Type SQL — Cmd/Ctrl-Enter to run"
          />
        </div>

        {/* Error / loading / result */}
        {exec.error ? (
          <div className="border-b border-border p-3">
            <ApiErrorAlert error={exec.error} />
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col">
          {exec.data ? (
            <ResultsTable result={exec.data as ClickHouseQueryResponse} />
          ) : (
            <EmptyResults />
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyResults() {
  return (
    <div className="grid h-full place-items-center text-sm text-fg-subtle">
      Run a query to see results.
    </div>
  );
}

function RecentQueries({
  recent,
  onPick,
  onClear,
}: {
  recent: ReadonlyArray<string>;
  onPick: (q: string) => void;
  onClear: () => void;
}) {
  if (recent.length === 0) {
    return (
      <div className="border-b border-border bg-bg-subtle px-4 py-2 text-xs text-fg-subtle">
        No recent queries yet.
      </div>
    );
  }
  return (
    <div className="max-h-48 overflow-y-auto border-b border-border bg-bg-subtle">
      <div className="flex items-center justify-between border-b border-border px-3 py-1 text-[10px] uppercase tracking-wider text-fg-subtle">
        <span>Recent queries</span>
        <button
          type="button"
          onClick={onClear}
          className="hover:text-fg-base"
        >
          Clear
        </button>
      </div>
      <ul>
        {recent.map((q, i) => (
          <li key={i}>
            <button
              type="button"
              onClick={() => onPick(q)}
              className={cn(
                "block w-full truncate px-3 py-1.5 text-left font-mono text-xs text-fg-muted hover:bg-bg-muted hover:text-fg-base",
              )}
              title={q}
            >
              {q.replace(/\s+/g, " ").slice(0, 200)}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
