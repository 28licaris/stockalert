#!/usr/bin/env python3
"""Parse the futures flat-file mirror → futures.polygon_raw (Iceberg).

Phase 2 of the futures lake build (see docs/futures_flatfile_mirror.md). Reads
the gzipped CSV minute-aggregate files from OUR mirror
(`s3://<lake>/polygon_flatfiles_mirror/{exchange}/minute_aggs_v1/...`) — NOT
Polygon — and appends per-CONTRACT outright bars (ESH4, CLM4, …) to
`futures.polygon_raw`. No roll, no adjustment: this is the queryable raw layer,
analog of `equities.polygon_raw`. The continuous-root layer is derived from it.

Reading from the mirror (not Polygon) means this is subscription-independent —
it works after the entitlement lapses.

Design:
  * Outright contracts only (contract_root() rejects spreads/strips); the raw
    .csv.gz mirror keeps everything as the faithful archive.
  * Parse is parallel (download + decompress + pandas, I/O+CPU bound); appends
    are serialized on the main thread (Iceberg single-writer) in batches.
  * No silent failures: per-file row accounting, running totals, and a final
    reconcile of rows-written vs table row-count delta. Any parse/append error
    → logged + non-zero exit.

NOTE: append-only (bronze pattern). A clean re-load means dropping the table
first; otherwise re-running double-writes physically (dedup is on read via the
(contract, timestamp) identifier).

Usage:
    # Validate one exchange-month first:
    poetry run python scripts/polygon_futures_parse_raw.py \\
        --exchanges us_futures_comex --months 2024-03

    # Full parse (all exchanges, all mirrored history):
    poetry run python scripts/polygon_futures_parse_raw.py
"""
from __future__ import annotations

import argparse
import gzip
import io
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Revert the botocore/AWS-SDK default (>=1.36) that adds CRC64NVME checksums to
# uploads — it triggers `BadDigest` on multipart parquet PUTs via PyIceberg's
# pyarrow S3 writer. Must be set before any AWS client initializes. Safe in all
# environments; only disables the *automatic* extra checksum.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon_futures_parse_raw")

ALL_EXCHANGES = [
    "us_futures_cme", "us_futures_cbot", "us_futures_comex", "us_futures_nymex",
]
DEFAULT_MIRROR_PREFIX = "polygon_flatfiles_mirror"


def _make_client(settings):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", region_name=settings.stock_lake_region,
        config=Config(retries={"max_attempts": 8, "mode": "adaptive"},
                      max_pool_connections=64),
    )


def list_mirror_minute_keys(s3, bucket, prefix, exchange, months) -> list[str]:
    """List mirrored minute_aggs .csv.gz keys for one exchange, optional
    month filter (set of 'YYYY-MM')."""
    base = f"{prefix}/{exchange}/minute_aggs_v1/"
    keys: list[str] = []
    pg = s3.get_paginator("list_objects_v2")
    for page in pg.paginate(Bucket=bucket, Prefix=base):
        for o in page.get("Contents", []):
            k = o["Key"]
            if not k.endswith(".csv.gz"):
                continue
            if months:
                # key .../YYYY/MM/YYYY-MM-DD.csv.gz
                stem = k.rsplit("/", 1)[-1][: -len(".csv.gz")]  # YYYY-MM-DD
                if stem[:7] not in months:
                    continue
            keys.append(k)
    return keys


def parse_mirror_file(s3, bucket, key, exchange) -> pd.DataFrame:
    """Download + parse one mirrored minute file → outright-contract rows.

    Returns a DataFrame with columns the sink expects, or empty if the file is
    empty (holiday) or has no outright contracts. Raises on any read/parse
    error (caller records it; never swallowed). `exchange` is the flat-file
    prefix (e.g. 'us_futures_comex') — the CSV's own `exchange` column is an
    unhelpful numeric venue code, so we overwrite it with the prefix."""
    from app.providers.polygon_flatfiles import _FUTURES_CSV_DTYPES
    from app.services.futures.schemas import contract_root

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
        df = pd.read_csv(gz, dtype=_FUTURES_CSV_DTYPES)
    if df.empty:
        return df

    df["contract"] = df["ticker"].astype("string").str.upper()
    df["root"] = df["contract"].map(contract_root)
    df = df[df["root"].notna()].copy()          # outright contracts only
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["window_start"], unit="ns", utc=True)
    vol, dvol = df["volume"], df["dollar_volume"]
    ok = vol.notna() & (vol > 0) & dvol.notna()
    df["vwap"] = dvol.where(ok) / vol.where(ok)
    df["trade_count"] = df["transactions"]
    df["exchange"] = exchange               # the prefix, not the CSV venue code
    return df[[
        "contract", "timestamp", "open", "high", "low", "close", "volume",
        "vwap", "trade_count", "dollar_volume", "root", "exchange",
    ]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exchanges", nargs="+", default=ALL_EXCHANGES, choices=ALL_EXCHANGES)
    ap.add_argument("--months", nargs="*", default=None,
                    help="optional 'YYYY-MM' filters (default: all mirrored)")
    ap.add_argument("--workers", type=int, default=16, help="parallel parse workers")
    ap.add_argument("--batch-rows", type=int, default=2_000_000,
                    help="append when accumulated rows reach this")
    ap.add_argument("--mirror-prefix", default=DEFAULT_MIRROR_PREFIX)
    ap.add_argument("--dry-run", action="store_true", help="parse + count, no write")
    args = ap.parse_args()

    from app.config import settings
    from app.services.futures.polygon_raw_sink import PolygonRawFuturesSink
    from app.services.futures.tables import ensure_polygon_raw
    from app.services.iceberg_catalog import get_catalog

    s3 = _make_client(settings)
    bucket = settings.stock_lake_bucket
    months = set(args.months) if args.months else None

    logger.info("=== Parse mirror → futures.polygon_raw %s===",
                "| DRY RUN " if args.dry_run else "")
    logger.info("  mirror : s3://%s/%s/{exchange}/minute_aggs_v1/", bucket, args.mirror_prefix)
    logger.info("  exchanges: %s", " ".join(args.exchanges))
    logger.info("  months   : %s", " ".join(sorted(months)) if months else "ALL")

    table = ensure_polygon_raw(get_catalog())
    rows_before = _row_count(table)
    logger.info("  futures.polygon_raw rows before: %s", f"{rows_before:,}")

    sink = None if args.dry_run else PolygonRawFuturesSink()

    t0 = time.time()
    total_files = total_rows = total_written = 0
    failures: list[tuple[str, str]] = []

    for exchange in args.exchanges:
        keys = list_mirror_minute_keys(s3, bucket, args.mirror_prefix, exchange, months)
        logger.info("[%s] %d mirrored minute files to parse", exchange, len(keys))
        if not keys:
            logger.warning("[%s] NO mirrored files — nothing to parse", exchange)
            continue

        batch: list[pd.DataFrame] = []
        batch_rows = 0
        ex_files = ex_rows = ex_written = 0

        def flush():
            nonlocal batch, batch_rows, ex_written, total_written
            if not batch:
                return
            df = pd.concat(batch, ignore_index=True)
            if not args.dry_run:
                ex_written += sink.write_frame(df)
                total_written += len(df)
            batch = []
            batch_rows = 0

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(parse_mirror_file, s3, bucket, k, exchange): k for k in keys}
            for fut in as_completed(futs):
                k = futs[fut]
                try:
                    df = fut.result()
                except Exception as exc:
                    failures.append((k, str(exc)))
                    logger.error("[%s] FAILED parse %s: %s", exchange, k, exc)
                    continue
                ex_files += 1
                if len(df):
                    ex_rows += len(df)
                    batch.append(df)
                    batch_rows += len(df)
                    if batch_rows >= args.batch_rows:
                        flush()
                if ex_files % 250 == 0:
                    logger.info("[%s] parsed %d/%d files, %s rows (%.0fs, %d failed)",
                                exchange, ex_files, len(keys), f"{ex_rows:,}",
                                time.time() - t0, len(failures))
        flush()
        logger.info("[%s] done: files=%d outright_rows=%s written=%s",
                    exchange, ex_files, f"{ex_rows:,}", f"{ex_written:,}")
        total_files += ex_files
        total_rows += ex_rows

    logger.info("")
    logger.info("=== SUMMARY (%.0fs) ===", time.time() - t0)
    logger.info("  files parsed   : %d", total_files)
    logger.info("  outright rows  : %s", f"{total_rows:,}")
    logger.info("  rows written   : %s", f"{total_written:,}")
    logger.info("  parse failures : %d", len(failures))

    if args.dry_run:
        logger.info("DRY RUN — no rows written.")
        return 1 if failures else 0

    table.refresh()
    rows_after = _row_count(table)
    delta = rows_after - rows_before
    logger.info("  table rows: before=%s after=%s delta=%s (expected +%s)",
                f"{rows_before:,}", f"{rows_after:,}", f"{delta:,}", f"{total_written:,}")

    ok = (not failures) and (delta == total_written)
    if not ok:
        logger.error("PARSE INCOMPLETE — failures=%d, row delta %s != written %s",
                     len(failures), f"{delta:,}", f"{total_written:,}")
        return 1
    logger.info("PARSE COMPLETE — %s outright-contract rows in futures.polygon_raw.",
                f"{total_written:,}")
    return 0


def _row_count(table) -> int:
    try:
        snap = table.current_snapshot()
        return int(snap.summary.additional_properties.get("total-records", 0)) if snap else 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
