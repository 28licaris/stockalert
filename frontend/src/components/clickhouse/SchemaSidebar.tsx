import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Database, Search } from "lucide-react";
import {
  useClickHouseSchema,
  type CHColumn,
  type CHTable,
} from "@/api/queries";
import { fmtInt } from "@/lib/fmt";
import { cn } from "@/lib/utils";

interface SchemaSidebarProps {
  /** Called when the operator clicks a table name. */
  onTableClick: (table: CHTable) => void;
  /** Called when the operator clicks a column name. */
  onColumnClick: (table: CHTable, column: CHColumn) => void;
}

/**
 * Left rail of the CH query page. Groups tables by database, supports
 * filter-by-name, and exposes click-to-insert for both tables (insert
 * `SELECT * FROM db.table LIMIT 100`) and columns (insert the column
 * name at the caret).
 *
 * Persisted state lives only in component state — refreshing the page
 * collapses everything, which is fine; the schema reload is cheap.
 */
export function SchemaSidebar({
  onTableClick,
  onColumnClick,
}: SchemaSidebarProps) {
  const query = useClickHouseSchema();
  const [filter, setFilter] = useState("");
  const [expandedDbs, setExpandedDbs] = useState<Set<string>>(new Set());
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());

  // Group tables by database, then filter by case-insensitive substring
  // against either DB or table name.
  const grouped = useMemo(() => {
    const tables = query.data?.tables ?? [];
    const needle = filter.trim().toLowerCase();

    const byDb = new Map<string, CHTable[]>();
    for (const t of tables) {
      if (
        needle &&
        !t.name.toLowerCase().includes(needle) &&
        !t.database.toLowerCase().includes(needle)
      ) {
        continue;
      }
      const arr = byDb.get(t.database) ?? [];
      arr.push(t);
      byDb.set(t.database, arr);
    }
    return Array.from(byDb.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [query.data, filter]);

  const toggle = (set: Set<string>, key: string): Set<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  return (
    <aside
      aria-label="ClickHouse schema browser"
      className="flex h-full min-h-0 w-64 shrink-0 flex-col border-r border-border bg-bg-subtle"
    >
      <div className="flex h-9 items-center gap-2 border-b border-border px-3">
        <Database className="h-4 w-4 text-fg-subtle" aria-hidden />
        <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
          Schema
        </span>
        <span className="ml-auto text-[10px] text-fg-subtle">
          {query.isFetching ? "…" : fmtInt(query.data?.tables.length)}
        </span>
      </div>

      <div className="flex h-9 items-center gap-2 border-b border-border bg-bg-base px-3">
        <Search className="h-3.5 w-3.5 text-fg-subtle" aria-hidden />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter"
          className="h-full flex-1 bg-transparent text-xs text-fg-base focus:outline-none"
          spellCheck={false}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto text-[12px]">
        {query.isLoading ? (
          <ul className="space-y-1 p-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <li
                key={i}
                className="h-5 animate-pulse rounded bg-bg-muted"
              />
            ))}
          </ul>
        ) : grouped.length === 0 ? (
          <div className="px-3 py-4 text-xs text-fg-subtle">
            No tables match.
          </div>
        ) : (
          grouped.map(([db, tables]) => {
            const dbOpen = expandedDbs.has(db);
            return (
              <div key={db}>
                <button
                  type="button"
                  onClick={() => setExpandedDbs((s) => toggle(s, db))}
                  className="flex w-full items-center gap-1 px-2 py-1 text-left text-fg-muted hover:bg-bg-muted"
                >
                  {dbOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                  <span className="truncate">{db}</span>
                  <span className="ml-auto text-[10px] text-fg-subtle">
                    {tables.length}
                  </span>
                </button>
                {dbOpen
                  ? tables.map((t) => {
                      const key = `${db}.${t.name}`;
                      const tableOpen = expandedTables.has(key);
                      return (
                        <div key={key}>
                          <div className="flex items-center pl-4 hover:bg-bg-muted">
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedTables((s) => toggle(s, key))
                              }
                              className="flex h-6 w-4 shrink-0 items-center justify-center text-fg-subtle"
                              aria-label={tableOpen ? "Collapse" : "Expand"}
                            >
                              {tableOpen ? (
                                <ChevronDown className="h-3 w-3" />
                              ) : (
                                <ChevronRight className="h-3 w-3" />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => onTableClick(t)}
                              className="flex flex-1 items-center gap-2 truncate px-1 py-0.5 text-left font-mono text-fg-base"
                              title={`Insert SELECT * FROM ${db}.${t.name} LIMIT 100`}
                            >
                              <span className="truncate">{t.name}</span>
                              {t.row_count != null ? (
                                <span className="ml-auto text-[10px] text-fg-subtle">
                                  {fmtInt(t.row_count)}
                                </span>
                              ) : null}
                            </button>
                          </div>
                          {tableOpen ? (
                            <ul className="pl-9">
                              {t.columns.map((c) => (
                                <li key={c.name}>
                                  <button
                                    type="button"
                                    onClick={() => onColumnClick(t, c)}
                                    className={cn(
                                      "flex w-full items-center justify-between gap-2 px-2 py-0.5 text-left font-mono hover:bg-bg-muted",
                                    )}
                                    title={`Insert ${c.name}`}
                                  >
                                    <span className="truncate text-fg-base">
                                      {c.name}
                                    </span>
                                    <span className="truncate text-[10px] text-fg-subtle">
                                      {c.type}
                                    </span>
                                  </button>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      );
                    })
                  : null}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
