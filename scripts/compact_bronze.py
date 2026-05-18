#!/usr/bin/env python3
"""
Compact bronze Iceberg tables via Athena's OPTIMIZE command.

**Why this exists:** the live_lake_writer (TA-5.7) writes a small
Iceberg file every 5 minutes (~30 KB × 288 cycles/day = ~8 MB across
hundreds of tiny files per day per provider). Iceberg's recommended
target file size is ~256 MB; tiny files bloat metadata and slow
queries.

This script runs Athena's `OPTIMIZE … REWRITE DATA USING BIN_PACK`
which merges small files into target-sized files. It's idempotent
and safe to run during market hours (Iceberg's snapshot isolation
keeps live writes consistent).

**Recommended cadence:** daily at 03:00 ET (after market close +
nightly_polygon_refresh + corp_actions_backfill, before market open).

**Usage:**

    poetry run python scripts/compact_bronze.py             # compact all bronze tables
    poetry run python scripts/compact_bronze.py --table schwab_minute
    poetry run python scripts/compact_bronze.py --dry-run   # print SQL, don't execute

**Requirements:**

- AWS credentials with `athena:StartQueryExecution`,
  `athena:GetQueryExecution`, S3 read/write on the lake bucket, and
  Glue catalog access. The default operator profile has these.
- `ATHENA_WORKGROUP` env var (default 'primary') — must have an
  Athena query result location configured.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.config import settings  # noqa: E402

logger = logging.getLogger(__name__)


# Bronze tables eligible for compaction. Add new bronze tables here
# when their writers land.
_COMPACTABLE = [
    "polygon_minute",
    "schwab_minute",
    "polygon_corp_actions",
]

# Athena OPTIMIZE command template. BIN_PACK is the standard
# strategy: pack small files into target-sized output files.
# See https://docs.aws.amazon.com/athena/latest/ug/optimize-statement.html
_OPTIMIZE_SQL = (
    "OPTIMIZE {db}.{table} REWRITE DATA USING BIN_PACK"
)


def _run_athena_query(sql: str, *, workgroup: str, output_location: Optional[str]) -> dict:
    """Submit an Athena query and poll until done.

    Returns the QueryExecution dict on success; raises on failure.
    """
    import boto3

    athena = boto3.client("athena", region_name=os.getenv("AWS_REGION", "us-east-1"))
    kwargs: dict = {
        "QueryString": sql,
        "WorkGroup": workgroup,
        "QueryExecutionContext": {"Database": settings.iceberg_glue_database},
    }
    if output_location:
        kwargs["ResultConfiguration"] = {"OutputLocation": output_location}

    submit = athena.start_query_execution(**kwargs)
    qid = submit["QueryExecutionId"]
    logger.info("Athena query submitted: %s", qid)

    deadline = time.time() + 30 * 60   # 30-min timeout
    while time.time() < deadline:
        info = athena.get_query_execution(QueryExecutionId=qid)
        state = info["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            logger.info("Athena query SUCCEEDED: %s", qid)
            return info["QueryExecution"]
        if state in ("FAILED", "CANCELLED"):
            reason = info["QueryExecution"]["Status"].get(
                "StateChangeReason", "(no reason)"
            )
            raise RuntimeError(f"Athena query {qid} {state}: {reason}")
        # Still running. Poll every 5s.
        time.sleep(5)
    raise TimeoutError(f"Athena query {qid} did not finish within 30 minutes")


def compact_one(table_short: str, *, workgroup: str, output_location: Optional[str], dry_run: bool) -> dict:
    """Run OPTIMIZE on one bronze table."""
    sql = _OPTIMIZE_SQL.format(
        db=settings.iceberg_glue_database, table=table_short,
    )
    logger.info("Compacting %s — SQL: %s", table_short, sql)
    if dry_run:
        return {"table": table_short, "sql": sql, "status": "dry_run"}

    started = time.time()
    info = _run_athena_query(sql, workgroup=workgroup, output_location=output_location)
    duration = time.time() - started
    stats = info.get("Statistics", {}) or {}
    return {
        "table": table_short,
        "status": "ok",
        "duration_s": duration,
        "data_scanned_bytes": stats.get("DataScannedInBytes"),
        "data_manifest_location": stats.get("DataManifestLocation"),
        "query_execution_id": info["QueryExecutionId"],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--table",
        choices=_COMPACTABLE,
        default=None,
        help="Compact only this bronze table (default: all).",
    )
    p.add_argument(
        "--workgroup",
        default=os.getenv("ATHENA_WORKGROUP", "primary"),
        help="Athena workgroup (default: ATHENA_WORKGROUP env or 'primary').",
    )
    p.add_argument(
        "--output-location",
        default=os.getenv("ATHENA_OUTPUT_LOCATION"),
        help=(
            "S3 URI for query results "
            "(default: ATHENA_OUTPUT_LOCATION env; workgroup default if unset)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would run, don't execute.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    tables = [args.table] if args.table else _COMPACTABLE
    results: list[dict] = []
    any_fail = False

    for table in tables:
        try:
            r = compact_one(
                table,
                workgroup=args.workgroup,
                output_location=args.output_location,
                dry_run=args.dry_run,
            )
            results.append(r)
            logger.info("  → %s done in %.1fs", table, r.get("duration_s", 0))
        except Exception as e:
            logger.exception("  ✗ %s failed: %s", table, e)
            results.append({"table": table, "status": "fail", "error": str(e)})
            any_fail = True

    print()
    print("─── compact_bronze summary ───")
    for r in results:
        status = r.get("status", "?")
        line = f"  {r['table']:<26} {status}"
        if "duration_s" in r:
            line += f"  {r['duration_s']:.1f}s"
        if r.get("error"):
            line += f"  error={r['error']}"
        print(line)
    print()

    return 0 if not any_fail else 2


if __name__ == "__main__":
    sys.exit(main())
