#!/usr/bin/env python3
"""Polygon futures flat-files backfill → futures.polygon_futures.

Downloads bulk 1-minute OHLCV from Polygon's S3 flat-file bucket for CME /
COMEX / CBOT / NYMEX futures, stitches rows into continuous roots using
front-month windows, and appends to the Iceberg lake table
`futures.polygon_futures`.

Advantages over the REST API approach (polygon_futures_backfill.py):
  • No per-request rate limits — S3 downloads are throttled by bandwidth only
  • One file per exchange per day covers ALL contracts simultaneously
  • 10 years of CME minute data ≈ 2 500 files × 1.5 MB ≈ 3.7 GB per exchange

Flat-file layout:
  s3://flatfiles/{exchange}/minute_aggs_v1/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz
  Columns: ticker, exchange, session_end_date, window_start, open, high, low,
           close, volume, dollar_volume, transactions

Architecture:
  1. Discover contracts   — REST list_futures_contracts (once per root, small)
  2. Build windows        — front-month date ranges per contract
  3. Build day→ticker map — O(1) lookup dict for any calendar date
  4. List available dates — S3 listing for the exchange prefix
  5. Per day: download flat file, filter to front-month ticker, write to lake

Re-runnable: Iceberg append is idempotent at the (symbol, timestamp) level;
CH's ReplacingMergeTree dedupes on read.

Note: multiple roots on the same exchange (e.g. ES + NQ on CME) each download
the full CME file and filter independently. For a one-time bulk backfill this
is acceptable; for frequent re-runs consider grouping by exchange.

Usage:
    poetry run python scripts/polygon_futures_flatfiles_backfill.py --root ES
    poetry run python scripts/polygon_futures_flatfiles_backfill.py --root ES NQ GC CL
    poetry run python scripts/polygon_futures_flatfiles_backfill.py --root ES --start-year 2017 --end-year 2026
    poetry run python scripts/polygon_futures_flatfiles_backfill.py --root ES --dry-run

Environment:
    POLYGON_API_KEY              required (contract discovery via REST)
    POLYGON_S3_ACCESS_KEY_ID     required (flat-file downloads via S3)
    POLYGON_S3_SECRET_ACCESS_KEY required (flat-file downloads via S3)
    STOCK_LAKE_BUCKET            required (unless --dry-run)
    AWS_PROFILE                  optional (default: stock-lake via config.py)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ── Bootstrap ──────────────────────────────────────────────────────────────
import os as _os
_os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")

from dotenv import load_dotenv
load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon_futures_flatfiles_backfill")

for _noisy in ("botocore", "boto3", "urllib3", "pyiceberg", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_BATCH_SIZE = 10_000
_DEFAULT_START_YEAR = 2017  # First year available in Polygon flat files


# ── Calendar helpers ──────────────────────────────────────────────────────────

def _build_day_ticker_map(windows) -> dict[date, str]:
    """Build {calendar_date: front_month_ticker} from ContractWindow list.

    Iterates each window's [front_start, front_end] span — at most ~2 500
    total days for 10 years of quarterly contracts. Gives O(1) per-day
    lookups at download time.
    """
    out: dict[date, str] = {}
    one = timedelta(days=1)
    for w in windows:
        cur = w.front_start
        while cur <= w.front_end:
            out[cur] = w.ticker
            cur += one
    return out


# ── Bar conversion ─────────────────────────────────────────────────────────────

def _df_to_bars(df, symbol: str) -> list[dict]:
    """Convert a filtered futures DataFrame row to bar dicts for PolygonFuturesSink.

    ``df`` must already have the ``timestamp`` (UTC datetime) and ``vwap``
    columns added by ``_read_futures_csv_gz``. ``symbol`` is the continuous
    root (e.g. ``"/ES"``).
    """
    import pandas as pd

    def _fval(v) -> Optional[float]:
        try:
            return None if pd.isna(v) else float(v)
        except (TypeError, ValueError):
            return None

    def _ival(v) -> Optional[int]:
        try:
            return None if pd.isna(v) else int(v)
        except (TypeError, ValueError):
            return None

    bars: list[dict] = []
    for row in df.itertuples(index=False):
        ts = row.timestamp
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        bars.append({
            "symbol":      symbol,
            "timestamp":   ts,
            "open":        _fval(row.open),
            "high":        _fval(row.high),
            "low":         _fval(row.low),
            "close":       _fval(row.close),
            "volume":      _fval(row.volume),
            "vwap":        _fval(getattr(row, "vwap", None)),
            "trade_count": _ival(row.transactions),
        })
    return bars


# ── Per-root processing ───────────────────────────────────────────────────────

def process_root(
    root: str,
    *,
    flat_client,
    rest_client,
    sink,
    start_date: date,
    end_date: date,
    batch_size: int,
    dry_run: bool,
) -> dict:
    """Download flat-file data for one root and write to the lake.

    Returns a summary dict. Never raises — errors are captured in the result.
    """
    from app.providers.polygon_flatfiles import FUTURES_EXCHANGE_PREFIXES
    from app.services.futures.contract_chain import (
        build_front_month_windows,
        discover_contracts,
    )

    product_code = root.lstrip("/").upper()
    symbol = f"/{product_code}"

    exchange_prefix = FUTURES_EXCHANGE_PREFIXES.get(product_code)
    if exchange_prefix is None:
        msg = (
            f"{product_code}: no exchange prefix mapping — "
            "add it to FUTURES_EXCHANGE_PREFIXES in app/providers/polygon_flatfiles.py"
        )
        logger.error(msg)
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": msg,
        }

    logger.info("\n── %s (%s) → %s ──", symbol, product_code, exchange_prefix)

    # Step 1: discover contracts from Polygon REST (small number of calls)
    try:
        contracts = discover_contracts(
            rest_client, product_code,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        logger.error("discover_contracts failed for %s: %s", product_code, exc)
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": str(exc),
        }

    if not contracts:
        logger.warning("%s: no contracts found for %s–%s; skipping",
                       product_code, start_date.year, end_date.year)
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": None,
        }

    # Step 2: build front-month windows
    windows = build_front_month_windows(contracts)
    if not windows:
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": None,
        }

    # Step 3: build calendar day → front-month ticker lookup
    day_ticker = _build_day_ticker_map(windows)
    logger.info(
        "%s: %d contracts → %d windows → %d calendar days mapped",
        product_code, len(contracts), len(windows), len(day_ticker),
    )

    # Step 4: enumerate trading days actually present in S3
    try:
        available = flat_client.available_futures_dates(
            exchange_prefix, start_date, end_date,
        )
    except Exception as exc:
        logger.error("available_futures_dates failed for %s: %s", exchange_prefix, exc)
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": str(exc),
        }

    if not available:
        logger.warning("%s: no flat files found in S3 for %s–%s",
                       product_code, start_date, end_date)
        return {
            "root": root, "rows_fetched": 0, "rows_written": 0,
            "days_processed": 0, "days_skipped": 0, "error": None,
        }

    logger.info(
        "%s: %d trading days in S3 (%s → %s)",
        product_code, len(available),
        available[0].file_date, available[-1].file_date,
    )

    # Step 5: per-day — download → filter → batch → write
    t0 = time.monotonic()
    rows_fetched = rows_written = days_ok = days_skipped = 0
    batch: list[dict] = []

    for fi in available:
        d = fi.file_date
        ticker = day_ticker.get(d)
        if ticker is None:
            days_skipped += 1
            logger.debug("%s: %s — no front-month ticker mapped, skipping", product_code, d)
            continue

        try:
            df = flat_client.download_futures_minute_aggs(
                exchange_prefix, d, tickers=[ticker],
            )
        except Exception as exc:
            logger.warning("%s: download failed for %s: %s", product_code, d, exc)
            days_skipped += 1
            continue

        if df.empty:
            # Market closed or no bars for this ticker on this day
            days_skipped += 1
            continue

        bars = _df_to_bars(df, symbol)
        if not bars:
            days_skipped += 1
            continue

        rows_fetched += len(bars)
        batch.extend(bars)
        days_ok += 1

        if len(batch) >= batch_size:
            if not dry_run and sink is not None:
                rows_written += sink.write_batch(batch)
            else:
                rows_written += len(batch)
            batch = []

        if days_ok % 50 == 0:
            logger.info(
                "  %s  %s  ticker=%-8s  fetched=%d  written=%d  (%.0fs elapsed)",
                product_code, d, ticker, rows_fetched, rows_written,
                time.monotonic() - t0,
            )

    # Flush remaining batch
    if batch:
        if not dry_run and sink is not None:
            rows_written += sink.write_batch(batch)
        else:
            rows_written += len(batch)

    elapsed = time.monotonic() - t0
    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        "%s%s done: days=%d  skipped=%d  fetched=%d  written=%d  %.1fs",
        label, product_code, days_ok, days_skipped, rows_fetched, rows_written, elapsed,
    )

    return {
        "root": root,
        "rows_fetched": rows_fetched,
        "rows_written": rows_written,
        "days_processed": days_ok,
        "days_skipped": days_skipped,
        "error": None,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run_backfill(
    roots: list[str],
    *,
    start_year: int,
    end_year: int,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Orchestrate flat-files futures backfill for one or more roots.

    Returns a summary dict. Exits non-zero (sys.exit) on missing prerequisites
    (API credentials, lake bucket).
    """
    from app.config import settings

    # Pre-flight checks
    api_key = settings.polygon_api_key or ""
    if not api_key.strip():
        logger.error("POLYGON_API_KEY is not set. Cannot discover contracts via REST.")
        sys.exit(1)

    if not (settings.polygon_s3_access_key_id and settings.polygon_s3_secret_access_key):
        logger.error(
            "POLYGON_S3_ACCESS_KEY_ID / POLYGON_S3_SECRET_ACCESS_KEY not set. "
            "These are required for flat-file downloads."
        )
        sys.exit(1)

    if not dry_run and not (settings.stock_lake_bucket or "").strip():
        logger.error(
            "STOCK_LAKE_BUCKET is not set. Set it or pass --dry-run to test "
            "without writing."
        )
        sys.exit(1)

    start_date = date(start_year, 1, 1)
    end_date = date(end_year, 12, 31)

    logger.info(
        "=== Polygon futures flat-files backfill %s–%s | roots=%s | %s ===",
        start_date, end_date, ", ".join(roots),
        "DRY RUN" if dry_run else "writing to futures.polygon_futures",
    )

    # REST client for contract discovery (small number of calls)
    from massive import RESTClient
    rest_client = RESTClient(
        api_key=api_key,
        connect_timeout=10,
        read_timeout=60,
        retries=3,
    )

    # S3 flat-files client (reused across all roots)
    from app.providers.polygon_flatfiles import PolygonFlatFilesClient
    flat_client = PolygonFlatFilesClient.from_settings()

    # Iceberg sink (created once — opens the table, sets run_id + ingestion_ts)
    sink = None
    if not dry_run:
        from app.services.futures.polygon_sink import PolygonFuturesSink
        sink = PolygonFuturesSink()
        logger.info("Sink ready: %s", sink.table_name)

    total_t0 = time.monotonic()
    all_results: list[dict] = []
    errors: list[str] = []

    for root in roots:
        result = process_root(
            root,
            flat_client=flat_client,
            rest_client=rest_client,
            sink=sink,
            start_date=start_date,
            end_date=end_date,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        all_results.append(result)
        if result.get("error"):
            errors.append(root)

    # Refresh Iceberg snapshot after all writes
    if not dry_run and sink is not None:
        snap = sink.refresh_snapshot()
        logger.info("Snapshot after write: %s", snap)

    elapsed = time.monotonic() - total_t0
    grand_fetched = sum(r["rows_fetched"] for r in all_results)
    grand_written = sum(r["rows_written"] for r in all_results)
    grand_days = sum(r["days_processed"] for r in all_results)

    summary = {
        "roots": roots,
        "start_year": start_year,
        "end_year": end_year,
        "dry_run": dry_run,
        "roots_processed": len(all_results),
        "days_processed": grand_days,
        "rows_fetched": grand_fetched,
        "rows_written": grand_written,
        "elapsed_s": round(elapsed, 1),
        "errors": errors,
    }

    logger.info(
        "\n=== DONE  days=%d  fetched=%d  written=%d  errors=%d  elapsed=%.1fs ===",
        grand_days, grand_fetched, grand_written, len(errors), elapsed,
    )
    if errors:
        logger.warning("Roots with errors: %s", errors)

    return summary


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--root", dest="roots", nargs="+", required=True,
        metavar="ROOT",
        help=(
            "CME/COMEX/CBOT/NYMEX product code(s) to backfill: ES NQ GC CL "
            "(leading slash optional)"
        ),
    )
    p.add_argument(
        "--start-year", type=int, default=_DEFAULT_START_YEAR,
        help=f"First calendar year to pull (default: {_DEFAULT_START_YEAR})",
    )
    p.add_argument(
        "--end-year", type=int, default=date.today().year,
        help="Last calendar year to pull (default: current year)",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Iceberg write batch size in rows (default: {_DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Discover contracts and count bars but do not write to the lake. "
            "Downloads flat files normally — useful for verifying coverage."
        ),
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    roots = [r.lstrip("/").upper() for r in args.roots]

    summary = run_backfill(
        roots,
        start_year=args.start_year,
        end_year=args.end_year,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
