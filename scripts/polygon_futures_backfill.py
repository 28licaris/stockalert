#!/usr/bin/env python3
"""Polygon futures historical 1-min backfill → futures.polygon_futures.

Pulls minute-bar data for CME futures contracts directly from Polygon's
futures aggregates API, stitches them into continuous roots (/ES, /NQ, …)
using front-month windows, and appends to the Iceberg lake table
`futures.polygon_futures`.

Architecture:
  1. Discover contracts   — list_futures_contracts(product_code=root)
  2. Build windows        — assign front-month date ranges per contract
  3. Pull bars            — list_futures_aggregates(ticker, resolution="1min", …)
  4. Write lake           — PolygonFuturesSink.write_batch()
  5. Report               — per-contract row count, total, elapsed

Re-runnable: Iceberg append is idempotent at the (symbol, timestamp)
identifier level; CH's ReplacingMergeTree dedupes on read.

Usage:
    poetry run python scripts/polygon_futures_backfill.py --root ES
    poetry run python scripts/polygon_futures_backfill.py --root ES --start-year 2022 --end-year 2025
    poetry run python scripts/polygon_futures_backfill.py --root ES --dry-run
    poetry run python scripts/polygon_futures_backfill.py --root ES NQ GC --batch-size 5000

Rate limits (Polygon REST):
    Free tier:    5 req/min  → use --rate-limit 13 (1 req/5s with margin)
    Starter+:     100 req/min → use --rate-limit 0.6 (default)

Environment:
    POLYGON_API_KEY   required
    STOCK_LAKE_BUCKET required (unless --dry-run)
    AWS_PROFILE       optional (default: stock-lake via config.py)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ── Bootstrap ──────────────────────────────────────────────────────────────
# Must be first — loads .env and normalises AWS_PROFILE before any imports
# that touch boto3 or PyIceberg.
import os as _os
_os.environ.setdefault("DJANGO_SETTINGS_MODULE", "")  # silence Django warnings if any

from dotenv import load_dotenv
load_dotenv()

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon_futures_backfill")

# Silence noisy third-party loggers
for _noisy in ("botocore", "boto3", "urllib3", "pyiceberg", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ── Constants ────────────────────────────────────────────────────────────────

# Maximum bars per API page. Polygon supports up to 50 000.
_PAGE_LIMIT = 50_000

# Polygon futures resolution string (the API rejects "minute" or "1m").
_RESOLUTION = "1min"

# Write to Iceberg in chunks of this many rows to bound memory.
_DEFAULT_BATCH_SIZE = 10_000

# Default rate-limit between API calls (seconds). 0.6s ≈ 100 req/min
# for paid tiers; use 13 for free tier (5 req/min with headroom).
_DEFAULT_RATE_LIMIT = 0.6


# ── Timestamp parsing ────────────────────────────────────────────────────────

def _parse_window_start(ws) -> Optional[datetime]:
    """Parse FuturesAgg.window_start to a UTC datetime.

    Polygon futures REST returns window_start as a Unix nanosecond integer.
    The SDK may return it as int, float, or occasionally an ISO string —
    handle all three.
    """
    if ws is None:
        return None
    if isinstance(ws, (int, float)):
        # Nanoseconds → seconds. Values > 1e15 are nanoseconds; < 1e13 are ms.
        ns = float(ws)
        if ns > 1e15:
            return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        if ns > 1e12:
            return datetime.fromtimestamp(ns / 1e3, tz=timezone.utc)
        return datetime.fromtimestamp(ns, tz=timezone.utc)
    try:
        s = str(ws).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ── Bar extraction ───────────────────────────────────────────────────────────

def _agg_to_bar(agg, symbol: str) -> Optional[dict]:
    """Convert a FuturesAgg to a bar dict for PolygonFuturesSink.

    Maps per-contract bars to the continuous root (symbol=/ES rather than
    ticker=ESZ5). Computes vwap from dollar_volume/volume when available.
    Returns None if the timestamp can't be parsed (defensive; logs a warning).
    """
    ts = _parse_window_start(getattr(agg, "window_start", None))
    if ts is None:
        logger.warning("_agg_to_bar: unparseable window_start=%r, skip", agg)
        return None

    vol = getattr(agg, "volume", None)
    dvol = getattr(agg, "dollar_volume", None)

    vwap: Optional[float] = None
    if vol and dvol and float(vol) > 0:
        vwap = float(dvol) / float(vol)

    return {
        "symbol":      symbol,
        "timestamp":   ts,
        "open":        getattr(agg, "open", None),
        "high":        getattr(agg, "high", None),
        "low":         getattr(agg, "low", None),
        "close":       getattr(agg, "close", None),
        "volume":      vol,
        "vwap":        vwap,
        "trade_count": getattr(agg, "transactions", None),
    }


# ── Per-contract pull ────────────────────────────────────────────────────────

def pull_contract(
    client,
    window,                # ContractWindow
    sink,                  # PolygonFuturesSink | None (dry-run)
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    rate_limit: float = _DEFAULT_RATE_LIMIT,
    dry_run: bool = False,
) -> dict:
    """Pull 1-min bars for one ContractWindow and write to the lake.

    Returns a result dict: {ticker, rows_fetched, rows_written, elapsed_s, error}.
    Never raises — errors are captured in the returned dict.
    """
    t0 = time.monotonic()
    ticker = window.ticker
    symbol = window.symbol
    # Polygon window_start params accept ISO 8601 (inclusive on both sides).
    gte = datetime.combine(window.front_start, datetime.min.time()).replace(tzinfo=timezone.utc).isoformat()
    lte = datetime.combine(window.front_end,   datetime.max.time()).replace(tzinfo=timezone.utc).isoformat()

    logger.info(
        "  %-8s  %s → %s  (continuous %s)",
        ticker, window.front_start, window.front_end, symbol,
    )

    rows_fetched = 0
    rows_written = 0
    batch: list[dict] = []
    last_api_call = 0.0

    try:
        for agg in client.list_futures_aggregates(
            ticker=ticker,
            resolution=_RESOLUTION,
            window_start_gte=gte,
            window_start_lte=lte,
            sort="asc",
            limit=_PAGE_LIMIT,
        ):
            # Rate-limit between pages: the SDK fetches the next page
            # lazily; we pace ourselves on each iteration.
            now = time.monotonic()
            gap = now - last_api_call
            if last_api_call > 0 and gap < rate_limit:
                time.sleep(rate_limit - gap)
            last_api_call = time.monotonic()

            bar = _agg_to_bar(agg, symbol)
            if bar is None:
                continue
            batch.append(bar)
            rows_fetched += 1

            if len(batch) >= batch_size:
                if not dry_run and sink is not None:
                    rows_written += sink.write_batch(batch)
                else:
                    rows_written += len(batch)
                batch = []

        # Flush remaining
        if batch:
            if not dry_run and sink is not None:
                rows_written += sink.write_batch(batch)
            else:
                rows_written += len(batch)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("  %s FAILED after %.1fs: %s", ticker, elapsed, exc)
        return {
            "ticker": ticker, "rows_fetched": rows_fetched,
            "rows_written": rows_written, "elapsed_s": elapsed, "error": str(exc),
        }

    elapsed = time.monotonic() - t0
    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        "  %s%-8s  fetched=%d  written=%d  %.1fs",
        label, ticker, rows_fetched, rows_written, elapsed,
    )
    return {
        "ticker": ticker, "rows_fetched": rows_fetched,
        "rows_written": rows_written, "elapsed_s": elapsed, "error": None,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def run_backfill(
    roots: list[str],
    *,
    start_year: int,
    end_year: int,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    rate_limit: float = _DEFAULT_RATE_LIMIT,
    dry_run: bool = False,
) -> dict:
    """Orchestrate the full backfill for one or more roots.

    Returns a summary dict with per-root and totals.
    Exits with a non-zero return code (sys.exit) if any root has a fatal
    configuration error (missing API key, no lake bucket in non-dry-run).
    """
    from app.config import settings
    from app.services.futures.contract_chain import (
        build_front_month_windows,
        discover_contracts,
    )

    # Pre-flight checks
    api_key = settings.polygon_api_key or ""
    if not api_key.strip():
        logger.error("POLYGON_API_KEY is not set. Cannot pull from Polygon.")
        sys.exit(1)

    if not dry_run and not (settings.stock_lake_bucket or "").strip():
        logger.error(
            "STOCK_LAKE_BUCKET is not set. Set it or pass --dry-run to test "
            "without writing."
        )
        sys.exit(1)

    start_date = date(start_year, 1, 1)
    end_date   = date(end_year, 12, 31)

    logger.info(
        "=== Polygon futures backfill %s — %s | roots=%s | %s ===",
        start_date, end_date, ", ".join(roots),
        "DRY RUN" if dry_run else f"writing to futures.polygon_futures",
    )

    # Build Polygon REST client (reused across all roots)
    from massive import RESTClient
    client = RESTClient(
        api_key=api_key,
        connect_timeout=10,
        read_timeout=60,
        retries=3,
    )

    # Build sink once (creates the table if absent) — skipped in dry-run
    sink = None
    if not dry_run:
        from app.services.futures.polygon_sink import PolygonFuturesSink
        sink = PolygonFuturesSink()
        logger.info("Sink ready: %s", sink.table_name)

    total_t0 = time.monotonic()
    grand_fetched = 0
    grand_written = 0
    all_results: list[dict] = []
    root_errors: list[str] = []

    for root in roots:
        # Polygon product_code doesn't include the slash (ES, not /ES)
        product_code = root.lstrip("/").upper()
        logger.info("\n── %s (%s) ──", root if root.startswith("/") else f"/{root}", product_code)

        # Step 1: Discover contracts from Polygon
        try:
            contracts = discover_contracts(
                client, product_code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            logger.error("discover_contracts failed for %s: %s", product_code, exc)
            root_errors.append(root)
            continue

        if not contracts:
            logger.warning("%s: no contracts found for %s–%s; skipping",
                           product_code, start_year, end_year)
            continue

        # Step 2: Build front-month windows
        windows = build_front_month_windows(contracts)
        logger.info("%s: %d contracts → %d windows", product_code, len(contracts), len(windows))

        # Step 3+4: Pull + write per window
        root_fetched = root_written = 0
        for window in windows:
            result = pull_contract(
                client, window, sink,
                batch_size=batch_size,
                rate_limit=rate_limit,
                dry_run=dry_run,
            )
            all_results.append(result)
            root_fetched += result["rows_fetched"]
            root_written += result["rows_written"]
            if result["error"]:
                root_errors.append(window.ticker)

        grand_fetched += root_fetched
        grand_written += root_written
        logger.info("%s done: fetched=%d written=%d", product_code, root_fetched, root_written)

    # Final snapshot refresh
    if not dry_run and sink is not None:
        snap = sink.refresh_snapshot()
        logger.info("Snapshot after write: %s", snap)

    elapsed = time.monotonic() - total_t0
    summary = {
        "roots": roots,
        "start_year": start_year,
        "end_year": end_year,
        "dry_run": dry_run,
        "contracts_processed": len(all_results),
        "rows_fetched": grand_fetched,
        "rows_written": grand_written,
        "elapsed_s": round(elapsed, 1),
        "errors": root_errors,
    }

    logger.info(
        "\n=== DONE  fetched=%d  written=%d  errors=%d  elapsed=%.1fs ===",
        grand_fetched, grand_written, len(root_errors), elapsed,
    )
    if root_errors:
        logger.warning("Contracts/roots with errors: %s", root_errors)

    return summary


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--root", dest="roots", nargs="+", required=True,
        metavar="ROOT",
        help="CME product code(s) to backfill: ES NQ GC CL (slash optional)",
    )
    p.add_argument(
        "--start-year", type=int,
        default=date.today().year - 4,
        help="First calendar year to pull (default: 4 years ago)",
    )
    p.add_argument(
        "--end-year", type=int,
        default=date.today().year,
        help="Last calendar year to pull (default: current year)",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Iceberg write batch size in rows (default: {_DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--rate-limit", type=float, default=_DEFAULT_RATE_LIMIT,
        metavar="SECONDS",
        help=(
            f"Min seconds between Polygon API calls (default: {_DEFAULT_RATE_LIMIT}). "
            "Use 13 for free tier (5 req/min)."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Discover contracts and count bars but do not write to the lake.",
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
        rate_limit=args.rate_limit,
        dry_run=args.dry_run,
    )

    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
