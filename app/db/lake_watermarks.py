"""
Watermark repository for the S3 lake archive.

This module owns the entire surface area for the
``lake_archive_watermarks`` ClickHouse table. It is intentionally small
and dependency-free so it can be:

  - mocked at the function boundary in unit tests (no ClickHouse needed)
  - swapped for a different store later (Postgres, SQLite) without
    touching the lake-archive service that depends on it
  - extracted to its own microservice if the audit trail outgrows the
    monolith (the ``WatermarkRepo`` Protocol is the IPC contract)

Schema (see ``app/db/init.py``)::

    CREATE TABLE lake_archive_watermarks (
        source        LowCardinality(String),
        table_name    LowCardinality(String),
        stage         LowCardinality(String),
        period_start  DateTime64(3, 'UTC'),
        period_end    DateTime64(3, 'UTC'),
        bars_archived UInt64 DEFAULT 0,
        s3_key        String DEFAULT '',
        status        LowCardinality(String) DEFAULT 'ok',
        error         String DEFAULT '',
        archived_at   DateTime64(3, 'UTC') DEFAULT now64(3),
        version       UInt64 DEFAULT 0
    )
    ENGINE = ReplacingMergeTree(version)
    ORDER BY (source, table_name, stage, period_start)

The ``ReplacingMergeTree(version)`` engine means re-runs of the same
``(source, table_name, stage, period_start)`` tuple silently dedupe to
the highest ``version`` — i.e. the most recent write wins, which is
exactly the idempotency contract the LakeArchiveWriter relies on.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Callable, List, Optional, Protocol

logger = logging.getLogger(__name__)


# Allowed status values, exported so the writer / sinks share one vocabulary.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_PARTIAL = "partial"

_STATUSES = frozenset({STATUS_OK, STATUS_ERROR, STATUS_PARTIAL})


@dataclass(frozen=True, slots=True)
class Watermark:
    """Read-side view of one row in ``lake_archive_watermarks``."""
    source: str
    table_name: str
    stage: str
    period_start: datetime
    period_end: datetime
    bars_archived: int
    s3_key: str
    status: str
    error: str
    archived_at: datetime


def _now_version() -> int:
    """Millisecond-precision version stamp. Matches the convention used
    elsewhere in ``app/db/queries.py`` so re-runs sort correctly."""
    return time.time_ns() // 1_000_000


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    """Compute UTC ``[start, end)`` bounds for one calendar day. The
    archive's natural unit is a UTC calendar day (Polygon publishes flat
    files at ~16:00 UTC for the prior session)."""
    start = datetime.combine(d, dtime.min, tzinfo=timezone.utc)
    # End at 23:59:59.999 UTC so range queries can use BETWEEN without
    # accidentally including the next day's 00:00:00 row.
    end = datetime.combine(d, dtime.max, tzinfo=timezone.utc).replace(microsecond=999000)
    return start, end


class WatermarkRepoProtocol(Protocol):
    """Subset of the repo that downstream code (LakeArchiveWriter, tests)
    actually uses. Codified as a Protocol so a future swap to e.g.
    Postgres only needs to satisfy these three methods."""

    async def record(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
        bars_archived: int,
        s3_key: str,
        status: str = STATUS_OK,
        error: str = "",
    ) -> None: ...

    async def get_status(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
    ) -> Optional[str]: ...

    async def get(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
    ) -> Optional[Watermark]: ...

    async def get_ok_dates(
        self,
        *,
        source: str,
        table_name: str,
        stage: str,
        start: date,
        end: date,
    ) -> set[date]: ...


class WatermarkRepo:
    """
    Async CRUD around ``lake_archive_watermarks``.

    Construction is explicit. Tests inject ``insert_fn`` and ``query_fn``
    directly so they never need a real ClickHouse client. Production
    callers either use the default (``from_clickhouse()``) which wires up
    ``app.db.client.get_client()`` lazily, or build their own using
    ``WatermarkRepo(insert_fn=..., query_fn=...)``.

    Both injection points are sync callables. ``record`` / ``get_status``
    / ``get`` wrap them in ``asyncio.to_thread`` so they can be awaited
    from FastAPI background tasks without leaking blocking I/O onto the
    event loop.
    """

    TABLE = "lake_archive_watermarks"

    # Column layout MUST match the schema in ``app/db/init.py``. Listed
    # explicitly so the insert call doesn't depend on Python dict order
    # for two ClickHouse versions back.
    COLUMNS: List[str] = [
        "source",
        "table_name",
        "stage",
        "period_start",
        "period_end",
        "bars_archived",
        "s3_key",
        "status",
        "error",
        "version",
    ]

    InsertFn = Callable[[str, List[List[Any]], List[str]], None]
    """``(table, rows, columns) -> None`` — matches clickhouse-connect.Client.insert."""

    QueryFn = Callable[[str, dict[str, Any]], List[tuple]]
    """``(sql, params) -> [(row, ...), ...]`` — matches clickhouse-connect.Client.query result rows."""

    def __init__(
        self,
        *,
        insert_fn: Optional[InsertFn] = None,
        query_fn: Optional[QueryFn] = None,
    ) -> None:
        self._insert_fn = insert_fn or self._default_insert
        self._query_fn = query_fn or self._default_query

    # ---------- factories ----------

    @classmethod
    def from_clickhouse(cls) -> "WatermarkRepo":
        """Build the canonical instance wired to the shared ClickHouse
        client. Imported lazily so importing this module is free for
        callers that only need the Protocol or constants."""
        return cls()

    # ---------- defaults that touch ClickHouse ----------

    @staticmethod
    def _default_insert(table: str, rows: List[List[Any]], cols: List[str]) -> None:
        # Lazy import keeps the module import free of ClickHouse for
        # consumers (tests, future microservice clients) that inject
        # their own ``insert_fn``.
        from app.db.client import get_client
        get_client().insert(table, rows, column_names=cols)

    @staticmethod
    def _default_query(sql: str, params: dict[str, Any]) -> List[tuple]:
        from app.db.client import get_client
        return get_client().query(sql, parameters=params).result_rows

    # ---------- write ----------

    async def record(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
        bars_archived: int,
        s3_key: str,
        status: str = STATUS_OK,
        error: str = "",
    ) -> None:
        """
        Stamp a watermark row for ``(source, table_name, stage, period)``.

        Idempotent by table engine: re-running with the same key yields
        a new ``version`` that supersedes the older row at merge time.
        Reads via ``get_status`` use ``FINAL`` to see the latest
        regardless of merge state.
        """
        if status not in _STATUSES:
            raise ValueError(
                f"WatermarkRepo.record: status must be one of {sorted(_STATUSES)}, "
                f"got {status!r}"
            )
        if bars_archived < 0:
            raise ValueError(
                f"WatermarkRepo.record: bars_archived must be >= 0, got {bars_archived}"
            )
        if not source or not table_name or not stage:
            raise ValueError(
                "WatermarkRepo.record: source / table_name / stage are all required"
            )
        start, end = _day_bounds(period)
        row = [
            source,
            table_name,
            stage,
            start,
            end,
            int(bars_archived),
            s3_key or "",
            status,
            error or "",
            _now_version(),
        ]
        # We deliberately push the sync insert through ``asyncio.to_thread``
        # rather than holding our own threadpool. Matches the pattern in
        # ``app.db.queries.insert_bars_batch_async``.
        await asyncio.to_thread(self._insert_fn, self.TABLE, [row], self.COLUMNS)

    # ---------- read ----------

    async def get_status(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
    ) -> Optional[str]:
        """
        Return the most recent ``status`` for the given key, or ``None``
        when no watermark row exists. Used by LakeArchiveWriter to skip
        days that already finished successfully.
        """
        watermark = await self.get(
            source=source, table_name=table_name, period=period, stage=stage,
        )
        return watermark.status if watermark else None

    async def get_ok_dates(
        self,
        *,
        source: str,
        table_name: str,
        stage: str,
        start: date,
        end: date,
    ) -> set[date]:
        """
        Return the set of dates with ``status='ok'`` watermarks in the
        inclusive ``[start, end]`` range.

        Used by the bulk-backfill CLI as a one-query resumability
        pre-scan: dates already in this set can be skipped without even
        downloading the flat file. Returns an empty set when no rows
        exist — semantically equivalent to "nothing done yet".

        ``FINAL`` is used so we see the post-merge state regardless of
        when ClickHouse last merged the ReplacingMergeTree parts.
        """
        if end < start:
            raise ValueError(f"end ({end}) is before start ({start})")
        start_dt, _ = _day_bounds(start)
        _, end_dt = _day_bounds(end)
        sql = (
            "SELECT toDate(period_start) AS d "
            f"FROM {self.TABLE} FINAL "
            "WHERE source = %(source)s "
            "  AND table_name = %(table_name)s "
            "  AND stage = %(stage)s "
            "  AND status = 'ok' "
            "  AND period_start >= %(start)s "
            "  AND period_start <= %(end)s "
            "GROUP BY d"
        )
        params = {
            "source": source,
            "table_name": table_name,
            "stage": stage,
            "start": start_dt,
            "end": end_dt,
        }
        rows = await asyncio.to_thread(self._query_fn, sql, params)
        out: set[date] = set()
        for r in rows:
            d = r[0]
            # ClickHouse Date columns come back as datetime.date; tolerate
            # either form for driver portability.
            if isinstance(d, date) and not isinstance(d, datetime):
                out.add(d)
            elif isinstance(d, datetime):
                out.add(d.date())
            else:
                out.add(date.fromisoformat(str(d)))
        return out

    async def get(
        self,
        *,
        source: str,
        table_name: str,
        period: date,
        stage: str,
    ) -> Optional[Watermark]:
        """Return the full latest row, or ``None``. Uses ``FINAL`` so we
        see the post-merge version even when ClickHouse hasn't merged
        yet (small extra cost; this table only has a handful of rows
        per day so it's negligible)."""
        start, _ = _day_bounds(period)
        sql = (
            "SELECT source, table_name, stage, period_start, period_end, "
            "bars_archived, s3_key, status, error, archived_at "
            f"FROM {self.TABLE} FINAL "
            "WHERE source = %(source)s "
            "  AND table_name = %(table_name)s "
            "  AND stage = %(stage)s "
            "  AND period_start = %(period_start)s "
            "LIMIT 1"
        )
        params = {
            "source": source,
            "table_name": table_name,
            "stage": stage,
            "period_start": start,
        }
        rows = await asyncio.to_thread(self._query_fn, sql, params)
        if not rows:
            return None
        r = rows[0]
        return Watermark(
            source=str(r[0]),
            table_name=str(r[1]),
            stage=str(r[2]),
            period_start=_ensure_utc(r[3]),
            period_end=_ensure_utc(r[4]),
            bars_archived=int(r[5]),
            s3_key=str(r[6] or ""),
            status=str(r[7] or ""),
            error=str(r[8] or ""),
            archived_at=_ensure_utc(r[9]),
        )


def _ensure_utc(ts: Any) -> datetime:
    """Coerce a ClickHouse datetime to a timezone-aware UTC ``datetime``.
    The driver returns naive datetimes for some versions; we treat them
    as UTC (which matches the column's declared timezone)."""
    if not isinstance(ts, datetime):
        # Shouldn't happen with our schema, but degrade gracefully.
        return datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


__all__ = [
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "Watermark",
    "WatermarkRepo",
    "WatermarkRepoProtocol",
]
