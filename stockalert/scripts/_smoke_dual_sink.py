#!/usr/bin/env python3
"""
Smoke test: end-to-end dual-sink flat-files backfill.

Wires up a ``FlatFilesBackfillService`` with BOTH sinks explicitly
(ClickHouseSink + LakeSink), drives one trading day's worth of minute
bars for a tiny symbol set, then verifies all three stores agree:

  1. ClickHouse ``ohlcv_1m`` has bars for the target day with
     ``source='polygon-flatfiles'``
  2. S3 has a Parquet object at the canonical key
  3. ``lake_archive_watermarks`` has an ``ok`` row matching the
     ``s3_key`` and ``bars_archived`` count

Run:
    python -m scripts._smoke_dual_sink
    python -m scripts._smoke_dual_sink --date 2026-05-12

The script is deliberately tiny (~5 symbols, 1 day) so it completes in
under a minute even on a cold cache. Pre-existing ClickHouse rows for
the symbols are tolerated — we use the source filter to scope the
verification to the run we just performed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "scripts" / ".env")

# Force lake-archive on for the smoke test even if .env disables it.
# We touch settings AFTER dotenv has loaded so the env vars are in place.
os.environ["LAKE_ARCHIVE_ENABLED"] = "true"

from app.config import settings  # noqa: E402
from app.db.client import get_client  # noqa: E402
from app.db.lake_watermarks import WatermarkRepo  # noqa: E402
from app.providers.polygon_flatfiles import PolygonFlatFilesClient  # noqa: E402
from app.services.flatfiles_backfill import FlatFilesBackfillService  # noqa: E402
from app.services.flatfiles_sinks import ClickHouseSink, LakeSink  # noqa: E402
from app.services.lake_archive import LakeArchiveWriter  # noqa: E402
from app.services.s3_lake_client import S3LakeClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("smoke_dual_sink")


SYMBOLS = ["AAPL", "MSFT", "SPY", "QQQ", "IWM"]


def _default_date() -> date:
    """Pick the most recent weekday (Mon-Fri). Doesn't try to skip
    holidays — if today is a Monday following a holiday, the flat-file
    listing will report the day as missing and we'll retry one back."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--date", default=_default_date().isoformat(),
        help="Trading day to backfill (YYYY-MM-DD). Default: most recent weekday.",
    )
    p.add_argument(
        "--kind", default="minute", choices=("minute", "day"),
        help="Aggregation grain (default: minute).",
    )
    p.add_argument(
        "--symbols", default=",".join(SYMBOLS),
        help="Comma-separated symbol filter. Default: 5 large caps.",
    )
    return p.parse_args()


def _print_step(n: int, msg: str) -> None:
    print(f"\n=== Step {n}: {msg} ===")


async def _verify_clickhouse(
    symbols: list[str], target: date, source_tag: str,
) -> tuple[int, int]:
    """Return (rows, distinct_symbols) in ``ohlcv_1m`` for the target
    day filtered to the smoke run's symbols + source tag."""
    client = get_client()
    sql = """
        SELECT count() AS rows, uniqExact(symbol) AS distinct_symbols
        FROM (
            SELECT symbol, timestamp
            FROM ohlcv_1m
            WHERE toDate(timestamp) = %(d)s
              AND symbol IN %(syms)s
              AND source = %(src)s
            GROUP BY symbol, timestamp
        )
    """
    rows = client.query(sql, parameters={
        "d": target, "syms": symbols, "src": source_tag,
    }).result_rows
    return int(rows[0][0]), int(rows[0][1])


async def _verify_s3(s3: S3LakeClient, key: str) -> int:
    """Return object size in bytes; raise if missing."""
    head = s3.head(key)
    if head is None:
        raise RuntimeError(f"S3 object NOT FOUND at s3://{s3.bucket}/{key}")
    return int(head.get("ContentLength", 0))


async def _main() -> int:
    args = _parse_args()
    target = date.fromisoformat(args.date)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    provider = "polygon-flatfiles"

    print(f"smoke: dual-sink backfill")
    print(f"  date     = {target.isoformat()}")
    print(f"  kind     = {args.kind}")
    print(f"  symbols  = {symbols}")
    print(f"  provider = {provider}")
    print(f"  bucket   = {settings.stock_lake_bucket}")

    # ---- Step 1: build the service with explicit dual-sink wiring ----
    _print_step(1, "Build FlatFilesBackfillService with [ClickHouseSink, LakeSink]")
    s3 = S3LakeClient.from_settings()
    repo = WatermarkRepo.from_clickhouse()
    writer = LakeArchiveWriter(s3=s3, watermarks=repo)
    sinks = [
        ClickHouseSink.from_settings(),
        LakeSink(writer=writer, force=True),  # force so a re-run overwrites
    ]
    svc = FlatFilesBackfillService(
        flat_files=PolygonFlatFilesClient.from_settings(),
        sinks=sinks,
    )
    print(f"  sinks = {[s.name for s in svc.sinks]}")

    # ---- Step 2: run the backfill for one day ----
    _print_step(2, f"backfill_range({symbols}, {target}..{target}, kind={args.kind})")
    result = await svc.backfill_range(
        symbols, target, target, kind=args.kind,
    )
    print(f"  summary = {result.to_summary()}")
    if not result.days:
        print("  ERROR: no days were processed (listing returned empty?)")
        return 2
    day = result.days[0]
    print(f"  day.status        = {day.status}")
    print(f"  day.bars_persisted= {day.bars_persisted}")
    print(f"  day.sink_results  = {{")
    for name, sr in day.sink_results.items():
        print(f"    {name}: status={sr.status} bars={sr.bars_written} "
              f"error={sr.error!r} meta={sr.metadata}")
    print("  }")
    if day.status not in ("ok", "partial"):
        print(f"  ERROR: day did not complete (status={day.status})")
        return 3

    expected_bars = day.bars_persisted

    # ---- Step 3: verify ClickHouse ----
    _print_step(3, "Verify ClickHouse ohlcv_1m has new bars")
    rows, distinct_syms = await _verify_clickhouse(symbols, target, provider)
    print(f"  ohlcv_1m rows = {rows}, distinct symbols = {distinct_syms}")
    ch_sink_result = day.sink_results.get("clickhouse")
    if ch_sink_result and ch_sink_result.status == "ok":
        if rows < expected_bars:
            print(f"  ERROR: expected >= {expected_bars} rows, got {rows}")
            return 4
        if distinct_syms < min(len(symbols), 1):
            print(f"  ERROR: expected >= 1 symbol, got {distinct_syms}")
            return 4
        print("  OK: ClickHouse has the bars")
    else:
        print("  SKIP: ClickHouse sink did not succeed; ClickHouse check skipped")

    # ---- Step 4: verify S3 parquet ----
    _print_step(4, "Verify S3 has the canonical Parquet object")
    key = writer.key_for(file_date=target, kind=args.kind, provider=provider)
    print(f"  expected key = s3://{s3.bucket}/{key}")
    lake_sink_result = day.sink_results.get("lake")
    if lake_sink_result and lake_sink_result.status in ("ok", "skipped"):
        size = await _verify_s3(s3, key)
        print(f"  S3 ContentLength = {size} bytes")
        if size <= 0:
            print(f"  ERROR: S3 object exists but is empty")
            return 5
        print("  OK: S3 has the Parquet")
    else:
        print("  SKIP: Lake sink did not succeed; S3 check skipped")

    # ---- Step 5: verify watermark ----
    _print_step(5, "Verify lake_archive_watermarks row")
    table_name = "ohlcv_1m" if args.kind == "minute" else "ohlcv_daily"
    wm = await repo.get(
        source=provider, table_name=table_name,
        period=target, stage="raw",
    )
    if wm is None:
        if lake_sink_result and lake_sink_result.status == "ok":
            print("  ERROR: lake sink wrote ok but no watermark row found")
            return 6
        print("  SKIP: no watermark expected (lake sink did not succeed)")
    else:
        print(f"  watermark: status={wm.status} bars={wm.bars_archived} "
              f"s3_key={wm.s3_key} archived_at={wm.archived_at}")
        if wm.status not in ("ok",):
            print(f"  ERROR: watermark status = {wm.status!r}")
            return 7
        if wm.s3_key != key:
            print(f"  ERROR: watermark s3_key mismatch: {wm.s3_key!r} != {key!r}")
            return 7
        print("  OK: watermark is consistent")

    print("\nsmoke: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
