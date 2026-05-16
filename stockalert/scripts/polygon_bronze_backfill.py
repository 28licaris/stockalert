"""
Manually backfill `bronze.polygon_minute` for a date range.

Wraps `refresh_polygon_lake_yesterday` (the same function the 07:00 UTC
nightly job calls) but loops over a window so you can catch up missed
days. Idempotent — re-running the same date is fine.

Examples:
    # Yesterday only (same as the nightly does automatically)
    poetry run python scripts/polygon_bronze_backfill.py

    # Last 3 days
    poetry run python scripts/polygon_bronze_backfill.py --days 3

    # Explicit ET window
    poetry run python scripts/polygon_bronze_backfill.py \\
        --start 2026-05-13 --end 2026-05-15
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
from app.services.ingest.nightly_polygon_refresh import refresh_polygon_lake_yesterday  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polygon-bronze-backfill")


def _parse_date(s: str) -> date:
    s = s.strip().lower()
    if s == "yesterday":
        return date.today() - timedelta(days=1)
    if s == "today":
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--days", type=int, default=None,
                     help="Backfill the last N days ending at --end (or yesterday).")
    grp.add_argument("--start", type=_parse_date, default=None,
                     help="Inclusive start date (YYYY-MM-DD).")
    p.add_argument("--end", type=_parse_date, default=None,
                   help="Inclusive end date (default: yesterday).")
    p.add_argument("--include-weekends", action="store_true",
                   help="Process weekend dates too (usually no data; safe to skip).")
    return p


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _resolve_window(args: argparse.Namespace) -> list[date]:
    end = args.end or (date.today() - timedelta(days=1))
    if args.start is not None:
        start = args.start
    elif args.days is not None:
        start = end - timedelta(days=args.days - 1)
    else:
        start = end  # default: yesterday only
    if start > end:
        raise SystemExit(f"--start {start} is after --end {end}")
    out = []
    d = start
    while d <= end:
        if args.include_weekends or not _is_weekend(d):
            out.append(d)
        d += timedelta(days=1)
    return out


async def main():
    args = _build_parser().parse_args()
    if not (settings.stock_lake_bucket or "").strip():
        sys.exit("STOCK_LAKE_BUCKET is empty.")

    days = _resolve_window(args)
    log.info("Polygon → bronze.polygon_minute: %d day(s) %s .. %s",
             len(days), days[0] if days else "-", days[-1] if days else "-")

    ok = err = skipped = 0
    for d in days:
        log.info("→ %s", d)
        try:
            result = await refresh_polygon_lake_yesterday(target=d)
            status = result.get("skipped") and "skipped" or "ok"
            if result.get("skipped"):
                skipped += 1
                log.info("  skipped: %s", result.get("reason"))
            else:
                ok += 1
                log.info("  %s", result)
        except Exception as e:
            err += 1
            log.exception("  FAIL %s: %s", d, e)

    log.info("Done: ok=%d skipped=%d errors=%d", ok, skipped, err)
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
