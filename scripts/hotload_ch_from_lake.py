#!/usr/bin/env python3
"""
Hot-load ClickHouse `stocks.ohlcv_1m` from the v2 lake.

Per universe symbol:
  1. Read deep history (~5y) from `equities.polygon_adjusted`
  2. Read recent (~48d) from `equities.schwab_universe`
  3. Merge, dedupe by timestamp (polygon wins for overlap)
  4. Bulk-insert into CH `stocks.ohlcv_1m`

By default TRUNCATEs the table first for a clean rebuild — re-running
without --no-truncate produces identical state. Designed for the
post-cutover one-shot universe load + any future "rebuild from lake"
operation (CH disk wipe, schema rebuild, etc.).

Usage:
  poetry run python scripts/hotload_ch_from_lake.py
  poetry run python scripts/hotload_ch_from_lake.py --symbols AAPL,NVDA,MSFT
  poetry run python scripts/hotload_ch_from_lake.py --parallelism 16 --no-truncate
  poetry run python scripts/hotload_ch_from_lake.py --dry-run
"""
from __future__ import annotations

import argparse
import concurrent.futures
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.db.client import get_client  # noqa: E402
from app.services.equities.schemas import equities_table_id  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402

log = logging.getLogger(__name__)


# Schwab universe table holds the last ~48d. 60d gives safety margin
# for the (polygon last commit) ↔ (schwab live tip) handoff.
SCHWAB_LOOKBACK_DAYS = 60

CH_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "version",
]


@dataclass
class SymbolResult:
    symbol: str
    polygon_rows: int = 0
    schwab_rows: int = 0
    inserted: int = 0
    wall_s: float = 0.0
    error: str | None = None


def load_universe_symbols(ch_client) -> list[str]:
    """Active real symbols (excluding test pollution) from stream_universe."""
    rows = ch_client.query(
        "SELECT symbol FROM stocks.stream_universe FINAL "
        "WHERE is_active = 1 "
        "  AND symbol NOT LIKE '__TEST%' "
        "  AND symbol NOT LIKE 'FAKE%' "
        "ORDER BY symbol"
    ).result_rows
    return [r[0] for r in rows]


def _arrow_to_rows(arr, symbol: str, source_tag: str) -> list[list]:
    """Convert PyArrow Table → list-of-lists matching CH_COLUMNS ordering.

    Handles the Polygon-flat-files quirks:
      - volume can be fractional (float in polygon_raw schema) → keep float
      - trade_count can be fractional → round to int for UInt32
      - vwap can be NULL → default 0.0
    """
    if arr.num_rows == 0:
        return []
    out: list[list] = []
    cols = arr.to_pydict()
    for i in range(arr.num_rows):
        out.append([
            symbol,
            cols["timestamp"][i],
            float(cols["open"][i]),
            float(cols["high"][i]),
            float(cols["low"][i]),
            float(cols["close"][i]),
            float(cols["volume"][i]) if cols["volume"][i] is not None else 0.0,
            float(cols["vwap"][i]) if cols["vwap"][i] is not None else 0.0,
            int(round(cols["trade_count"][i])) if cols["trade_count"][i] is not None else 0,
            source_tag,
            1,  # version (ReplacingMergeTree)
        ])
    return out


def load_one_symbol(
    symbol: str,
    catalog,
    schwab_since: datetime,
    dry_run: bool,
) -> SymbolResult:
    """Read both lake tables for one symbol + insert deduped into CH."""
    started = time.time()
    result = SymbolResult(symbol=symbol)
    try:
        # Per-thread catalog handles for clean isolation.
        adj_table = catalog.load_table(equities_table_id("polygon_adjusted"))
        schwab_table = catalog.load_table(equities_table_id("schwab_universe"))

        polygon_arr = adj_table.scan(
            row_filter=f"symbol = '{symbol}'"
        ).to_arrow()
        schwab_arr = schwab_table.scan(
            row_filter=(
                f"symbol = '{symbol}' "
                f"AND timestamp >= '{schwab_since.isoformat()}'"
            )
        ).to_arrow()
        result.polygon_rows = polygon_arr.num_rows
        result.schwab_rows = schwab_arr.num_rows

        polygon_rows = _arrow_to_rows(polygon_arr, symbol, "silver-polygon")
        schwab_rows = _arrow_to_rows(schwab_arr, symbol, "silver-schwab")

        # Dedupe by timestamp — polygon wins for overlap (more authoritative
        # split-adjusted), schwab covers the recent tip not yet in polygon.
        seen_ts: set = set()
        merged: list[list] = []
        for r in polygon_rows:
            ts = r[1]
            seen_ts.add(ts)
            merged.append(r)
        for r in schwab_rows:
            ts = r[1]
            if ts not in seen_ts:
                merged.append(r)

        if dry_run:
            log.info(
                "  [DRY] %s: polygon=%d schwab=%d merged=%d",
                symbol, result.polygon_rows, result.schwab_rows, len(merged),
            )
            result.inserted = len(merged)
        elif merged:
            # Chunk by year — stocks.ohlcv_1m partitions by toYYYYMM(timestamp),
            # so 20yr of data = 240 partitions per INSERT block. CH's default
            # max_partitions_per_insert_block=100 caps each insert at ~8yr. We
            # split per calendar year (12 partitions each) for safe headroom.
            # The timestamp column is index 1 in CH_COLUMNS.
            ch = get_client()
            chunks: dict[int, list[list]] = {}
            for r in merged:
                ts = r[1]
                # Polygon timestamps come back as pandas.Timestamp / datetime
                # via PyArrow; both expose .year.
                year = ts.year if hasattr(ts, "year") else int(str(ts)[:4])
                chunks.setdefault(year, []).append(r)
            for year in sorted(chunks):
                ch.insert("stocks.ohlcv_1m", chunks[year], column_names=CH_COLUMNS)
            result.inserted = len(merged)
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        log.exception("  ✗ %s failed", symbol)

    result.wall_s = time.time() - started
    if not result.error:
        log.info(
            "  ✓ %s: polygon=%d schwab=%d inserted=%d wall=%.1fs",
            symbol, result.polygon_rows, result.schwab_rows,
            result.inserted, result.wall_s,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list. Omit = all active real symbols from CH stream_universe.",
    )
    p.add_argument(
        "--parallelism", type=int, default=8,
        help="Per-symbol worker concurrency. CH inserts + PyIceberg reads are "
             "thread-safe; bumping helps when network/lake is the bottleneck.",
    )
    p.add_argument(
        "--no-truncate", action="store_true",
        help="Skip the initial TRUNCATE — additive load (may create duplicates "
             "via ReplacingMergeTree version-based dedup at merge time).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Read lake + count rows; do NOT insert into CH.",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    ch = get_client()
    catalog = get_catalog()

    # Resolve universe.
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_universe_symbols(ch)
    log.info("Hot-load target: %d symbols", len(symbols))

    schwab_since = datetime.now(timezone.utc) - timedelta(days=SCHWAB_LOOKBACK_DAYS)
    log.info("Schwab window: from %s (last %dd)", schwab_since.isoformat(), SCHWAB_LOOKBACK_DAYS)

    if not args.no_truncate and not args.dry_run:
        log.warning("TRUNCATE stocks.ohlcv_1m — wiping ALL existing rows")
        ch.command("TRUNCATE TABLE stocks.ohlcv_1m")
        log.info("TRUNCATE done")
    elif args.no_truncate:
        log.info("Skipping TRUNCATE (--no-truncate)")
    elif args.dry_run:
        log.info("Skipping TRUNCATE (--dry-run)")

    started = time.time()
    results: list[SymbolResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallelism) as pool:
        futures = {
            pool.submit(load_one_symbol, sym, catalog, schwab_since, args.dry_run): sym
            for sym in symbols
        }
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    wall = time.time() - started

    # Summary
    ok = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]
    total_inserted = sum(r.inserted for r in ok)
    total_polygon = sum(r.polygon_rows for r in ok)
    total_schwab = sum(r.schwab_rows for r in ok)

    log.info("")
    log.info("─── Summary ───")
    log.info("  symbols ok:   %d / %d", len(ok), len(results))
    log.info("  symbols fail: %d", len(failed))
    log.info("  rows polygon: %s", f"{total_polygon:,}")
    log.info("  rows schwab:  %s", f"{total_schwab:,}")
    log.info("  rows inserted: %s", f"{total_inserted:,}")
    log.info("  wall: %.1fs (%.1f min)", wall, wall / 60)
    if failed:
        log.error("FAILURES:")
        for r in failed:
            log.error("  %s: %s", r.symbol, r.error)
        return 1

    if not args.dry_run:
        # Cross-check: CH count
        ch_count = ch.query("SELECT count(*) FROM stocks.ohlcv_1m").result_rows[0][0]
        ch_symbols = ch.query("SELECT uniqExact(symbol) FROM stocks.ohlcv_1m").result_rows[0][0]
        log.info("")
        log.info("  CH post-load: %s rows / %d symbols", f"{ch_count:,}", ch_symbols)

    return 0


if __name__ == "__main__":
    sys.exit(main())
