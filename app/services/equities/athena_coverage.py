"""Athena-backed per-symbol coverage for the v2 equities lake tables.

Replaces the old full-data-scan coverage in
``AdjustedOhlcvReader._coverage_for``, which materialized **every row**
for a symbol via PyIceberg ``.to_arrow()`` and then min/max'd in Python
— 106 s for AAPL (3.4M rows pulled from S3 just to compute min/max/count).

Athena pushes the aggregate down and prunes by the symbol's
``bucket(N, symbol)`` partitions, reading only the ``timestamp`` column
of the matching files: **exact** min/max/count in ~2-3 s. This is the
"use metadata / engine pushdown, not brute-force materialization"
principle the coverage path was missing.

Athena dialect: DML uses Trino double-quoted identifiers + single-quoted
string literals — see ``docs/standards/data/athena_dialects.md``.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import boto3

from app.config import settings

logger = logging.getLogger(__name__)

_ATHENA_WORKGROUP = "primary"
# Symbols are uppercase tickers; restrict to a safe charset as defence in
# depth on top of the single-quote escaping below (the value is inlined as
# a Trino string literal).
_SAFE_SYMBOL = re.compile(r"[^A-Z0-9.$/_-]")


@dataclass
class AthenaCoverage:
    """Exact per-(symbol, table) coverage from an Athena aggregate query."""

    row_count: int
    earliest: Optional[datetime]
    latest: Optional[datetime]


def _athena_output() -> str:
    return f"s3://{settings.stock_lake_bucket}/athena-results/"


def symbol_coverage(
    table: str, symbol: str, *, timeout_s: float = 90.0,
) -> Optional[AthenaCoverage]:
    """Return exact min/max(timestamp) + count(*) for `symbol` in
    ``equities.<table>`` via Athena aggregate pushdown.

    `table` is the short Iceberg table name (e.g. ``"polygon_adjusted"``
    or ``"schwab_universe"``); it is qualified with the equities Glue DB.

    Returns ``None`` on any failure (missing table, query error, timeout)
    so callers degrade to empty coverage rather than raising — matches
    the cold-start-safe contract of the old scan path.
    """
    db = settings.iceberg_equities_glue_database
    sym = _SAFE_SYMBOL.sub("", (symbol or "").upper()).replace("'", "''")
    if not sym:
        return AthenaCoverage(0, None, None)

    # DML → Trino dialect: double-quote identifiers, single-quote the literal.
    sql = (
        'SELECT count(*) AS n, min("timestamp") AS e, max("timestamp") AS l '
        f'FROM "{db}"."{table}" WHERE "symbol" = \'{sym}\''
    )

    try:
        cli = boto3.client("athena", region_name=settings.stock_lake_region)
        qid = cli.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": db, "Catalog": "AwsDataCatalog"},
            ResultConfiguration={"OutputLocation": _athena_output()},
            WorkGroup=_ATHENA_WORKGROUP,
        )["QueryExecutionId"]

        started = time.monotonic()
        while True:
            state = cli.get_query_execution(QueryExecutionId=qid)[
                "QueryExecution"
            ]["Status"]["State"]
            if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            if time.monotonic() - started > timeout_s:
                logger.warning(
                    "athena coverage %s.%s for %s timed out after %.0fs",
                    db, table, sym, timeout_s,
                )
                return None
            time.sleep(0.5)

        if state != "SUCCEEDED":
            logger.warning("athena coverage %s.%s for %s -> %s", db, table, sym, state)
            return None

        res = cli.get_query_results(QueryExecutionId=qid)
        rows = res.get("ResultSet", {}).get("Rows", [])
        if len(rows) < 2:  # header only -> no data
            return AthenaCoverage(0, None, None)
        data = rows[1].get("Data", [])
        n = int(data[0].get("VarCharValue") or 0)
        earliest = _parse_athena_ts(data[1].get("VarCharValue"))
        latest = _parse_athena_ts(data[2].get("VarCharValue"))
        return AthenaCoverage(row_count=n, earliest=earliest, latest=latest)
    except Exception as exc:  # noqa: BLE001 — boundary; degrade to None
        logger.warning("athena coverage %s.%s for %s failed: %s", db, table, sym, exc)
        return None


def _parse_athena_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse Athena's timestamp string ('YYYY-MM-DD HH:MM:SS[.ffffff] UTC')."""
    if not value:
        return None
    s = value.replace("UTC", "").strip()
    dt: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
