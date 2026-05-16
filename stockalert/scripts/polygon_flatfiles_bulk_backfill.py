#!/usr/bin/env python3
"""
Bulk-backfill historical bars from Polygon (Massive) Flat Files.

By default writes to BOTH sinks:
  - ClickHouse hot cache (``ohlcv_1m`` / ``ohlcv_daily``)
  - S3 data lake (``stock-lake-...``, canonical Parquet at
    ``raw/provider={src}/kind={kind}/year={Y}/date={YYYY-MM-DD}.parquet``)

Either sink can be disabled via ``--no-write-clickhouse`` /
``--no-write-lake``. With the lake sink on, the run is **resumable**:
days that already have a successful ``lake_archive_watermarks`` row
are skipped (no download, no insert). Use ``--force`` to bypass the
short-circuit and re-archive every day.

Examples
--------
Seed the curated 100-ticker universe for the last 30 days (default
dual-sink + serial)::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols seed --start 2026-04-13 --end 2026-05-12

Five-year full-tape seed (the production seed run; parallel 4-way,
will skip already-archived days)::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols all --kind minute \\
        --start 2021-01-04 --end yesterday \\
        --workers 4

Lake-only (skip ClickHouse — useful for catching the S3 archive up
without churning the hot cache)::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols all --start 2026-05-12 --end 2026-05-12 \\
        --no-write-clickhouse

ClickHouse-only (skip the lake — useful when the lake bucket is down
or the operator wants to rebuild the cache without touching S3)::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols seed --start 2026-05-12 --end 2026-05-12 \\
        --no-write-lake

Force re-archive a day even if the watermark says it's done::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols all --start 2026-05-12 --end 2026-05-12 --force

Dry-run to confirm date listing without touching either sink::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \\
        --symbols seed --start 2026-05-12 --end 2026-05-12 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root scripts/ folder regardless of cwd, so the
# CLI works from anywhere. ``override=False`` lets shell env vars win.
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env", override=False)
load_dotenv(override=False)

from app.config import settings  # noqa: E402
from app.data.seed_universe import SEED_SYMBOLS  # noqa: E402
from app.db.lake_watermarks import WatermarkRepo  # noqa: E402
from app.providers.polygon_flatfiles import PolygonFlatFilesClient  # noqa: E402
from app.services.flatfiles_backfill import (  # noqa: E402
    DayResult,
    FlatFilesBackfillService,
)
from app.services.flatfiles_sinks import (  # noqa: E402
    ClickHouseSink,
    LakeSink,
    Sink,
)
from app.services.lake_archive import LakeArchiveWriter  # noqa: E402
from app.services.s3_lake_client import S3LakeClient  # noqa: E402


# ---------- argument parsing ----------


def _parse_date(s: str) -> date:
    """Accept YYYY-MM-DD plus the literal ``yesterday`` (handy for cron
    scripts that don't want to compute the date themselves)."""
    s = s.strip().lower()
    if s == "yesterday":
        return date.today() - timedelta(days=1)
    if s == "today":
        return date.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD or 'yesterday'/'today', got {s!r}: {e}"
        ) from e


def _resolve_symbols(spec: str) -> list[str]:
    """
    Accepts:
      - "seed" / "SEED"  -> the curated seed-100 universe
      - "all" / "*" / "" -> empty list (download the full file, no filter)
      - "AAPL,MSFT,SPY"  -> explicit comma-separated tickers
    """
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    if s in ("all", "*", ""):
        return []
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bulk-backfill bars from Polygon Flat Files to ClickHouse + S3 lake.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--symbols", default="seed",
        help="'seed' (default; curated 100 tickers), 'all' (no filter), "
             "or comma-separated explicit list (e.g. AAPL,MSFT,SPY).",
    )
    p.add_argument(
        "--start", type=_parse_date, required=True,
        help="Inclusive start date (YYYY-MM-DD, 'yesterday', or 'today').",
    )
    p.add_argument(
        "--end", type=_parse_date, required=True,
        help="Inclusive end date (YYYY-MM-DD, 'yesterday', or 'today').",
    )
    p.add_argument(
        "--kind", choices=("minute", "day"), default="minute",
        help="'minute' -> ohlcv_1m / kind=minute lake partition; "
             "'day' -> ohlcv_daily / kind=day.",
    )
    p.add_argument(
        "--source-tag",
        default=FlatFilesBackfillService.DEFAULT_SOURCE_TAG,
        help=f"Provenance tag stored on every row + watermark "
             f"(default: {FlatFilesBackfillService.DEFAULT_SOURCE_TAG!r}).",
    )
    p.add_argument(
        "--batch-size", type=int,
        default=FlatFilesBackfillService.DEFAULT_BATCH_SIZE,
        help="Rows per ClickHouse insert batch (default 1000).",
    )

    # --- sink toggles ---
    ch_group = p.add_mutually_exclusive_group()
    ch_group.add_argument(
        "--write-clickhouse", dest="write_clickhouse",
        action="store_true", default=True,
        help="Write to ClickHouse hot cache (default ON).",
    )
    ch_group.add_argument(
        "--no-write-clickhouse", dest="write_clickhouse",
        action="store_false",
        help="Skip ClickHouse writes.",
    )

    lake_group = p.add_mutually_exclusive_group()
    lake_group.add_argument(
        "--write-lake", dest="write_lake",
        action="store_true", default=None,
        help="Write to the S3 lake (default: follow POLYGON_NIGHTLY_ENABLED).",
    )
    lake_group.add_argument(
        "--no-write-lake", dest="write_lake",
        action="store_false",
        help="Skip S3 lake writes.",
    )

    p.add_argument(
        "--workers", type=int, default=1,
        help="Number of days processed in parallel (default 1). "
             "Memory peaks at ~150 MB × workers for the full US tape.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Bypass the lake watermark short-circuit. Re-downloads and "
             "re-archives every day in range, even if previously done.",
    )

    p.add_argument(
        "--dry-run", action="store_true",
        help="Download + parse but do NOT write to any sink.",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print INFO logs (defaults to WARNING only).",
    )
    return p


# ---------- formatting ----------


def _format_size(n: int) -> str:
    f = float(n)
    for unit in ("", "K", "M", "B"):
        if f < 1000:
            return f"{f:.1f}{unit}"
        f /= 1000
    return f"{f:.1f}T"


def _format_elapsed(s: float) -> str:
    if s < 60:
        return f"{s:5.1f}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{int(m):>2d}m{int(s):02d}s"
    h, m = divmod(m, 60)
    return f"{int(h)}h{int(m):02d}m"


# ---------- sink wiring ----------


@dataclass(frozen=True, slots=True)
class _SinkConfig:
    """Resolved sink-build decision after merging flags + settings."""
    write_clickhouse: bool
    write_lake: bool
    force: bool


def _resolve_sink_config(args: argparse.Namespace) -> _SinkConfig:
    """Merge ``--write-lake`` / ``--no-write-lake`` / settings into a
    single decision. ``--write-lake`` unspecified falls back to
    ``POLYGON_NIGHTLY_ENABLED``."""
    if args.write_lake is None:
        write_lake = bool(settings.polygon_nightly_enabled)
    else:
        write_lake = bool(args.write_lake)
    return _SinkConfig(
        write_clickhouse=bool(args.write_clickhouse),
        write_lake=write_lake,
        force=bool(args.force),
    )


def _build_sinks(cfg: _SinkConfig, *, batch_size: int) -> list[Sink]:
    """Construct the sink list per the resolved config. Each sink is
    built explicitly so this script is self-contained and doesn't
    silently inherit sink defaults from ``from_settings()``."""
    sinks: list[Sink] = []
    if cfg.write_clickhouse:
        sinks.append(ClickHouseSink.from_settings(batch_size=batch_size))
    if cfg.write_lake:
        if not settings.stock_lake_bucket:
            print(
                "FAIL: --write-lake requested but STOCK_LAKE_BUCKET is empty. "
                "Set it in your .env or pass --no-write-lake.",
                file=sys.stderr,
            )
            sys.exit(2)
        writer = LakeArchiveWriter(
            s3=S3LakeClient.from_settings(),
            watermarks=WatermarkRepo.from_clickhouse(),
        )
        sinks.append(LakeSink(writer=writer, force=cfg.force))
    return sinks


# ---------- resumability ----------


async def _scan_resumable_skip_set(
    *,
    source_tag: str,
    kind: str,
    start: date,
    end: date,
) -> set[date]:
    """Read ``lake_archive_watermarks`` for the range and return the
    set of dates that already have a successful watermark.

    These dates are passed to ``backfill_range(skip_dates=...)`` so we
    don't re-download / re-parse / re-insert them. The skip set is
    only meaningful when the lake sink is enabled — otherwise we have
    no source-of-truth for "already done" and re-process everything.
    """
    table_name = "ohlcv_1m" if kind == "minute" else "ohlcv_daily"
    repo = WatermarkRepo.from_clickhouse()
    return await repo.get_ok_dates(
        source=source_tag, table_name=table_name, stage="raw",
        start=start, end=end,
    )


# ---------- progress / summary ----------


def _make_progress(total_days: int, started_at: float):
    """Build a closure that prints a one-line update per day. The total
    is the post-filter day count, so the X/Y counter reflects what
    the run will actually do (not the calendar range)."""
    state = {"done": 0, "bars_total": 0}

    def _sink_glyph(sink_results: dict) -> str:
        """One-glyph-per-sink status line, e.g. ``ch:ok lake:ok``."""
        if not sink_results:
            return ""
        parts: list[str] = []
        for name, sr in sink_results.items():
            short = "ch" if name == "clickhouse" else name
            parts.append(f"{short}:{sr.status}")
        return "  " + " ".join(parts)

    def _on_progress(day: DayResult) -> None:
        state["done"] += 1
        state["bars_total"] += day.bars_persisted
        elapsed = time.monotonic() - started_at
        status_str = day.status.ljust(8)
        suffix = ""
        if day.status in ("error", "partial"):
            suffix = f"  ERROR: {day.error}"
        elif day.status == "ok":
            suffix = (
                f"  bars={day.bars_persisted:>7,d}  syms={day.symbols_seen:>5,d}"
                f"{_sink_glyph(day.sink_results)}"
            )
        elif day.status == "partial":
            suffix += _sink_glyph(day.sink_results)
        print(
            f"  [{state['done']:>4d}/{total_days:>4d}] "
            f"{day.file_date}  {status_str}  "
            f"in {_format_elapsed(day.elapsed_s)}  "
            f"total={_format_size(state['bars_total'])} bars  "
            f"elapsed={_format_elapsed(elapsed)}"
            f"{suffix}",
            flush=True,
        )

    return _on_progress


def _print_summary(
    result, total_elapsed: float, *,
    sink_cfg: _SinkConfig, resumed_skipped: int,
) -> None:
    print()
    print("Summary")
    print(f"  range listed   : {result.days_listed} day(s)")
    if resumed_skipped:
        print(f"  resumed skip   : {resumed_skipped} day(s) "
              f"(already archived)")
    print(f"  ok             : {result.days_ok}")
    print(f"  partial        : {result.days_partial}")
    print(f"  filtered       : {result.days_filtered}")
    print(f"  missing        : {result.days_missing}")
    print(f"  skipped        : {result.days_skipped}")
    print(f"  errored        : {result.days_errored}")
    print(f"  bars persisted : {result.bars_persisted:,}")
    print(f"  elapsed        : {_format_elapsed(total_elapsed)}")

    # Per-sink rollup (totals across days). Useful for verifying both
    # sinks fired and reporting partial coverage.
    sink_totals: dict[str, dict] = {}
    for day in result.days:
        for name, sr in day.sink_results.items():
            t = sink_totals.setdefault(
                name,
                {"ok": 0, "skipped": 0, "error": 0, "bars": 0},
            )
            t[sr.status] = t.get(sr.status, 0) + 1
            t["bars"] += sr.bars_written
    if sink_totals:
        print()
        print("Per-sink totals")
        for name in sorted(sink_totals.keys()):
            t = sink_totals[name]
            print(
                f"  {name:<12s} ok={t.get('ok', 0):>4d} "
                f"skipped={t.get('skipped', 0):>4d} "
                f"error={t.get('error', 0):>4d} "
                f"bars={t.get('bars', 0):,}"
            )


# ---------- main ----------


async def main(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args.symbols)
    sym_display = (
        "ALL US tickers" if not symbols
        else f"{len(symbols)} tickers ({', '.join(symbols[:5])}"
             f"{'...' if len(symbols) > 5 else ''})"
    )
    sink_cfg = _resolve_sink_config(args)

    print("Polygon Flat Files bulk backfill")
    print(f"  kind            : {args.kind}")
    print(f"  window          : {args.start} .. {args.end}")
    print(f"  symbols         : {sym_display}")
    print(f"  source / wm tag : {args.source_tag}")
    print(f"  batch (CH)      : {args.batch_size}")
    print(f"  workers         : {args.workers}")
    print(f"  force           : {sink_cfg.force}")
    print(f"  write_clickhouse: {sink_cfg.write_clickhouse}")
    print(f"  write_lake      : {sink_cfg.write_lake} "
          f"(POLYGON_NIGHTLY_ENABLED={settings.polygon_nightly_enabled})")
    print(f"  dry-run         : {args.dry_run}")
    print()

    if args.workers < 1:
        print("FAIL: --workers must be >= 1", file=sys.stderr)
        return 2

    # ---- sink wiring ----
    sinks = _build_sinks(sink_cfg, batch_size=args.batch_size)
    if not sinks and not args.dry_run:
        print(
            "FAIL: no sinks enabled (use --write-clickhouse or --write-lake) "
            "and not a dry-run.",
            file=sys.stderr,
        )
        return 2

    # ---- build service ----
    try:
        client = PolygonFlatFilesClient.from_settings()
    except Exception as e:
        print(f"FAIL: could not build Polygon flat-files client: {e}",
              file=sys.stderr)
        return 2

    service = FlatFilesBackfillService(
        flat_files=client,
        sinks=sinks,
        source_tag=args.source_tag,
        batch_size=args.batch_size,
    )
    print(f"  sinks           : {[s.name for s in service.sinks] or '(dry-run only)'}")

    # ---- resumability pre-scan ----
    # Only meaningful when the lake sink is active (watermarks are the
    # source of truth for "already done"). When the lake sink is off,
    # we have no reliable signal and re-process every day.
    skip_dates: set[date] = set()
    if sink_cfg.write_lake and not sink_cfg.force and not args.dry_run:
        print()
        print(f"Scanning lake_archive_watermarks for already-done days in "
              f"{args.start}..{args.end} ...")
        try:
            skip_dates = await _scan_resumable_skip_set(
                source_tag=args.source_tag, kind=args.kind,
                start=args.start, end=args.end,
            )
            print(f"  resumable skip set: {len(skip_dates)} day(s) already 'ok'")
        except Exception as e:
            # Don't abort the run if the pre-scan fails — we just lose
            # the resumability optimisation. The lake sink itself still
            # short-circuits on a per-day basis.
            print(f"  WARN: watermark pre-scan failed ({e}); continuing "
                  f"without skip set.", file=sys.stderr)
            skip_dates = set()

    # ---- pre-list dates for the progress bar's denominator ----
    started = time.monotonic()
    listing = await asyncio.to_thread(
        client.available_dates, args.start, args.end, kind=args.kind,
    )
    if not listing:
        print(f"\nNo Polygon Flat Files for kind={args.kind} in "
              f"{args.start}..{args.end}. Nothing to do.")
        return 0
    after_skip = [f for f in listing if f.file_date not in skip_dates]
    resumed_skipped = len(listing) - len(after_skip)
    print(f"\nFound {len(listing)} {args.kind} file(s) in range "
          f"({listing[0].file_date}..{listing[-1].file_date}); "
          f"{len(after_skip)} to process, {resumed_skipped} skipped.")
    if not after_skip:
        print("All days already archived. Nothing to do (re-run with --force "
              "to re-archive).")
        return 0
    print()

    on_progress = _make_progress(len(after_skip), started)
    result = await service.backfill_range(
        symbols, args.start, args.end,
        kind=args.kind,
        dry_run=args.dry_run,
        on_progress=on_progress,
        concurrency=args.workers,
        skip_dates=skip_dates,
    )

    total_elapsed = time.monotonic() - started
    _print_summary(result, total_elapsed,
                   sink_cfg=sink_cfg, resumed_skipped=resumed_skipped)

    # Exit codes:
    #   0 = clean (no errored days)
    #   1 = at least one fully-failed day
    # Partial days do NOT trigger a non-zero exit because data is in at
    # least one persistent store and a re-run will idempotently complete
    # the missing sink.
    return 0 if result.days_errored == 0 else 1


if __name__ == "__main__":
    parsed = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO if parsed.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Workers > 1 multiplies S3 connections; tell urllib3 to permit more
    # so workers don't queue up on the default pool of 10.
    if parsed.workers > 1:
        os.environ.setdefault(
            "AWS_DATA_PATH", os.environ.get("AWS_DATA_PATH", ""),
        )
    rc = asyncio.run(main(parsed))
    sys.exit(rc)
