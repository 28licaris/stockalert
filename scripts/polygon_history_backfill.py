"""
Pull Polygon flat-files into `equities.polygon_raw` for an arbitrary
date range — the v2 replacement for `polygon_bronze_backfill.py`.

Reusable for any operator need to extend history:
  - Initial Phase 1A bulk-load (CV4 — `--since 2021-01-04`).
  - Future Polygon-subscription upgrade ("got 10y of history now,
    pull 2016-2020").
  - Re-pull a specific window after a corp-action correction.

Idempotency:
  - Pre-scans `equities.polygon_raw` for the requested window and
    skips any trading day already loaded (via
    `app.services.equities.gaps.loaded_dates_in_range`).
  - Re-running the same `--since / --until` is a no-op once the window
    is fully loaded.
  - The underlying sink (`EquitiesIcebergSink`) is append-only; the
    skip-dates pre-scan is what makes the whole script safe to re-run.

Examples:

    # Yesterday only (same as the post-Phase-1B nightly cron will do)
    poetry run python scripts/polygon_history_backfill.py

    # Last 5 trading days
    poetry run python scripts/polygon_history_backfill.py --days 5

    # The Phase 1A bulk-load (CV4 operational step)
    poetry run python scripts/polygon_history_backfill.py \\
        --since 2021-01-04 \\
        --until 2026-05-20 \\
        --concurrency 4

    # Future 10y subscription extension (whole-market)
    poetry run python scripts/polygon_history_backfill.py \\
        --since 2016-01-04 \\
        --until 2020-12-31 \\
        --concurrency 4

Output:
  Final summary line counts ok / partial / errored days and total bars
  persisted. Exit code 1 if any day errored, 0 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.providers.polygon_flatfiles import PolygonFlatFilesClient  # noqa: E402
from app.services.equities.gaps import (  # noqa: E402
    loaded_dates_in_range,
    yesterday_et,
)
from app.services.equities.sink import EquitiesIcebergSink  # noqa: E402
from app.services.equities.tables import ensure_polygon_raw  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402
from app.services.ingest.flatfiles_backfill import FlatFilesBackfillService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polygon-history-backfill")


def _parse_date(s: str) -> date:
    s = s.strip().lower()
    if s == "yesterday":
        return yesterday_et()
    if s == "today":
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--days", type=int, default=None,
        help="Backfill the last N trading days ending at --until "
             "(or yesterday-ET).",
    )
    grp.add_argument(
        "--since", type=_parse_date, default=None,
        help="Inclusive start date (YYYY-MM-DD). Pair with --until "
             "for explicit windows.",
    )
    p.add_argument(
        "--until", type=_parse_date, default=None,
        help="Inclusive end date (default: yesterday-ET).",
    )
    p.add_argument(
        "--concurrency", type=int, default=4,
        help="Parallel days in flight (default: 4). Memory ~ "
             "concurrency × peak-day-frame (~150 MB for whole-market).",
    )
    p.add_argument(
        "--symbols", default="",
        help="Comma-separated symbols. Empty (default) = whole-market "
             "(every ticker in each daily flat-file).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Skip the loaded-dates pre-scan and re-process every day "
             "in the window. WARNING: produces duplicate rows in "
             "equities.polygon_raw (sink is append-only). Only use to "
             "recover from a known-corrupt write where you've already "
             "dropped the affected partitions manually.",
    )
    return p


def _resolve_window(args: argparse.Namespace) -> tuple[date, date]:
    end = args.until or yesterday_et()
    if args.since is not None:
        start = args.since
    elif args.days is not None:
        start = end - timedelta(days=args.days - 1)
    else:
        start = end
    if start > end:
        raise SystemExit(f"--since {start} is after --until {end}")
    return start, end


async def main() -> int:
    args = _build_parser().parse_args()

    if not (settings.stock_lake_bucket or "").strip():
        sys.exit("STOCK_LAKE_BUCKET is empty.")

    start, end = _resolve_window(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    log.info(
        "Polygon → equities.polygon_raw: window=%s..%s symbols=%s concurrency=%d",
        start, end, symbols if symbols else "ALL", args.concurrency,
    )

    # Skip-dates pre-scan against the actual target table — single
    # source of truth, no CH dependency.
    skip_dates: set[date] = set()
    if not args.force:
        try:
            catalog = get_catalog()
            table = ensure_polygon_raw(catalog)
            skip_dates = loaded_dates_in_range(table, start=start, end=end)
            if skip_dates:
                log.info(
                    "Pre-scan: %d trading day(s) in window already loaded; "
                    "will skip them.",
                    len(skip_dates),
                )
        except Exception as e:
            log.warning(
                "Pre-scan failed (%s); will process the entire window. "
                "Re-runs may double-write rows.", e,
            )

    # Build the v2 sink wired to equities.polygon_raw.
    sink = EquitiesIcebergSink.for_polygon_raw()
    client = PolygonFlatFilesClient.from_settings()
    svc = FlatFilesBackfillService(
        flat_files=client,
        sinks=[sink],
        source_tag="polygon-flatfiles",
    )

    try:
        result = await svc.backfill_range(
            symbols=symbols,
            start=start,
            end=end,
            kind="minute",
            concurrency=args.concurrency,
            skip_dates=skip_dates,
        )
    except Exception:
        log.exception("backfill_range failed")
        return 1

    log.info(
        "Done: days_ok=%d days_partial=%d days_errored=%d days_filtered=%d "
        "days_missing=%d bars_persisted=%d",
        result.days_ok, result.days_partial, result.days_errored,
        result.days_filtered, result.days_missing, result.bars_persisted,
    )
    return 1 if result.days_errored else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
