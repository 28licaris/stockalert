"""
Monthly bronze compaction CLI.

Runs Athena `OPTIMIZE … REWRITE DATA USING BIN_PACK` scoped to one
month's partition. Intended use:
  - First Sunday of each month, target the just-closed prior month.
  - After a backfill that wrote many small daily files to a partition.

Safety rails:
  - Refuses to compact months older than 90 days. Glacier IR's 90-day
    minimum-storage rule means rewriting tiered files triggers early-
    deletion fees. Override with --force if you really mean it.

Run:
    poetry run python scripts/compact_bronze_monthly.py --month 2024-07
    poetry run python scripts/compact_bronze_monthly.py            # = prior month
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bronze-compaction")

DB = settings.iceberg_glue_database
BUCKET = settings.stock_lake_bucket
ATHENA_OUTPUT = f"s3://{BUCKET}/athena-results/"
WORKGROUP = "primary"
TABLE = "polygon_minute"
MAX_AGE_DAYS_BEFORE_FORCE = 90  # Glacier IR minimum-storage protection


def _previous_month(today: date) -> str:
    """Return the just-closed prior month as YYYY-MM."""
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def _month_age_days(month: str, today: date) -> int:
    """Days between the END of `month` and `today`."""
    year, m = month.split("-")
    year, m = int(year), int(m)
    # Last day of `month` is the day before the first of the next month.
    if m == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, m + 1, 1)
    return (today - month_end).days


def _run(athena, sql: str) -> dict:
    log.info("→ %s", sql.strip().splitlines()[0][:120])
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DB, "Catalog": "AwsDataCatalog"},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
        WorkGroup=WORKGROUP,
    )
    qid = resp["QueryExecutionId"]
    started = time.monotonic()
    while True:
        time.sleep(2)
        qstate = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        status = qstate["Status"]["State"]
        if status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        elapsed = time.monotonic() - started
        if int(elapsed) % 30 == 0:
            log.info("  …%s after %.0fs", status, elapsed)
    elapsed = time.monotonic() - started
    if status != "SUCCEEDED":
        reason = qstate["Status"].get("StateChangeReason", "(no reason)")
        raise RuntimeError(f"Athena {status} in {elapsed:.0f}s: {reason}")
    stats = qstate.get("Statistics", {}) or {}
    log.info(
        "✓ in %.0fs · scanned=%.2f GB",
        elapsed,
        stats.get("DataScannedInBytes", 0) / (1024 ** 3),
    )
    return qstate


def _file_count_in_month(s3, month: str) -> int:
    prefix = f"{settings.iceberg_warehouse_prefix}/bronze/{TABLE}/data/ts_month={month}/"
    count = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        count += sum(1 for o in page.get("Contents", []) or [] if o["Key"].endswith(".parquet"))
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM (default: just-closed prior month)")
    ap.add_argument("--force", action="store_true",
                    help="override the 90-day age safety check")
    args = ap.parse_args()

    today = datetime.now(tz=timezone.utc).date()
    month = args.month or _previous_month(today)

    if not (len(month) == 7 and month[4] == "-"):
        sys.exit(f"--month must be YYYY-MM, got {month!r}")

    age = _month_age_days(month, today)
    log.info("Target month: %s (closed %d days ago)", month, age)

    if age > MAX_AGE_DAYS_BEFORE_FORCE and not args.force:
        sys.exit(
            f"Refusing to compact {month}: closed {age} days ago. "
            f"Files may have aged into Glacier IR (90-day minimum). "
            f"Pass --force to override."
        )

    athena = boto3.client("athena", region_name=settings.stock_lake_region)
    s3 = boto3.client("s3", region_name=settings.stock_lake_region)

    files_before = _file_count_in_month(s3, month)
    log.info("Files in ts_month=%s before: %d", month, files_before)
    if files_before == 0:
        log.info("Nothing to compact.")
        return
    if files_before == 1:
        log.info("Already a single file — compaction would be a no-op.")
        return

    # Iceberg OPTIMIZE supports a WHERE clause to scope to a partition.
    # We filter on the underlying `timestamp` column; Iceberg's partition
    # pruning rewrites only files whose `month(timestamp)` matches.
    month_start = f"{month}-01"
    if month.endswith("-12"):
        next_month = f"{int(month[:4]) + 1}-01-01"
    else:
        y, m = month.split("-")
        next_month = f"{y}-{int(m) + 1:02d}-01"

    sql = f"""
        OPTIMIZE {DB}.{TABLE} REWRITE DATA USING BIN_PACK
        WHERE "timestamp" >= TIMESTAMP '{month_start} 00:00:00 UTC'
          AND "timestamp" <  TIMESTAMP '{next_month} 00:00:00 UTC'
    """
    _run(athena, sql)

    files_after = _file_count_in_month(s3, month)
    log.info("Files in ts_month=%s after:  %d  (Δ=%+d)",
             month, files_after, files_after - files_before)


if __name__ == "__main__":
    main()
