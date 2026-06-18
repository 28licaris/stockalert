"""Athena-backed single-symbol bar extraction for the CH fill path.

Why Athena (not PyIceberg / CH-direct): ``equities.polygon_adjusted`` is
the whole-market store — 6.8B rows, ``bucket(32, symbol)`` partitioned,
with uncompacted merge-on-read deletes (~3.9x raw-row bloat). Pulling one
symbol out of it is slow everywhere EXCEPT Athena, which natively (a)
prunes the bucket partition and (b) applies the position-deletes. For a
single symbol-year:

    CH iceberg() direct .......... >180 s  (no bucket pruning)
    CH s3() over bucket files ..... 31 s   (reads 1000 symbols + bloat)
    PyIceberg .to_arrow() ......... 41 s   (Python + 3.9x bloat)
    Athena UNLOAD ................. ~4-6 s  (prunes + dedups server-side)

Flow: UNLOAD the UNION of polygon_adjusted ∪ schwab_universe for the
window (polygon wins overlaps, like AdjustedOhlcvReader.get_bars_union)
→ clean per-symbol Parquet on S3 → read it (small, deduped) → return an
Arrow table the caller inserts into ClickHouse. Temp output is deleted.

Athena dialect: DML uses Trino double-quoted identifiers + single-quoted
literals — see ``docs/standards/data/athena_dialects.md``.
"""
from __future__ import annotations

import io
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Optional

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from app.config import settings

logger = logging.getLogger(__name__)

_ATHENA_WORKGROUP = "primary"
_SAFE_SYMBOL = re.compile(r"[^A-Z0-9.$/_-]")


def _athena_results() -> str:
    return f"s3://{settings.stock_lake_bucket}/athena-results/"


def _tmp_prefix(symbol: str) -> str:
    # Unique per call so concurrent fills don't collide; cleaned up after.
    return f"tmp/ch_fill/{symbol}/{uuid.uuid4().hex}"


def extract_symbol_window(
    symbol: str, start: datetime, end: datetime, *, timeout_s: float = 120.0,
) -> Optional[pa.Table]:
    """Return a deduped Arrow table of `symbol` bars in [start, end) from
    the lake (polygon_adjusted ∪ schwab_universe, polygon wins), via
    Athena UNLOAD. Columns: symbol, timestamp, open, high, low, close,
    volume, vwap, trade_count, source.

    Returns an empty table if the window has no data, or None on any
    Athena/read failure (caller degrades — never raises).
    """
    db = settings.iceberg_equities_glue_database
    sym = _SAFE_SYMBOL.sub("", (symbol or "").upper())
    if not sym:
        return pa.table({})

    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    prefix = _tmp_prefix(sym)
    out_loc = f"s3://{settings.stock_lake_bucket}/{prefix}/"

    # UNLOAD writes Parquet to its own (must-be-empty) location. Union the
    # two sources, dedup on (symbol, timestamp) with polygon (pri=1)
    # winning, and CAST the timestamptz → timestamp(6) (Hive Parquet has
    # no timestamptz). bounds via from_iso8601_timestamp → timestamptz.
    def _src(table: str, pri: int) -> str:
        return (
            f'SELECT symbol, "timestamp", open, high, low, close, volume, '
            f'vwap, trade_count, source, {pri} AS pri '
            f'FROM "{db}"."{table}" '
            f"WHERE symbol = '{sym}' "
            f"AND \"timestamp\" >= from_iso8601_timestamp('{start_iso}') "
            f"AND \"timestamp\" <  from_iso8601_timestamp('{end_iso}')"
        )

    sql = (
        f"UNLOAD (\n"
        f"  SELECT symbol, ts AS \"timestamp\", open, high, low, close, "
        f"volume, vwap, trade_count, source FROM (\n"
        f"    SELECT symbol, CAST(\"timestamp\" AS timestamp(3)) AS ts, "
        f"open, high, low, close, volume, vwap, trade_count, source,\n"
        f"      row_number() OVER (PARTITION BY symbol, \"timestamp\" "
        f"ORDER BY pri) AS rn\n"
        f"    FROM (\n      {_src('polygon_adjusted', 1)}\n"
        f"      UNION ALL\n      {_src('schwab_universe', 2)}\n    )\n"
        f"  ) WHERE rn = 1\n"
        f")\nTO '{out_loc}'\nWITH (format = 'PARQUET', compression = 'SNAPPY')"
    )

    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    try:
        if not _run_athena(sql, timeout_s=timeout_s):
            return None
        table = _read_parquet_prefix(s3, prefix)
        return table
    except Exception as exc:  # noqa: BLE001 — boundary; degrade to None
        logger.warning("athena_extract %s [%s,%s) failed: %s", sym, start_iso, end_iso, exc)
        return None
    finally:
        _cleanup_prefix(s3, prefix)


def _run_athena(sql: str, *, timeout_s: float) -> bool:
    cli = boto3.client("athena", region_name=settings.stock_lake_region)
    qid = cli.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={
            "Database": settings.iceberg_equities_glue_database,
            "Catalog": "AwsDataCatalog",
        },
        ResultConfiguration={"OutputLocation": _athena_results()},
        WorkGroup=_ATHENA_WORKGROUP,
    )["QueryExecutionId"]

    started = time.monotonic()
    while True:
        q = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = q["Status"]["State"]
        if state == "SUCCEEDED":
            return True
        if state in {"FAILED", "CANCELLED"}:
            logger.warning(
                "athena_extract query %s: %s",
                state, q["Status"].get("StateChangeReason", ""),
            )
            return False
        if time.monotonic() - started > timeout_s:
            logger.warning("athena_extract query timed out after %.0fs", timeout_s)
            return False
        time.sleep(0.5)


def _read_parquet_prefix(s3, prefix: str) -> pa.Table:
    """Read all data objects UNLOAD wrote under `prefix` → one Arrow table.

    Athena UNLOAD-to-PARQUET names its output files with a bare GUID (no
    `.parquet` extension) and writes nothing else to the (unique, clean)
    location — so read every non-empty object under the prefix.
    """
    bucket = settings.stock_lake_bucket
    keys = [
        o["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix + "/"
        )
        for o in page.get("Contents", [])
        if o["Size"] > 0
    ]
    if not keys:
        return pa.table({})
    tables = []
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        tables.append(pq.read_table(io.BytesIO(body)))
    return pa.concat_tables(tables) if len(tables) > 1 else tables[0]


def _cleanup_prefix(s3, prefix: str) -> None:
    bucket = settings.stock_lake_bucket
    try:
        keys = [
            {"Key": o["Key"]}
            for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=prefix + "/"
            )
            for o in page.get("Contents", [])
        ]
        for i in range(0, len(keys), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys[i : i + 1000]})
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        logger.warning("athena_extract cleanup %s failed: %s", prefix, exc)
