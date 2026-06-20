#!/usr/bin/env python3
"""Polygon futures flat-files backfill → futures.polygon_futures.

Exchange-grouped approach: each exchange flat file is downloaded ONCE per
trading day and bars are split across ALL roots in that exchange in one pass.

  outer loop: (exchange, trading_day)  ← 1 download per unique file
  inner split: each root's front-month ticker extracted from that download

Total downloads: ~10,000 (4 exchanges × ~2,520 days).
Sequential wall time: ~1.25h for all exchanges and roots.
Contrast with the per-root approach: ~93,000 downloads, ~14h.

Architecture:
  1. Group roots by exchange prefix (ES+NQ → cme, GC+SI → comex, …)
  2. Per exchange: discover contracts for all roots (REST, once each)
  3. Build {date: {root: ticker}} calendar from front-month windows
  4. List available flat-file dates for the exchange (S3 listing, once)
  5. Per trading day: download the exchange file once, split bars by root,
     accumulate into a combined batch, write to Iceberg when batch is full

Re-runnable: Iceberg append is idempotent at the (symbol, timestamp) level.

Usage:
    # Dry-run — verify coverage without writing
    poetry run python scripts/polygon_futures_flatfiles_backfill.py \\
        --root ES NQ GC CL --start-year 2024 --end-year 2024 --dry-run

    # Full 10-year backfill of all roots
    poetry run python scripts/polygon_futures_flatfiles_backfill.py \\
        --root ES MES NQ MNQ YM MYM RTY M2K \\
               GC MGC SI SIL HG PL PA \\
               ZB UB ZN ZF ZT ZC ZS ZW ZM ZL \\
               CL MCL NG RB HO BZ \\
        --start-year 2017 --end-year 2026

Environment:
    POLYGON_API_KEY              required (contract discovery via REST)
    POLYGON_S3_ACCESS_KEY_ID     required (flat-file downloads)
    POLYGON_S3_SECRET_ACCESS_KEY required
    STOCK_LAKE_BUCKET            required (unless --dry-run)
    AWS_PROFILE                  optional (default: stock-lake via config.py)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from datetime import date, timedelta, timezone
from typing import Optional

import os as _os
_os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon_futures_flatfiles_backfill")

for _noisy in ("botocore", "boto3", "urllib3", "pyiceberg", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_DEFAULT_BATCH_SIZE = 10_000
_DEFAULT_START_YEAR = 2017


# ── Calendar helpers ──────────────────────────────────────────────────────────

def _build_day_ticker_map(windows) -> dict[date, str]:
    """Build {calendar_date: front_month_ticker} from ContractWindow list."""
    out: dict[date, str] = {}
    one = timedelta(days=1)
    for w in windows:
        cur = w.front_start
        while cur <= w.front_end:
            out[cur] = w.ticker
            cur += one
    return out


# ── Bar conversion ────────────────────────────────────────────────────────────

def _df_to_bars(df, symbol: str) -> list[dict]:
    """Convert a filtered futures DataFrame slice to bar dicts."""
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


# ── Contract discovery ────────────────────────────────────────────────────────

def _discover_root(
    product_code: str,
    rest_client,
    start_date: date,
    end_date: date,
) -> Optional[dict[date, str]]:
    """Return a {calendar_date: front_month_ticker} map for one root.

    Returns None on failure so callers can skip the root without crashing.
    """
    from app.services.futures.contract_chain import (
        build_front_month_windows,
        discover_contracts,
    )
    try:
        contracts = discover_contracts(
            rest_client, product_code,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        logger.error("%s: contract discovery failed: %s", product_code, exc)
        return None

    if not contracts:
        logger.warning("%s: no contracts found for %s–%s; skipping",
                       product_code, start_date.year, end_date.year)
        return None

    windows = build_front_month_windows(contracts)
    if not windows:
        return None

    day_ticker = _build_day_ticker_map(windows)
    logger.info("  %s: %d contracts → %d windows → %d days mapped",
                product_code, len(contracts), len(windows), len(day_ticker))
    return day_ticker


# ── Per-exchange processing ───────────────────────────────────────────────────

def process_exchange(
    exchange_prefix: str,
    roots: list[str],          # product codes for this exchange, e.g. ["ES","NQ","YM"]
    *,
    flat_client,
    rest_client,
    sink,
    start_date: date,
    end_date: date,
    batch_size: int,
    dry_run: bool,
) -> dict:
    """Download each exchange flat file once and emit bars for all roots in one pass.

    For each trading day:
      1. Find the front-month ticker for every root on that day.
      2. Download the exchange file once, filtered to all those tickers.
      3. Split the resulting DataFrame by ticker and convert to per-root bars.
      4. Accumulate in a combined batch; flush to Iceberg when full.

    Never raises — errors are captured in the return dict.
    """
    logger.info("\n════ %s — roots: %s ════", exchange_prefix, " ".join(roots))
    t0 = time.monotonic()

    # Step 1: discover contracts for every root in this exchange.
    # root → (continuous_symbol, {date: ticker})
    root_data: dict[str, tuple[str, dict[date, str]]] = {}
    for product_code in roots:
        day_ticker = _discover_root(product_code, rest_client, start_date, end_date)
        if day_ticker is not None:
            root_data[product_code] = (f"/{product_code}", day_ticker)

    if not root_data:
        logger.warning("%s: no roots with valid contracts; skipping", exchange_prefix)
        return {
            "exchange": exchange_prefix, "rows_fetched": 0,
            "rows_written": 0, "days_processed": 0, "error": None,
        }

    # Step 2: enumerate trading days actually present in S3 (one listing).
    try:
        available = flat_client.available_futures_dates(exchange_prefix, start_date, end_date)
    except Exception as exc:
        logger.error("available_futures_dates failed for %s: %s", exchange_prefix, exc)
        return {
            "exchange": exchange_prefix, "rows_fetched": 0,
            "rows_written": 0, "days_processed": 0, "error": str(exc),
        }

    if not available:
        logger.warning("%s: no flat files in S3 for %s–%s",
                       exchange_prefix, start_date, end_date)
        return {
            "exchange": exchange_prefix, "rows_fetched": 0,
            "rows_written": 0, "days_processed": 0, "error": None,
        }

    logger.info("%s: %d trading days in S3 (%s → %s)",
                exchange_prefix, len(available),
                available[0].file_date, available[-1].file_date)

    # Step 3: iterate days — one download, split across all roots.
    rows_fetched = rows_written = days_ok = days_skipped = 0
    combined_batch: list[dict] = []

    for fi in available:
        d = fi.file_date

        # Build {ticker: (root, symbol)} for every root that has a front-month
        # contract on this day. Multiple roots can share an exchange file but
        # each has a distinct ticker (ESM5, NQM5, YMM5, …).
        ticker_to_root: dict[str, tuple[str, str]] = {}
        for product_code, (symbol, day_ticker) in root_data.items():
            t = day_ticker.get(d)
            if t:
                ticker_to_root[t] = (product_code, symbol)

        if not ticker_to_root:
            days_skipped += 1
            continue

        # One S3 download covers all roots for this exchange on this day.
        try:
            df = flat_client.download_futures_minute_aggs(
                exchange_prefix, d, tickers=list(ticker_to_root),
            )
        except Exception as exc:
            logger.warning("%s: download failed for %s: %s", exchange_prefix, d, exc)
            days_skipped += 1
            continue

        if df.empty:
            days_skipped += 1
            continue

        # Split by ticker → per-root bars.
        day_rows = 0
        for ticker, (_, symbol) in ticker_to_root.items():
            root_df = df[df["ticker"] == ticker]
            if root_df.empty:
                continue
            bars = _df_to_bars(root_df, symbol)
            combined_batch.extend(bars)
            day_rows += len(bars)

        if day_rows == 0:
            days_skipped += 1
            continue

        rows_fetched += day_rows
        days_ok += 1

        if len(combined_batch) >= batch_size:
            if not dry_run and sink is not None:
                rows_written += sink.write_batch(combined_batch)
            else:
                rows_written += len(combined_batch)
            combined_batch = []

        if days_ok % 100 == 0:
            logger.info(
                "  %s  %s  days=%d  fetched=%d  written=%d  (%.0fs)",
                exchange_prefix, d, days_ok, rows_fetched, rows_written,
                time.monotonic() - t0,
            )

    # Flush remainder.
    if combined_batch:
        if not dry_run and sink is not None:
            rows_written += sink.write_batch(combined_batch)
        else:
            rows_written += len(combined_batch)

    elapsed = time.monotonic() - t0
    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        "%s%s done: days=%d  skipped=%d  fetched=%d  written=%d  %.1fs",
        label, exchange_prefix, days_ok, days_skipped,
        rows_fetched, rows_written, elapsed,
    )
    return {
        "exchange": exchange_prefix,
        "rows_fetched": rows_fetched,
        "rows_written": rows_written,
        "days_processed": days_ok,
        "error": None,
    }


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_backfill(
    roots: list[str],
    *,
    start_year: int,
    end_year: int,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> dict:
    """Group roots by exchange, then process each exchange sequentially."""
    from app.config import settings
    from app.providers.polygon_flatfiles import FUTURES_EXCHANGE_PREFIXES

    api_key = settings.polygon_api_key or ""
    if not api_key.strip():
        logger.error("POLYGON_API_KEY is not set.")
        sys.exit(1)
    if not (settings.polygon_s3_access_key_id and settings.polygon_s3_secret_access_key):
        logger.error("POLYGON_S3_ACCESS_KEY_ID / POLYGON_S3_SECRET_ACCESS_KEY not set.")
        sys.exit(1)
    if not dry_run and not (settings.stock_lake_bucket or "").strip():
        logger.error("STOCK_LAKE_BUCKET is not set. Pass --dry-run or set the bucket.")
        sys.exit(1)

    start_date = date(start_year, 1, 1)
    end_date   = date(end_year, 12, 31)

    # Group roots by exchange prefix — determines which flat file to download.
    exchange_roots: dict[str, list[str]] = defaultdict(list)
    unknown: list[str] = []
    for root in roots:
        code = root.lstrip("/").upper()
        prefix = FUTURES_EXCHANGE_PREFIXES.get(code)
        if prefix is None:
            logger.error("%s: no exchange prefix mapping; skipping", code)
            unknown.append(code)
        else:
            exchange_roots[prefix].append(code)

    logger.info(
        "=== Polygon futures flat-files backfill %s–%s | %s ===",
        start_date, end_date,
        "DRY RUN" if dry_run else "writing to futures.polygon_futures",
    )
    for prefix, pfx_roots in sorted(exchange_roots.items()):
        logger.info("  %-30s %s", prefix + ":", " ".join(pfx_roots))

    from massive import RESTClient
    rest_client = RESTClient(
        api_key=api_key, connect_timeout=10, read_timeout=60, retries=3,
    )

    from app.providers.polygon_flatfiles import PolygonFlatFilesClient
    flat_client = PolygonFlatFilesClient.from_settings()

    sink = None
    if not dry_run:
        from app.services.futures.polygon_sink import PolygonFuturesSink
        sink = PolygonFuturesSink()
        logger.info("Sink ready: %s", sink.table_name)

    total_t0 = time.monotonic()
    all_results: list[dict] = []
    errors: list[str] = []

    # Exchanges run sequentially to avoid concurrent Iceberg metadata conflicts.
    for exchange_prefix, pfx_roots in sorted(exchange_roots.items()):
        result = process_exchange(
            exchange_prefix, pfx_roots,
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
            errors.append(exchange_prefix)

    if not dry_run and sink is not None:
        snap = sink.refresh_snapshot()
        logger.info("Snapshot after write: %s", snap)

    elapsed = time.monotonic() - total_t0
    grand_fetched = sum(r["rows_fetched"] for r in all_results)
    grand_written = sum(r["rows_written"] for r in all_results)
    grand_days   = sum(r["days_processed"] for r in all_results)

    summary = {
        "start_year": start_year,
        "end_year":   end_year,
        "dry_run":    dry_run,
        "exchanges_processed": len(all_results),
        "days_processed": grand_days,
        "rows_fetched": grand_fetched,
        "rows_written": grand_written,
        "elapsed_s":  round(elapsed, 1),
        "errors":     errors + unknown,
    }
    logger.info(
        "\n=== DONE  days=%d  fetched=%d  written=%d  errors=%d  elapsed=%.1fs ===",
        grand_days, grand_fetched, grand_written, len(errors), elapsed,
    )
    if errors:
        logger.warning("Exchanges with errors: %s", errors)
    if unknown:
        logger.warning("Unknown roots (no exchange mapping): %s", unknown)
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--root", dest="roots", nargs="+", required=True, metavar="ROOT",
        help="Product code(s) to backfill — grouped by exchange automatically. "
             "ES NQ GC CL ZB etc. (leading slash optional).",
    )
    p.add_argument(
        "--start-year", type=int, default=_DEFAULT_START_YEAR,
        help=f"First calendar year (default: {_DEFAULT_START_YEAR})",
    )
    p.add_argument(
        "--end-year", type=int, default=date.today().year,
        help="Last calendar year (default: current year)",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Iceberg write batch size in rows (default: {_DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Count bars but do not write to the lake.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
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
