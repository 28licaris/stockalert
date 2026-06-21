#!/usr/bin/env python3
"""Byte-for-byte mirror of Polygon futures flat files into the lake S3 bucket.

Phase 1 of the futures raw-data capture (see docs/futures_flatfile_mirror.md).
This copies Polygon's gzipped CSV flat files **verbatim** — no parsing, no REST
discovery, no roll logic, no Iceberg — from Polygon's S3 (files.massive.com)
into our lake bucket under ``polygon_flatfiles_mirror/``.

Why a raw mirror first: the subscription expires soon, the data is only ~131 GB
(minute + session + trades), and a pure object copy is immune to the
roll/discovery/exchange-mapping bugs that corrupted the continuous-root
backfill.  Continuous roots and the ``futures.polygon_raw`` Iceberg table are
derived **later**, from this durable mirror (Phase 2), so the subscription can
lapse safely once the bytes are ours.

Layout (Polygon's key structure preserved exactly):
  src : s3://flatfiles/{exchange}/{dataset}/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz
  dst : s3://<lake>/polygon_flatfiles_mirror/{exchange}/{dataset}/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz

No silent failures (the failure mode of the previous backfill):
  * Preflight: enumerate the full source manifest (key -> size) up front.
  * Idempotent: skip a dest object only when it already exists with the exact
    same byte size; otherwise (re)copy.
  * Per-file: verify the dest object size equals the source size after PUT.
  * Post-run: re-list the destination and reconcile key set + byte totals
    against the source manifest, per (exchange, dataset).  ANY missing key,
    size mismatch, or transfer error -> loud log + non-zero exit.  Zero-copy
    (everything already mirrored) is reported explicitly, not hidden.

Usage:
    # Validate on one small slice first (recommended before the full run):
    poetry run python scripts/polygon_futures_mirror.py \\
        --exchanges us_futures_comex --datasets minute_aggs_v1 \\
        --start-year 2024 --end-year 2024

    # Full mirror (all exchanges, minute + session + trades, full history):
    poetry run python scripts/polygon_futures_mirror.py \\
        --datasets minute_aggs_v1 session_aggs_v1 trades_v1 \\
        --start-year 2017 --end-year 2026

    # Dry run: enumerate + report what WOULD copy, transfer nothing.
    poetry run python scripts/polygon_futures_mirror.py --dry-run ...
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon_futures_mirror")

ALL_EXCHANGES = [
    "us_futures_cme",
    "us_futures_cbot",
    "us_futures_comex",
    "us_futures_nymex",
]
ALL_DATASETS = ["minute_aggs_v1", "session_aggs_v1", "trades_v1"]
DEFAULT_DEST_PREFIX = "polygon_flatfiles_mirror"


@dataclass
class GroupResult:
    """Outcome for one (exchange, dataset) group."""

    exchange: str
    dataset: str
    source_files: int = 0
    source_bytes: int = 0
    copied: int = 0
    copied_bytes: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)
    # Filled during post-run reconciliation.
    missing_in_dest: list[str] = field(default_factory=list)
    size_mismatch: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.failures
            and not self.missing_in_dest
            and not self.size_mismatch
        )


def _make_clients(settings):
    import boto3
    from botocore.config import Config

    cfg = Config(
        retries={"max_attempts": 8, "mode": "adaptive"},
        max_pool_connections=64,
    )
    src = boto3.client(
        "s3",
        endpoint_url=settings.polygon_s3_endpoint,
        aws_access_key_id=settings.polygon_s3_access_key_id,
        aws_secret_access_key=settings.polygon_s3_secret_access_key,
        region_name="us-east-1",
        config=cfg,
    )
    dst = boto3.client("s3", region_name=settings.stock_lake_region, config=cfg)
    return src, dst


def _year_of(key: str) -> int | None:
    """Extract YYYY from .../{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz; None if unparseable."""
    parts = key.split("/")
    for p in parts:
        if len(p) == 4 and p.isdigit():
            return int(p)
    return None


def list_source(src, bucket, exchange, dataset, start_year, end_year) -> dict[str, int]:
    """Return {key: size} for one (exchange, dataset) within the year range."""
    prefix = f"{exchange}/{dataset}/"
    manifest: dict[str, int] = {}
    paginator = src.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            y = _year_of(o["Key"])
            if y is None or y < start_year or y > end_year:
                continue
            manifest[o["Key"]] = o["Size"]
    return manifest


def list_dest(dst, bucket, dest_prefix, exchange, dataset) -> dict[str, int]:
    """Return {source_key: size} for objects already mirrored (dest prefix stripped)."""
    prefix = f"{dest_prefix}/{exchange}/{dataset}/"
    out: dict[str, int] = {}
    paginator = dst.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            src_key = o["Key"][len(dest_prefix) + 1 :]  # strip "prefix/"
            out[src_key] = o["Size"]
    return out


def copy_one(src, dst, src_bucket, dst_bucket, dest_prefix, key, expected_size) -> int:
    """Stream one object src -> dst and verify size. Returns bytes copied.

    Raises on any failure so the caller records it; never swallows errors.
    """
    from boto3.s3.transfer import TransferConfig

    dest_key = f"{dest_prefix}/{key}"
    obj = src.get_object(Bucket=src_bucket, Key=key)
    body = obj["Body"]
    # Sequential multipart works with a non-seekable streaming body.
    tcfg = TransferConfig(multipart_threshold=16 * 1024 * 1024,
                          multipart_chunksize=16 * 1024 * 1024,
                          use_threads=False)
    dst.upload_fileobj(body, dst_bucket, dest_key, Config=tcfg)
    head = dst.head_object(Bucket=dst_bucket, Key=dest_key)
    got = head["ContentLength"]
    if got != expected_size:
        raise ValueError(
            f"size mismatch after PUT: expected {expected_size}, got {got}"
        )
    return got


def mirror_group(
    src, dst, settings, dest_prefix, exchange, dataset,
    start_year, end_year, workers, dry_run,
) -> GroupResult:
    res = GroupResult(exchange=exchange, dataset=dataset)
    src_bucket = settings.polygon_s3_bucket
    dst_bucket = settings.stock_lake_bucket

    logger.info("[%s/%s] listing source…", exchange, dataset)
    source = list_source(src, src_bucket, exchange, dataset, start_year, end_year)
    res.source_files = len(source)
    res.source_bytes = sum(source.values())
    logger.info(
        "[%s/%s] source: %d files, %.3f GB (%d–%d)",
        exchange, dataset, res.source_files, res.source_bytes / 1e9,
        start_year, end_year,
    )
    if res.source_files == 0:
        logger.warning("[%s/%s] NO source files in range — nothing to mirror",
                       exchange, dataset)
        return res

    existing = list_dest(dst, dst_bucket, dest_prefix, exchange, dataset)
    todo = [
        (k, sz) for k, sz in source.items()
        if existing.get(k) != sz  # copy if absent OR size differs
    ]
    res.skipped = res.source_files - len(todo)
    logger.info(
        "[%s/%s] %d already mirrored (matched size), %d to copy",
        exchange, dataset, res.skipped, len(todo),
    )

    if dry_run:
        logger.info("[%s/%s] DRY RUN — would copy %d files (%.3f GB)",
                    exchange, dataset, len(todo),
                    sum(sz for _, sz in todo) / 1e9)
        res.copied = len(todo)  # for dry-run reporting only
        return res

    if not todo:
        logger.info("[%s/%s] up to date — 0 copied", exchange, dataset)
        return res

    lock = threading.Lock()
    t0 = time.time()
    done = 0

    def work(item):
        key, sz = item
        n = copy_one(src, dst, src_bucket, dst_bucket, dest_prefix, key, sz)
        return key, n

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, it): it for it in todo}
        for fut in as_completed(futs):
            key, sz = futs[fut]
            try:
                _, n = fut.result()
                with lock:
                    res.copied += 1
                    res.copied_bytes += n
                    done += 1
            except Exception as exc:  # record, never swallow
                with lock:
                    res.failures.append((key, str(exc)))
                    done += 1
                logger.error("[%s/%s] FAILED %s: %s", exchange, dataset, key, exc)
            if done % 250 == 0:
                el = time.time() - t0
                logger.info(
                    "[%s/%s] %d/%d copied (%.2f GB, %.0fs, %d failed)",
                    exchange, dataset, res.copied, len(todo),
                    res.copied_bytes / 1e9, el, len(res.failures),
                )

    logger.info(
        "[%s/%s] copy phase done: copied=%d skipped=%d failed=%d (%.2f GB, %.0fs)",
        exchange, dataset, res.copied, res.skipped, len(res.failures),
        res.copied_bytes / 1e9, time.time() - t0,
    )
    return res


def reconcile_group(src, dst, settings, dest_prefix, res: GroupResult,
                    start_year, end_year) -> None:
    src_bucket = settings.polygon_s3_bucket
    dst_bucket = settings.stock_lake_bucket
    source = list_source(src, src_bucket, res.exchange, res.dataset,
                         start_year, end_year)
    dest = list_dest(dst, dst_bucket, dest_prefix, res.exchange, res.dataset)
    for k, sz in source.items():
        if k not in dest:
            res.missing_in_dest.append(k)
        elif dest[k] != sz:
            res.size_mismatch.append(k)
    logger.info(
        "[%s/%s] RECONCILE: source=%d dest=%d missing=%d size_mismatch=%d -> %s",
        res.exchange, res.dataset, len(source), len(dest),
        len(res.missing_in_dest), len(res.size_mismatch),
        "OK" if res.ok else "FAIL",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exchanges", nargs="+", default=ALL_EXCHANGES,
                    choices=ALL_EXCHANGES)
    ap.add_argument("--datasets", nargs="+", default=ALL_DATASETS,
                    choices=ALL_DATASETS)
    ap.add_argument("--start-year", type=int, default=2017)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--dest-prefix", default=DEFAULT_DEST_PREFIX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.config import settings

    src, dst = _make_clients(settings)

    logger.info(
        "=== Polygon futures flat-file mirror %d–%d %s===",
        args.start_year, args.end_year, "| DRY RUN " if args.dry_run else "",
    )
    logger.info("  source : s3://%s/{exchange}/{dataset}/", settings.polygon_s3_bucket)
    logger.info("  dest   : s3://%s/%s/", settings.stock_lake_bucket, args.dest_prefix)
    logger.info("  exchanges: %s", " ".join(args.exchanges))
    logger.info("  datasets : %s", " ".join(args.datasets))

    results: list[GroupResult] = []
    t0 = time.time()
    for dataset in args.datasets:
        for exchange in args.exchanges:
            res = mirror_group(
                src, dst, settings, args.dest_prefix, exchange, dataset,
                args.start_year, args.end_year, args.workers, args.dry_run,
            )
            if not args.dry_run:
                reconcile_group(src, dst, settings, args.dest_prefix, res,
                                args.start_year, args.end_year)
            results.append(res)

    # ---- Summary + manifest ----
    total_src = sum(r.source_files for r in results)
    total_copied = sum(r.copied for r in results)
    total_skipped = sum(r.skipped for r in results)
    total_failed = sum(len(r.failures) for r in results)
    total_missing = sum(len(r.missing_in_dest) for r in results)
    total_mismatch = sum(len(r.size_mismatch) for r in results)

    logger.info("")
    logger.info("=== SUMMARY (%.0fs) ===", time.time() - t0)
    for r in results:
        logger.info(
            "  %-18s %-16s src=%-6d copied=%-6d skipped=%-6d failed=%-3d "
            "missing=%-3d mismatch=%-3d %s",
            r.exchange, r.dataset, r.source_files, r.copied, r.skipped,
            len(r.failures), len(r.missing_in_dest), len(r.size_mismatch),
            "" if (args.dry_run or r.ok) else "<<< FAIL",
        )
    logger.info(
        "  TOTAL src=%d copied=%d skipped=%d failed=%d missing=%d mismatch=%d",
        total_src, total_copied, total_skipped, total_failed,
        total_missing, total_mismatch,
    )

    if args.dry_run:
        logger.info("DRY RUN complete — no objects written.")
        return 0

    # Write a manifest record next to the mirror.
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_year": args.start_year,
        "end_year": args.end_year,
        "groups": [
            {
                "exchange": r.exchange,
                "dataset": r.dataset,
                "source_files": r.source_files,
                "source_bytes": r.source_bytes,
                "copied": r.copied,
                "skipped": r.skipped,
                "failed": len(r.failures),
                "missing_in_dest": len(r.missing_in_dest),
                "size_mismatch": len(r.size_mismatch),
                "ok": r.ok,
            }
            for r in results
        ],
    }
    try:
        key = f"{args.dest_prefix}/_manifest.json"
        dst.put_object(
            Bucket=settings.stock_lake_bucket, Key=key,
            Body=json.dumps(manifest, indent=2).encode(),
            ContentType="application/json",
        )
        logger.info("Wrote manifest: s3://%s/%s", settings.stock_lake_bucket, key)
    except Exception as exc:
        logger.error("Failed to write manifest (non-fatal): %s", exc)

    if total_failed or total_missing or total_mismatch:
        logger.error(
            "MIRROR INCOMPLETE — failed=%d missing=%d mismatch=%d. "
            "Re-run to resume (idempotent).",
            total_failed, total_missing, total_mismatch,
        )
        return 1

    logger.info("MIRROR COMPLETE — all %d source files present in dest, sizes match.",
                total_src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
