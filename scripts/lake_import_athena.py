"""
CV10' — server-side import of 5y Polygon flat-file Parquets into
`equities.polygon_raw` via AWS Athena.

The v2 replacement for the v1 `bronze_import_athena.py`. Same path,
same speed, same cost — just retargeted at the v2 equities namespace.

Why Athena: doing this from a laptop bottlenecks on home internet
(~30 MB/s round-trip). Athena reads + writes entirely inside AWS — no
laptop egress. Wall time goes from ~8 hours to a few minutes; cost is
~$0.20 in scan fees. We DO NOT re-query Polygon — the flat-file
Parquets at `s3://{STOCK_LAKE_BUCKET}/raw/provider=polygon-flatfiles/
kind=minute/year=YYYY/date=YYYY-MM-DD.parquet` are the source of truth
(already paid for, already in our S3).

Steps:
  1. (PyIceberg) Recreate the empty `equities.polygon_raw` table with
     the target schema, partition spec, sort order, identifier fields.
  2. (Athena DDL) Register the raw Polygon Parquets as an external
     table `equities.raw_polygon_minute_ext` with partition projection
     on `year`.
  3. (Athena DML) `INSERT INTO equities.polygon_raw` from the external
     table, projecting only the columns we want (drops the source
     Parquet's `__index_level_0__` if present), dropping garbage rows
     with NULL symbol or NULL timestamp.
  4. (Athena DML) `OPTIMIZE … REWRITE DATA USING BIN_PACK` — Iceberg's
     BIN_PACK respects the table's defined sort order
     (symbol ASC, timestamp ASC, per CV1), so the final compacted
     files are sorted per-file.
  5. (Verify) Row-count parity between external + Iceberg, plus the
     final Iceberg data-file count.

ONE-TIME OPERATION. Drops + recreates the target. If `equities.
polygon_raw` already has live-tier rows (post-CV7 nightly writes),
the script refuses without `--force` to avoid wiping production
data. Operational sequencing per docs/architecture_v2/07_runbook.md:
this script runs ONCE, in the cutover window, BEFORE CV7 deploys to
production.

Run:
    poetry run python scripts/lake_import_athena.py
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from app.services.equities.schemas import equities_table_id  # noqa: E402
from app.services.equities.tables import ensure_polygon_raw  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lake-import-athena")

DB = settings.iceberg_equities_glue_database
TARGET_TABLE = "polygon_raw"
BUCKET = settings.stock_lake_bucket
RAW_LOC = f"s3://{BUCKET}/raw/provider=polygon-flatfiles/kind=minute/"
ATHENA_OUTPUT = f"s3://{BUCKET}/athena-results/"
WORKGROUP = "primary"
EXTERNAL_TABLE = "raw_polygon_minute_ext"


class AthenaClient:
    def __init__(self):
        self.cli = boto3.client("athena", region_name=settings.stock_lake_region)

    def run(self, sql: str, expect_rows: bool = False) -> dict:
        """Submit a query, poll until done, return the final state.

        Raises on failure. If expect_rows, returns the first page of results.
        """
        sql_preview = sql.strip().splitlines()[0][:120]
        log.info("→ %s", sql_preview + ("..." if len(sql_preview) > 119 else ""))
        resp = self.cli.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": DB, "Catalog": "AwsDataCatalog"},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
            WorkGroup=WORKGROUP,
        )
        qid = resp["QueryExecutionId"]

        # Poll
        started = time.monotonic()
        while True:
            time.sleep(2)
            qstate = self.cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            status = qstate["Status"]["State"]
            if status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            elapsed = time.monotonic() - started
            if int(elapsed) % 20 == 0:
                log.info("  …%s after %.0fs", status, elapsed)

        elapsed = time.monotonic() - started
        stats = qstate.get("Statistics", {}) or {}
        if status != "SUCCEEDED":
            reason = qstate["Status"].get("StateChangeReason", "(no reason)")
            log.error("✗ %s in %.0fs: %s", status, elapsed, reason)
            raise RuntimeError(f"Athena query {status}: {reason}\nSQL:\n{sql}")

        log.info(
            "✓ SUCCEEDED in %.0fs  scanned=%s  cost≈$%.4f",
            elapsed,
            _fmt_bytes(stats.get("DataScannedInBytes", 0)),
            stats.get("DataScannedInBytes", 0) / (1024**4) * 5.0,
        )

        if expect_rows:
            results = self.cli.get_query_results(QueryExecutionId=qid)
            return results
        return qstate


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ─────────────────────────────────────────────────────────────────────
# Step 0 — pre-flight safety check
# ─────────────────────────────────────────────────────────────────────
def step0_preflight(athena: "AthenaClient", *, force: bool) -> None:
    """Refuse to drop `equities.polygon_raw` if it has live-tier rows.

    The script's step1 drops + recreates the target. Doing that on a
    table the CV7 nightly cron has been writing to wipes the live
    data. Run THIS script BEFORE CV7 deploys to production; if
    you're re-importing after CV7 already started writing, pass
    --force and accept that the post-CV7 rows are about to die.
    """
    try:
        rs = athena.run(
            f'SELECT count(*) AS n FROM {DB}.{TARGET_TABLE}',
            expect_rows=True,
        )
        n = int(rs["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])
    except Exception as e:
        # Most likely "table does not exist yet" — that's the happy
        # path for a first-time import. Anything else, surface so the
        # operator can decide.
        log.info("preflight: target row-count probe failed (%s) — "
                 "assuming empty/missing target, proceeding", e)
        return

    if n == 0:
        log.info("preflight: equities.polygon_raw is empty — ok to proceed")
        return

    if force:
        log.warning(
            "preflight: equities.polygon_raw has %d row(s); --force "
            "set, will WIPE and re-import", n,
        )
        return

    log.error(
        "preflight: equities.polygon_raw already has %d row(s). "
        "Re-running this import would DROP the table and lose those "
        "rows. If you're sure (e.g. CV7 hasn't deployed yet, or this "
        "is a recovery), pass --force. Aborting.",
        n,
    )
    sys.exit(2)


# ─────────────────────────────────────────────────────────────────────
# Step 1 — PyIceberg recreates the empty equities target table
# ─────────────────────────────────────────────────────────────────────
def step1_recreate_target() -> None:
    cat = get_catalog()
    tid = equities_table_id(TARGET_TABLE)
    try:
        cat.drop_table(tid)
        log.info("Dropped existing %s", tid)
    except Exception:
        log.info("No existing %s to drop (ok)", tid)

    table = ensure_polygon_raw(cat)
    log.info("Created empty %s at %s", tid, table.location())


# ─────────────────────────────────────────────────────────────────────
# Step 2 — register raw flat-files as external Athena table
# ─────────────────────────────────────────────────────────────────────
def step2_register_external(athena: AthenaClient) -> None:
    # Drop if it exists so re-runs are idempotent.
    athena.run(f"DROP TABLE IF EXISTS {DB}.{EXTERNAL_TABLE}")

    # The source files are at
    #   raw/provider=polygon-flatfiles/kind=minute/year=YYYY/date=YYYY-MM-DD.parquet
    # Use Athena's partition projection so we don't need MSCK REPAIR.
    # Year is treated as part of the path; "date" is the actual filename.
    # Source schema (verified during v1 inspection — unchanged for v2):
    #   symbol large_string, timestamp ns-tz=UTC, open/high/low/close/volume/vwap double,
    #   trade_count int64, source large_string, __index_level_0__ int64 (ignored by projection).
    #
    # "timestamp" is a SQL keyword; quote with backticks.
    # NOTE: Athena DDL is Hive-flavored — `backticks` for reserved-word
    # identifiers. DML (SELECT/INSERT below) is Trino-flavored —
    # "double quotes" for the same.
    #
    # Actual S3 layout has `year=YYYY/` as a directory but
    # `date=YYYY-MM-DD.parquet` as a *file name*, not a sub-dir — so only
    # `year` is a real partition. Date info comes from the inner Parquet
    # `timestamp` column.
    ddl = f"""
        CREATE EXTERNAL TABLE {DB}.{EXTERNAL_TABLE} (
          symbol      string,
          `timestamp` timestamp,
          open        double,
          high        double,
          low         double,
          close       double,
          volume      double,
          vwap        double,
          trade_count bigint,
          source      string
        )
        PARTITIONED BY (year int)
        STORED AS PARQUET
        LOCATION '{RAW_LOC}'
        TBLPROPERTIES (
          'projection.enabled'='true',
          'projection.year.type'='integer',
          'projection.year.range'='2021,2030',
          'storage.location.template'='{RAW_LOC}year=${{year}}/'
        )
    """
    athena.run(ddl)
    log.info("Registered external table %s.%s", DB, EXTERNAL_TABLE)


# ─────────────────────────────────────────────────────────────────────
# Step 3 — INSERT INTO bronze SELECT … FROM external
# ─────────────────────────────────────────────────────────────────────
def step3_insert(athena: AthenaClient) -> None:
    # Project only the columns we want; cast types as needed.
    # `ingestion_ts` / `ingestion_run_id` set to NULL for imported rows
    # so the trailing nullable fields on the Iceberg schema are present
    # (Iceberg requires matching column count + order).
    #
    # NOTE: no ORDER BY here. A global sort across ~2B rows pushes us past
    # Athena's 30-min query timeout. Instead, we run OPTIMIZE … BIN_PACK
    # in step 3b — Iceberg's BIN_PACK respects the table's defined sort
    # order (symbol ASC, timestamp ASC, per CV1's POLYGON_RAW_SORT), so
    # the final compacted files are sorted per-file.
    #
    # Source has ~80k rows (0.0038% of 2.1B) with NULL symbol — unusable
    # garbage from Polygon ingestion. Drop them at the boundary; the
    # v2 polygon_raw identifier is (symbol, timestamp) and both are NOT
    # NULL in CV1's schema.
    sql = f"""
        INSERT INTO {DB}.{TARGET_TABLE}
        SELECT
            symbol,
            "timestamp",
            open,
            high,
            low,
            close,
            volume,
            CASE WHEN vwap = 0.0 THEN NULL ELSE vwap END        AS vwap,
            trade_count,
            source,
            CAST(NULL AS timestamp(6) with time zone)           AS ingestion_ts,
            CAST(NULL AS varchar)                               AS ingestion_run_id
        FROM {DB}.{EXTERNAL_TABLE}
        WHERE symbol IS NOT NULL
          AND "timestamp" IS NOT NULL
    """
    athena.run(sql)


# ─────────────────────────────────────────────────────────────────────
# Step 3b — compact + sort within each file
# ─────────────────────────────────────────────────────────────────────
def step3b_optimize(athena: AthenaClient) -> None:
    # OPTIMIZE rewrites files to the table's write.target-file-size-bytes
    # (128 MB for polygon_raw per CV1's _BASE_PROPERTIES) and, when a
    # sort order is set on the table, sorts rows within each written
    # file by that order. For equities.polygon_raw that's
    # (symbol ASC, timestamp ASC) per CV1's POLYGON_RAW_SORT.
    sql = f"OPTIMIZE {DB}.{TARGET_TABLE} REWRITE DATA USING BIN_PACK"
    athena.run(sql)


# ─────────────────────────────────────────────────────────────────────
# Step 4 — verify
# ─────────────────────────────────────────────────────────────────────
def step4_verify(athena: AthenaClient) -> dict:
    # Row counts — minus the NULL-symbol / NULL-timestamp garbage rows
    # that step 3's WHERE drops at the boundary. The expected parity
    # is therefore (external WHERE filter) == polygon_raw rows.
    src = athena.run(
        f'SELECT count(*) AS n FROM {DB}.{EXTERNAL_TABLE} '
        f'WHERE symbol IS NOT NULL AND "timestamp" IS NOT NULL',
        expect_rows=True,
    )
    src_n = int(src["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])

    dst = athena.run(
        f"SELECT count(*) AS n FROM {DB}.{TARGET_TABLE}",
        expect_rows=True,
    )
    dst_n = int(dst["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])

    log.info("source rows (raw external, post-filter): %s", f"{src_n:,}")
    log.info("equities.polygon_raw rows (Iceberg):    %s", f"{dst_n:,}")
    log.info("parity: %s", "OK match" if src_n == dst_n else "MISMATCH")

    # File count (data files only; Iceberg metadata files excluded).
    # Post-CV1 the v2 warehouse layout is
    #   {warehouse_prefix}/{equities_db}/{table_name}/data/...
    # so the prefix is e.g. iceberg/equities/polygon_raw/data/
    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    prefix = (
        f"{settings.iceberg_warehouse_prefix}/{DB}/{TARGET_TABLE}/data/"
    )
    file_count = 0
    total_bytes = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            if obj["Key"].endswith(".parquet"):
                file_count += 1
                total_bytes += obj["Size"]
    log.info("data files: %d  total: %s", file_count, _fmt_bytes(total_bytes))

    return {
        "source_rows": src_n,
        "polygon_raw_rows": dst_n,
        "parity": src_n == dst_n,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


# ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--force", action="store_true",
        help="Skip the preflight refusal when equities.polygon_raw "
             "already has rows. Use ONLY if you know the existing rows "
             "are about to be replaced by this import (recovery scenario).",
    )
    args = p.parse_args()

    log.info("DB=%s  target=%s  bucket=%s", DB, TARGET_TABLE, BUCKET)

    athena = AthenaClient()

    log.info("─── Step 0: preflight ───")
    step0_preflight(athena, force=args.force)

    log.info("─── Step 1: recreate empty equities.polygon_raw ───")
    step1_recreate_target()

    log.info("─── Step 2: register raw flat-files as external table ───")
    step2_register_external(athena)

    log.info("─── Step 3: INSERT INTO equities.polygon_raw SELECT … FROM external ───")
    step3_insert(athena)

    log.info("─── Step 3b: OPTIMIZE (BIN_PACK + sort-within-file) ───")
    step3b_optimize(athena)

    log.info("─── Step 4: verify ───")
    summary = step4_verify(athena)

    print()
    print("=== CV10' Athena import complete ===")
    for k, v in summary.items():
        print(f"  {k}: {v if not isinstance(v, int) or v < 10000 else f'{v:,}'}")


if __name__ == "__main__":
    main()
