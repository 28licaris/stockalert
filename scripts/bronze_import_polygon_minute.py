"""
Phase 1 — one-time import of the existing 5-year Polygon flat-file
Parquets into `bronze.polygon_minute`.

What it does:
  1. List every Parquet under `s3://${STOCK_LAKE_BUCKET}/raw/provider=
     polygon-flatfiles/kind=minute/year=*/date=*.parquet`.
  2. For each, read with PyArrow, project away `__index_level_0__`,
     cast `timestamp` ns → us (Iceberg precision), write to a staging
     prefix under the Iceberg warehouse:
       s3://${bucket}/iceberg/bronze/polygon_minute/_staging/
         year=YYYY/date=YYYY-MM-DD.parquet
  3. After all are staged, batch-`add_files` them into the Iceberg
     table. `add_files` doesn't move data — staged files stay where
     they are and are tracked by Iceberg metadata. Later compaction
     rewrites them into proper monthly layout; orphan cleanup removes
     the stages.

Idempotent:
  - Re-running skips files already staged.
  - `add_files` is called only on files not already tracked by the table.

Run:
    poetry run python scripts/bronze_import_polygon_minute.py
        [--limit N]              process only first N files (smoke test)
        [--workers 8]            parallel rewrites
        [--dry-run]              list work, don't write
        [--skip-add-files]       rewrite to staging only; don't register
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from app.services.bronze import (  # noqa: E402
    BRONZE_POLYGON_MINUTE_SCHEMA,
    bronze_table_id,
    ensure_bronze_polygon_minute,
)
from app.services.iceberg_catalog import get_catalog  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bronze-import")

SRC_PREFIX = "raw/provider=polygon-flatfiles/kind=minute/"
STAGE_PREFIX = f"{settings.iceberg_warehouse_prefix}/bronze/polygon_minute/_staging/"


@dataclass(frozen=True)
class SourceFile:
    key: str
    year: str
    date: str  # YYYY-MM-DD

    @property
    def stage_key(self) -> str:
        return f"{STAGE_PREFIX}year={self.year}/date={self.date}.parquet"

    @property
    def s3_source_uri(self) -> str:
        return f"s3://{settings.stock_lake_bucket}/{self.key}"

    @property
    def s3_stage_uri(self) -> str:
        return f"s3://{settings.stock_lake_bucket}/{self.stage_key}"


def _s3():
    return boto3.client("s3", region_name=settings.stock_lake_region)


def list_source_files() -> list[SourceFile]:
    s3 = _s3()
    paginator = s3.get_paginator("list_objects_v2")
    files: list[SourceFile] = []
    for page in paginator.paginate(Bucket=settings.stock_lake_bucket, Prefix=SRC_PREFIX):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            # raw/provider=polygon-flatfiles/kind=minute/year=2021/date=2021-01-04.parquet
            try:
                year = key.split("year=", 1)[1].split("/", 1)[0]
                date = key.split("date=", 1)[1].split(".", 1)[0]
            except IndexError:
                continue
            files.append(SourceFile(key=key, year=year, date=date))
    files.sort(key=lambda f: f.date)
    return files


def list_already_staged() -> set[str]:
    s3 = _s3()
    out: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.stock_lake_bucket, Prefix=STAGE_PREFIX):
        for obj in page.get("Contents", []) or []:
            out.add(obj["Key"])
    return out


# ── Schema mapping (Arrow ←→ Iceberg) ────────────────────────────────
# Iceberg `timestamptz` is microsecond precision; the source files use ns.
# Iceberg `string` maps to Arrow `string` (NOT `large_string`).
_TARGET_ARROW_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("volume", pa.float64(), nullable=True),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("source", pa.string(), nullable=True),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)


def _conform(table: pa.Table) -> pa.Table:
    """Project + cast a source Parquet into the target Iceberg-compat schema."""
    cols = {f.name: table.column(f.name) for f in table.schema if f.name in {f2.name for f2 in _TARGET_ARROW_SCHEMA}}
    # Add missing columns as null arrays of the right type.
    out_arrays = []
    for f in _TARGET_ARROW_SCHEMA:
        if f.name in cols:
            col = cols[f.name]
            # Pull a single chunk to cast cleanly (sources have 1–2 chunks).
            arr = col.combine_chunks() if col.num_chunks > 1 else col.chunk(0)
            arr = arr.cast(f.type, safe=False)
        else:
            arr = pa.nulls(table.num_rows, type=f.type)
        out_arrays.append(arr)
    return pa.Table.from_arrays(out_arrays, schema=_TARGET_ARROW_SCHEMA)


def _rewrite_one(src: SourceFile, dry_run: bool = False) -> tuple[str, int, str]:
    """Download, conform, upload. Returns (date, row_count, status)."""
    s3 = _s3()

    if dry_run:
        return (src.date, 0, "dry-run")

    # Download to /tmp (smaller files easier than streaming to memory and back)
    local_in = f"/tmp/bronze_import_in_{src.date}.parquet"
    local_out = f"/tmp/bronze_import_out_{src.date}.parquet"
    try:
        s3.download_file(settings.stock_lake_bucket, src.key, local_in)
        table = pq.read_table(local_in)
        conformed = _conform(table)
        pq.write_table(
            conformed,
            local_out,
            compression="snappy",
            use_dictionary=True,
        )
        s3.upload_file(local_out, settings.stock_lake_bucket, src.stage_key)
        return (src.date, table.num_rows, "ok")
    finally:
        for p in (local_in, local_out):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def rewrite_all(files: Iterable[SourceFile], workers: int, dry_run: bool) -> int:
    total_rows = 0
    completed = 0
    files = list(files)
    log.info("Rewriting %d files with %d workers", len(files), workers)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_rewrite_one, f, dry_run): f for f in files}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                date, rows, status = fut.result()
                total_rows += rows
                completed += 1
                if completed % 10 == 0 or completed == len(files):
                    elapsed = time.monotonic() - started
                    rate = completed / elapsed if elapsed > 0 else 0
                    log.info(
                        "  %4d/%d (%.1f files/s)  last=%s rows=%s status=%s",
                        completed, len(files), rate, date, f"{rows:,}", status,
                    )
            except Exception as exc:  # noqa: BLE001
                log.error("FAILED %s: %s", src.date, exc)
                raise
    return total_rows


def add_files_to_bronze(staged: Iterable[SourceFile]) -> int:
    catalog = get_catalog()
    table = ensure_bronze_polygon_minute(catalog)

    staged_uris = [f.s3_stage_uri for f in staged]
    log.info("Adding %d files to %s via Iceberg add_files...", len(staged_uris), bronze_table_id("polygon_minute"))

    # add_files takes file paths; PyIceberg ≥ 0.7 supports a list of URIs.
    table.add_files(file_paths=staged_uris)

    # Re-load to pick up new metadata + count rows.
    table = catalog.load_table(bronze_table_id("polygon_minute"))
    row_count = table.scan().to_arrow().num_rows
    return row_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-add-files", action="store_true")
    args = ap.parse_args()

    log.info("Listing source files under s3://%s/%s", settings.stock_lake_bucket, SRC_PREFIX)
    sources = list_source_files()
    log.info("Found %d source files (%s … %s)", len(sources), sources[0].date, sources[-1].date)

    log.info("Listing already-staged files under s3://%s/%s", settings.stock_lake_bucket, STAGE_PREFIX)
    already = list_already_staged()
    log.info("Already staged: %d", len(already))

    todo = [s for s in sources if s.stage_key not in already]
    log.info("Files needing rewrite: %d", len(todo))

    if args.limit:
        todo = todo[: args.limit]
        log.info("Limiting to first %d", len(todo))

    if todo:
        total_rows = rewrite_all(todo, workers=args.workers, dry_run=args.dry_run)
        log.info("Rewrite complete; ~%s rows processed", f"{total_rows:,}")
    else:
        log.info("Nothing to rewrite")

    if args.dry_run or args.skip_add_files:
        log.info("Skipping add_files (dry_run=%s, skip=%s)", args.dry_run, args.skip_add_files)
        return

    # Compute the list of staged files to register; skip ones that may
    # have been registered by a prior partial run by listing what's in S3.
    final_staged = list_already_staged() if not args.limit else {s.stage_key for s in (todo if not args.dry_run else [])}
    if args.limit:
        sources_to_add = [s for s in todo if s.stage_key in final_staged]
    else:
        sources_to_add = [s for s in sources if s.stage_key in final_staged]
    log.info("Registering %d staged files into Iceberg", len(sources_to_add))
    row_count = add_files_to_bronze(sources_to_add)
    log.info("Table row count after add_files: %s", f"{row_count:,}")


if __name__ == "__main__":
    main()
