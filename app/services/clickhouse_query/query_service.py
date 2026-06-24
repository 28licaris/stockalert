"""
Ad-hoc ClickHouse query execution + schema introspection.

Safety contract (FE-CONTRACTS-6a):

  1. CH `readonly=1` setting applied at the query level — DDL / DML /
     SETTINGS attempts are rejected by the engine itself, not by
     string parsing. This is the actual security boundary.
  2. Per-query row cap (default 1000, hard ceiling 30 000) enforced
     via `max_result_rows` setting + a defensive trim if the engine
     somehow exceeds it.
  3. Per-query execution timeout (default 30s, hard ceiling 120s)
     enforced via `max_execution_time`.
  4. `system.*` databases hidden from the schema listing so internal
     CH metadata (parts, query log, etc.) isn't exposed to the cockpit.
  5. Empty / hidden schemas reject upstream of the query layer.

Caching:
  - Schema response is cached in-process for 60 s. The cockpit hits
    /schema on page load + on every tab-complete, so caching pays for
    itself.
  - Query responses are NOT cached — they may be operator-visible
    one-shot reads with side-effect-y intent (re-run to see fresher
    data).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Optional

from app.db.client import get_client

logger = logging.getLogger(__name__)


# Hard ceilings — the request schemas use these as the field-level
# `le=` constraint, but we re-clamp here so a future schema relaxation
# doesn't silently widen the safety envelope.
_MAX_ROWS_CEILING = 30_000
_MAX_TIMEOUT_CEILING = 120
_HIDDEN_DATABASES = ("system", "INFORMATION_SCHEMA", "information_schema")

# Per-query settings applied to every read. These are the safety
# envelope ClickHouse itself enforces.
_READ_ONLY_SETTINGS: dict[str, Any] = {
    # 1 = read-only; 2 would also forbid changing settings mid-query.
    "readonly": 1,
    # Defensive cap on the volume scanned. ~1 GiB scan per query is
    # generous for the cockpit but stops the obvious unbounded
    # full-table scan.
    "max_bytes_to_read": 1 * 1024 * 1024 * 1024,
    # Soft memory cap per query so a Cartesian join doesn't OOM CH.
    "max_memory_usage": 4 * 1024 * 1024 * 1024,
}


_SCHEMA_CACHE_TTL_S = 60.0
_schema_cache: tuple[float, list[dict]] | None = None


def _now_unix() -> float:
    return time.monotonic()


def _json_safe(value: Any) -> Any:
    """Coerce a CH cell into a JSON-safe scalar.

    Handles datetimes (→ ISO string with 'Z' for naive), dates,
    bytes, and the various numeric types CH may return.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        # Best-effort decode; clients see strings or hex fallback.
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def _strip_trailing_semicolons(sql: str) -> str:
    """Strip trailing `;` (and any whitespace) — clickhouse-connect
    can choke on a single statement that ends with `;`."""
    return sql.rstrip().rstrip(";").rstrip()


class ClickHouseQueryService:
    """The cockpit's read-only ad-hoc query surface.

    Stateless service; uses `app.db.client.get_client()` for the
    underlying connection. `from_settings()` exists for future
    test injection.
    """

    @classmethod
    def from_settings(cls) -> "ClickHouseQueryService":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Schema browser
    # ─────────────────────────────────────────────────────────────────

    def list_schema(self) -> tuple[list[dict], bool]:
        """Return `(tables, cached)` where `tables` is a list of
        `{database, name, engine, row_count, columns: [{name, type}]}`.

        Hidden databases (`system`, `INFORMATION_SCHEMA`) are filtered out.
        """
        global _schema_cache

        if _schema_cache is not None:
            ts, cached_tables = _schema_cache
            if _now_unix() - ts < _SCHEMA_CACHE_TTL_S:
                return cached_tables, True

        client = get_client()
        hidden_clause = ",".join(f"'{d}'" for d in _HIDDEN_DATABASES)

        # Tables + row estimates
        tables_rows = client.query(
            f"""
            SELECT database, name, engine, total_rows
            FROM system.tables
            WHERE database NOT IN ({hidden_clause})
              AND is_temporary = 0
            ORDER BY database, name
            """
        ).result_rows

        # Columns (single query; group in Python)
        cols_rows = client.query(
            f"""
            SELECT database, table, name, type
            FROM system.columns
            WHERE database NOT IN ({hidden_clause})
            ORDER BY database, table, position
            """
        ).result_rows

        cols_by_table: dict[tuple[str, str], list[dict]] = {}
        for db, table, col_name, col_type in cols_rows:
            cols_by_table.setdefault((db, table), []).append(
                {"name": col_name, "type": col_type}
            )

        tables_out: list[dict] = []
        for db, name, engine, total_rows in tables_rows:
            tables_out.append(
                {
                    "database": db,
                    "name": name,
                    "engine": engine or "",
                    "row_count": int(total_rows) if total_rows is not None else None,
                    "columns": cols_by_table.get((db, name), []),
                }
            )

        _schema_cache = (_now_unix(), tables_out)
        return tables_out, False

    @staticmethod
    def invalidate_schema_cache() -> None:
        """Drop the cached schema. Useful in tests; also exposed so the
        operator can force a refresh from the cockpit later if we add
        the affordance."""
        global _schema_cache
        _schema_cache = None

    # ─────────────────────────────────────────────────────────────────
    # Query execution
    # ─────────────────────────────────────────────────────────────────

    def execute(
        self,
        sql: str,
        *,
        max_rows: int = 1000,
        timeout_seconds: int = 30,
    ) -> dict:
        """Execute `sql` read-only and return the cockpit response dict.

        Clamps `max_rows` and `timeout_seconds` against the hard
        ceilings so a relaxed request schema can't widen them.
        Returns `{columns, rows, row_count, truncated, duration_ms}`.

        Errors from CH propagate as-is to the route, which wraps them
        in the typed ErrorResponse envelope (FE-CONTRACTS-1).
        """
        max_rows = min(int(max_rows), _MAX_ROWS_CEILING)
        timeout_seconds = min(int(timeout_seconds), _MAX_TIMEOUT_CEILING)

        cleaned = _strip_trailing_semicolons(sql)
        if not cleaned:
            raise ValueError("empty query after trimming")

        settings = {
            **_READ_ONLY_SETTINGS,
            # Truncation detection: ask for one extra row over the cap so
            # we can tell whether the result was clipped. Pair with
            # `result_overflow_mode=break` so CH SILENTLY clips at
            # `max_result_rows` instead of throwing (CH's default is
            # `throw`, which would 5xx the cockpit).
            "max_result_rows": max_rows + 1,
            "result_overflow_mode": "break",
            "max_execution_time": timeout_seconds,
        }

        client = get_client()
        t0 = _now_unix()
        result = client.query(cleaned, settings=settings)
        duration_ms = (_now_unix() - t0) * 1000.0

        # Column metadata
        col_names = list(result.column_names or [])
        col_types_raw = result.column_types or []
        col_type_strs = [
            getattr(t, "name", str(t)) for t in col_types_raw
        ]
        columns = [
            {"name": n, "type": t}
            for n, t in zip(col_names, col_type_strs)
        ]

        rows = list(result.result_rows or [])
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]

        # JSON-safe coercion per cell
        safe_rows = [[_json_safe(c) for c in row] for row in rows]

        return {
            "columns": columns,
            "rows": safe_rows,
            "row_count": len(safe_rows),
            "truncated": truncated,
            "duration_ms": round(duration_ms, 2),
        }


# Module-level singleton — matches the other service adapters.
query_service = ClickHouseQueryService()
