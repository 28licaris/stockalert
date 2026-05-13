#!/usr/bin/env python3
"""
Bulk-backfill historical bars from Polygon (Massive) Flat Files into
ClickHouse.

Examples
--------
Backfill the curated seed universe (100 tickers) for the last 30 days::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \
        --symbols seed --start 2026-04-13 --end 2026-05-12

Backfill an explicit symbol list, daily aggregates, full 2025::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \
        --symbols AAPL,MSFT,SPY,QQQ --kind day \
        --start 2025-01-01 --end 2025-12-31

Backfill the **entire US-equities tape** for one day (~1.9M rows; takes
several minutes and bytes through ~30MB)::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \
        --symbols all --start 2026-05-12 --end 2026-05-12

Dry-run to confirm date listing + downloads without touching ClickHouse::

    poetry run python scripts/polygon_flatfiles_bulk_backfill.py \
        --symbols seed --start 2026-05-12 --end 2026-05-12 --dry-run

The script reuses ``FlatFilesBackfillService`` so a green run here is the
same code path the in-app backfill enqueue will eventually drive.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()

from app.data.seed_universe import SEED_SYMBOLS  # noqa: E402
from app.services.flatfiles_backfill import (  # noqa: E402
    DayResult,
    FlatFilesBackfillService,
)


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {s!r}: {e}"
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


def _make_progress(total_days: int, started_at: float):
    """Build a closure that prints a one-line update per day. Total-days
    is the *listed* day count, not requested calendar days, so the X/Y
    counter reflects what we'll actually do."""
    state = {"done": 0, "bars_total": 0}

    def _on_progress(day: DayResult) -> None:
        state["done"] += 1
        state["bars_total"] += day.bars_persisted
        elapsed = time.monotonic() - started_at
        # Pad the file_date field so columns line up regardless of status.
        status_str = day.status.ljust(8)
        flag = "OK" if day.status == "ok" else day.status.upper()[:8]
        suffix = ""
        if day.status == "error":
            suffix = f"  ERROR: {day.error}"
        elif day.status == "ok":
            suffix = (
                f"  bars={day.bars_persisted:>7,d}  syms={day.symbols_seen:>5,d}"
            )
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


async def main(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args.symbols)
    sym_display = (
        "ALL US tickers" if not symbols
        else f"{len(symbols)} tickers ({', '.join(symbols[:5])}"
             f"{'...' if len(symbols) > 5 else ''})"
    )

    print("Polygon Flat Files bulk backfill")
    print(f"  kind     : {args.kind}")
    print(f"  window   : {args.start} .. {args.end}")
    print(f"  symbols  : {sym_display}")
    print(f"  source   : {args.source_tag}")
    print(f"  batch    : {args.batch_size}")
    print(f"  dry-run  : {args.dry_run}")
    print()

    try:
        service = FlatFilesBackfillService.from_settings()
    except Exception as e:
        print(f"FAIL: could not build FlatFilesBackfillService: {e}",
              file=sys.stderr)
        return 2

    # Override source tag / batch size if caller asked for non-defaults.
    # We rebuild instead of mutating so the service stays immutable.
    if (args.source_tag != FlatFilesBackfillService.DEFAULT_SOURCE_TAG
            or args.batch_size != FlatFilesBackfillService.DEFAULT_BATCH_SIZE):
        service = FlatFilesBackfillService(
            flat_files=service._client(),  # reuse the already-built client
            source_tag=args.source_tag,
            batch_size=args.batch_size,
        )

    started = time.monotonic()
    # First pass: list available files so the progress bar can show a
    # meaningful X/Y. ``backfill_range`` will re-list under the hood; the
    # extra call is cheap and keeps the CLI surface simple.
    listing = await asyncio.to_thread(
        service._client().available_dates, args.start, args.end, kind=args.kind,
    )
    if not listing:
        print(f"No Polygon Flat Files for kind={args.kind} in "
              f"{args.start}..{args.end}. Nothing to do.")
        return 0
    print(f"Found {len(listing)} {args.kind} file(s) in range "
          f"({listing[0].file_date}..{listing[-1].file_date}).")
    print()

    on_progress = _make_progress(len(listing), started)
    result = await service.backfill_range(
        symbols, args.start, args.end,
        kind=args.kind,
        dry_run=args.dry_run,
        on_progress=on_progress,
    )

    total_elapsed = time.monotonic() - started
    print()
    print("Summary")
    print(f"  listed   : {result.days_listed}")
    print(f"  ok       : {result.days_ok}")
    print(f"  filtered : {result.days_filtered}")
    print(f"  missing  : {result.days_missing}")
    print(f"  skipped  : {result.days_skipped}")
    print(f"  errored  : {result.days_errored}")
    print(f"  bars     : {result.bars_persisted:,}")
    print(f"  elapsed  : {_format_elapsed(total_elapsed)}")

    return 0 if result.days_errored == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bulk-backfill bars from Polygon Flat Files to ClickHouse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--symbols",
        default="seed",
        help="'seed' (default; curated 100 tickers), 'all' (no filter), "
             "or comma-separated explicit list (e.g. AAPL,MSFT,SPY).",
    )
    p.add_argument(
        "--start",
        type=_parse_date,
        required=True,
        help="Inclusive start date, YYYY-MM-DD.",
    )
    p.add_argument(
        "--end",
        type=_parse_date,
        required=True,
        help="Inclusive end date, YYYY-MM-DD.",
    )
    p.add_argument(
        "--kind",
        choices=("minute", "day"),
        default="minute",
        help="'minute' (1-min aggs -> ohlcv_1m) or 'day' (daily -> ohlcv_daily).",
    )
    p.add_argument(
        "--source-tag",
        default=FlatFilesBackfillService.DEFAULT_SOURCE_TAG,
        help=f"Provenance tag stored on every row "
             f"(default: {FlatFilesBackfillService.DEFAULT_SOURCE_TAG!r}).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=FlatFilesBackfillService.DEFAULT_BATCH_SIZE,
        help="Rows per ClickHouse insert (default 1000).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Download + parse but DO NOT insert. Useful for sizing runs.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print INFO logs (defaults to WARNING only).",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    rc = asyncio.run(main(args))
    sys.exit(rc)
