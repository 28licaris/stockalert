"""Production adapters for lake integrity: ClickHouse reader, Iceberg index and writer.

Heavy dependencies (clickhouse-connect, pyiceberg, boto3) are imported inside
the methods, per docs/standards/service_modules.md. The writer opens tables with
``load_table`` and never creates anything: a missing table is a loud error, and
the instance role needs no ``glue:CreateDatabase``.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pyarrow as pa

from app.services.lake_integrity.schemas import Bar, BarFamily

logger = logging.getLogger(__name__)

_COMMIT_RETRIES = 5


def _utc_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _ensure_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


class ClickHouseBarSource:
    """Reads surviving (``FINAL``) rows whose source has no other copy."""

    def __init__(self, client: Optional[Any] = None) -> None:
        self._client = client

    def _c(self) -> Any:
        if self._client is None:
            from app.db.client import get_client

            self._client = get_client()
        return self._client

    def days(self, family: BarFamily, since: date, until: date) -> list[date]:
        lo, _ = _utc_day_bounds(since)
        _, hi = _utc_day_bounds(until)
        rows = self._c().query(
            f"SELECT DISTINCT toDate(timestamp) AS d FROM {family.ch_table} FINAL "
            "WHERE source IN {sources:Array(String)} "
            "AND timestamp >= {lo:DateTime64(3, 'UTC')} AND timestamp < {hi:DateTime64(3, 'UTC')} "
            "ORDER BY d",
            parameters={"sources": list(family.sources), "lo": lo, "hi": hi},
        ).result_rows
        return [r[0] for r in rows]

    def bars(self, family: BarFamily, day: date) -> list[Bar]:
        lo, hi = _utc_day_bounds(day)
        rows = self._c().query(
            "SELECT symbol, timestamp, open, high, low, close, volume, vwap, trade_count, source "
            f"FROM {family.ch_table} FINAL "
            "WHERE source IN {sources:Array(String)} "
            "AND timestamp >= {lo:DateTime64(3, 'UTC')} AND timestamp < {hi:DateTime64(3, 'UTC')} "
            "ORDER BY symbol, timestamp",
            parameters={"sources": list(family.sources), "lo": lo, "hi": hi},
        ).named_results()
        out: list[Bar] = []
        for r in rows:
            out.append(
                Bar(
                    symbol=r["symbol"],
                    timestamp=_ensure_utc(r["timestamp"]),
                    open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                    volume=r["volume"],
                    # ClickHouse stores "absent" as 0; the lake stores it as null.
                    vwap=r["vwap"] or None,
                    trade_count=int(r["trade_count"]) if r["trade_count"] else None,
                    source=r["source"],
                )
            )
        return out


class IcebergBarIndex:
    """Reads the (symbol, timestamp) keys the lake table holds for one UTC day.

    Planning an Iceberg scan over this table's many small files costs seconds, so
    a scan covers ``CHUNK_DAYS`` days and is cached; neighbouring days are free.
    ``fresh=True`` re-reads the chunk (used to verify a write).
    """

    CHUNK_DAYS = 7

    def __init__(self, catalog: Optional[Any] = None) -> None:
        self._catalog = catalog
        self._cache_id: Optional[tuple[str, date]] = None
        self._cache: dict[date, set[tuple[str, datetime]]] = {}

    def _cat(self) -> Any:
        if self._catalog is None:
            from app.services.iceberg_catalog import get_catalog

            self._catalog = get_catalog()
        return self._catalog

    def keys(self, family: BarFamily, day: date, *, fresh: bool = False) -> set[tuple[str, datetime]]:
        chunk_start = day - timedelta(days=day.toordinal() % self.CHUNK_DAYS)
        cache_id = (family.lake_table, chunk_start)
        if fresh or self._cache_id != cache_id:
            self._cache = self._scan(family, chunk_start)
            self._cache_id = cache_id
        return set(self._cache.get(day, set()))

    def _scan(self, family: BarFamily, chunk_start: date) -> dict[date, set[tuple[str, datetime]]]:
        table = self._cat().load_table(family.lake_table)
        table.refresh()
        start, _ = _utc_day_bounds(chunk_start)
        end = start + timedelta(days=self.CHUNK_DAYS)
        arrow = table.scan(
            row_filter=f"timestamp >= '{start.isoformat()}' AND timestamp < '{end.isoformat()}'",
            selected_fields=("symbol", "timestamp"),
        ).to_arrow()
        by_day: dict[date, set[tuple[str, datetime]]] = {}
        for sym, t in zip(arrow.column("symbol").to_pylist(), arrow.column("timestamp").to_pylist()):
            by_day.setdefault(_ensure_utc(t).date(), set()).add((sym, t))
        return by_day


def bars_to_arrow(
    family: BarFamily,
    bars: list[Bar],
    run_id: str,
    schema: pa.Schema,
    now: Optional[datetime] = None,
) -> pa.Table:
    """Bars -> an Arrow table in the lake table's own schema.

    Schwab prices are pre-adjusted, so equities rows carry ``adj_factor = 1.0``
    (the same literal the live writer stamps). Provenance is explicit:
    ``source`` keeps the ClickHouse tag and ``ingestion_run_id`` names this run.
    A schema field this function does not know raises ``KeyError`` instead of
    writing nulls into a column it does not understand.
    """
    now = now or datetime.now(timezone.utc)
    cols: dict[str, list] = {
        "symbol": [b.symbol for b in bars],
        "timestamp": [b.timestamp for b in bars],
        "open": [b.open for b in bars],
        "high": [b.high for b in bars],
        "low": [b.low for b in bars],
        "close": [b.close for b in bars],
        "volume": [b.volume for b in bars],
        "vwap": [b.vwap for b in bars],
        "trade_count": [b.trade_count for b in bars],
        "source": [b.source for b in bars],
        "ingestion_ts": [now] * len(bars),
        "ingestion_run_id": [run_id] * len(bars),
    }
    if family.has_adj_factor:
        cols["adj_factor"] = [1.0] * len(bars)
    return pa.Table.from_pydict({f.name: cols[f.name] for f in schema}, schema=schema)


class IcebergBarWriter:
    """Appends bars to the lake table (bronze appends; readers dedup)."""

    def __init__(self, catalog: Optional[Any] = None) -> None:
        self._catalog = catalog

    def _cat(self) -> Any:
        if self._catalog is None:
            from app.services.iceberg_catalog import get_catalog

            self._catalog = get_catalog()
        return self._catalog

    def append(self, family: BarFamily, bars: list[Bar], run_id: str) -> int:
        from pyiceberg.exceptions import CommitFailedException

        table = self._cat().load_table(family.lake_table)
        arrow = bars_to_arrow(family, bars, run_id, table.schema().as_arrow())
        for attempt in range(1, _COMMIT_RETRIES + 1):
            try:
                table.refresh()  # another writer (live writer, nightly) may have committed
                table.append(arrow)
                return arrow.num_rows
            except CommitFailedException as exc:
                logger.warning(
                    "lake_archive: commit conflict on %s (attempt %d/%d): %s",
                    family.lake_table, attempt, _COMMIT_RETRIES, exc,
                )
                if attempt == _COMMIT_RETRIES:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def select_list(columns: list[tuple[str, str]]) -> str:
    """Column list for exporting a table through Arrow.

    ClickHouse cannot return ``UUID`` over Arrow, so those columns are exported as
    strings (``toString`` keeps NULL for ``Nullable(UUID)``) and ``insert_arrow``
    parses them back on restore (verified 2026-11-05). Any other type Arrow cannot
    carry raises, the table is reported as failed, and the snapshot is not published.
    """
    if not columns:
        return "*"
    return ", ".join(f"toString(`{n}`) AS `{n}`" if "UUID" in t else f"`{n}`" for n, t in columns)


class ClickHouseStateClient:
    """ClickHouse access for the state snapshot (Arrow in and out)."""

    def __init__(self, client: Optional[Any] = None) -> None:
        self._client = client

    def _c(self) -> Any:
        if self._client is None:
            from app.db.client import get_admin_client

            self._client = get_admin_client()  # DDL (CREATE/TRUNCATE) needs the admin client
        return self._client

    def list_tables(self, database: str) -> list[tuple[str, str]]:
        rows = self._c().query(
            "SELECT name, engine FROM system.tables WHERE database = {db:String} AND NOT is_temporary",
            parameters={"db": database},
        ).result_rows
        return [(r[0], r[1]) for r in rows]

    def create_ddl(self, database: str, table: str) -> str:
        return self._c().query(f"SHOW CREATE TABLE {database}.{table}").result_rows[0][0]

    def read_table(self, database: str, table: str, *, final: bool) -> pa.Table:
        cols = self._c().query(
            "SELECT name, type FROM system.columns WHERE database = {db:String} AND table = {t:String} ORDER BY position",
            parameters={"db": database, "t": table},
        ).result_rows
        select = select_list([(r[0], r[1]) for r in cols])
        return self._c().query_arrow(f"SELECT {select} FROM {database}.{table}{' FINAL' if final else ''}")

    def row_count(self, database: str, table: str) -> int:
        return int(self._c().query(f"SELECT count() FROM {database}.{table}").result_rows[0][0])

    def insert(self, database: str, table: str, data: pa.Table) -> None:
        self._c().insert_arrow(table, data, database=database)

    def coverage(self, database: str, table: str) -> list[tuple[str, datetime, datetime, int]]:
        """Per symbol: first bar, last bar and the number of DISTINCT minutes.

        ``rows`` is distinct timestamps, not ``count()``: the bar tables are
        ReplacingMergeTree, and until a merge runs a symbol holds duplicate rows (AAPL on
        the laptop: 5.88M rows for fewer than 5.03M minutes that can exist), which made
        the post-rebuild verify demand more rows than there are minutes. Counted one symbol
        at a time: a single ``GROUP BY symbol`` with ``uniqExact`` exhausts ClickHouse's
        memory on the dev box (measured: 2.7 GiB limit hit), while one symbol at a time
        peaks around 0.5 GiB (about 160 s for 425 symbols).
        """
        c = self._c()
        spans = c.query(
            f"SELECT symbol, min(timestamp), max(timestamp) FROM {database}.{table} "
            "GROUP BY symbol ORDER BY symbol"
        ).result_rows
        out: list[tuple[str, datetime, datetime, int]] = []
        for sym, first, last in spans:
            n = c.query(
                f"SELECT uniqExact(timestamp) FROM {database}.{table} WHERE symbol = {{s:String}}",
                parameters={"s": sym},
            ).result_rows[0][0]
            out.append((sym, _ensure_utc(first), _ensure_utc(last), int(n)))
        return out

    def command(self, sql: str) -> None:
        self._c().command(sql)


class S3ObjectStore:
    """Minimal S3 object store over boto3 (credentials from the default chain)."""

    def __init__(self, bucket: str, region: str) -> None:
        if not bucket:
            raise RuntimeError("STOCK_LAKE_BUCKET is not configured; the state snapshot has nowhere to go")
        self._bucket, self._region, self._s3 = bucket, region, None

    def _c(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    def put(self, key: str, data: bytes) -> None:
        self._c().put_object(Bucket=self._bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self._c().get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def size(self, key: str) -> Optional[int]:
        from botocore.exceptions import ClientError

        try:
            return int(self._c().head_object(Bucket=self._bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self._c().get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return keys

    def delete(self, key: str) -> None:
        self._c().delete_object(Bucket=self._bucket, Key=key)
