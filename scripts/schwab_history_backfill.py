#!/usr/bin/env python3
"""
v2 Schwab history backfill — pull 1-minute bars from Schwab REST
pricehistory and land them in `equities.schwab_universe` (Iceberg).
One Iceberg append per trading day, rows from N symbols merged into
a single canonical frame.

Schwab's pricehistory has a practical ~48-day lookback for 1-minute
bars, so this is typically run with `--days 48` to cover the maximum.

Idempotent-ish:
  - Iceberg `append` is fast and never fails on duplicates.
  - However, re-running for the same (day, symbol) set DOES create
    duplicate rows in equities.schwab_universe. For clean re-runs,
    delete the affected partitions manually before re-running (recipe
    in docs/architecture_v2/07_runbook.md — "Recover a known-bad
    partition"). For the nightly cron path, idempotency is enforced
    upstream by the gap-detection pre-scan in
    `app.services.ingest.nightly_schwab_refresh`.

Run:
    poetry run python scripts/schwab_history_backfill.py \\
        --symbols active --days 48

    # narrower scope:
    poetry run python scripts/schwab_history_backfill.py \\
        --symbols AAPL,MSFT --days 7
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.config import settings  # noqa: E402
from app.services.universe import resolve_universe_spec  # noqa: E402
from app.services.equities.sink import EquitiesIcebergSink  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("schwab-history-backfill")

SCHWAB_SOURCE_TAG = "schwab"
US_EASTERN = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────

def _resolve_symbols(spec: str) -> list[str]:
    s = (spec or "").strip().lower()
    if s in ("all", "*", ""):
        raise ValueError("Use 'active' or explicit symbols; 'all' is not supported for Schwab REST.")
    return resolve_universe_spec(spec)


def _us_eastern_day_window(d: date) -> tuple[datetime, datetime]:
    """Inclusive window for a single ET calendar day. See schwab_lake_backfill comments."""
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=US_EASTERN)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return start, end


def _is_weekend_us_eastern(d: date) -> bool:
    noon = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=US_EASTERN)
    return noon.weekday() >= 5


def _iter_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError(f"end ({end}) before start ({start})")
    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


# ─────────────────────────────────────────────────────────────────────
# Frame conversion
# ─────────────────────────────────────────────────────────────────────

def _schwab_1m_to_canonical(
    df: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    """
    Convert Schwab `historical_df()` output (DatetimeIndex'd OHLCV)
    into the canonical equities.*_universe / *_raw frame.

    Schwab's pricehistory does not return vwap or trade_count, so
    those columns are NaN — the sink converts NaN to NULL.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    x = df.reset_index()
    if "timestamp" not in x.columns:
        if len(x.columns) < 6:
            return pd.DataFrame()
        x = x.rename(columns={x.columns[0]: "timestamp"})

    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True)
    clean = x.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if clean.empty:
        return pd.DataFrame()

    return pd.DataFrame({
        "symbol": symbol.upper().strip(),
        "timestamp": clean["timestamp"],
        "open": clean["open"].astype("float64"),
        "high": clean["high"].astype("float64"),
        "low": clean["low"].astype("float64"),
        "close": clean["close"].astype("float64"),
        "volume": clean["volume"].astype("float64"),
        "vwap": pd.NA,           # Schwab doesn't return vwap
        "trade_count": pd.NA,    # Schwab doesn't return trade counts
        "source": SCHWAB_SOURCE_TAG,
    })


# ─────────────────────────────────────────────────────────────────────
# Main backfill
# ─────────────────────────────────────────────────────────────────────

async def run_backfill(
    *,
    symbols: list[str],
    start: date,
    end: date,
    sleep_seconds: float,
    include_weekends: bool,
    dry_run: bool,
) -> int:
    if not (settings.stock_lake_bucket or "").strip():
        print("FAIL: STOCK_LAKE_BUCKET is empty.", file=sys.stderr)
        return 2

    from app.config import get_provider
    provider = get_provider("schwab")

    sink = EquitiesIcebergSink.for_schwab_universe() if not dry_run else None

    days = _iter_days(start, end)
    logger.info(
        "Schwab → equities.schwab_universe: %d symbol(s) × %d day(s) "
        "(%s .. %s)  sleep=%.2fs",
        len(symbols), len(days), start, end, sleep_seconds,
    )

    ok_days = 0
    skipped_days = 0
    err_days = 0
    total_rows = 0

    for d in days:
        if not include_weekends and _is_weekend_us_eastern(d):
            logger.info("  %s: skip (weekend in America/New_York)", d)
            skipped_days += 1
            continue

        t0, t1 = _us_eastern_day_window(d)
        per_symbol_frames: list[pd.DataFrame] = []
        for sym in symbols:
            try:
                raw = await provider.historical_df(sym, t0, t1, timeframe="1Min")
                canonical = _schwab_1m_to_canonical(raw, sym)
                if not canonical.empty:
                    per_symbol_frames.append(canonical)
            except Exception as e:
                logger.error("  %s %s: API error: %s", d, sym, e)
            await asyncio.sleep(max(0.0, sleep_seconds))

        merged = pd.concat(per_symbol_frames, ignore_index=True) if per_symbol_frames else pd.DataFrame()

        if dry_run:
            logger.info(
                "  %s: dry-run rows=%-7d symbols_with_data=%d/%d",
                d, len(merged), len(per_symbol_frames), len(symbols),
            )
            continue

        if merged.empty:
            logger.info("  %s: no rows from any symbol", d)
            skipped_days += 1
            continue

        result = await sink.write(merged, file_date=d, kind="minute", provider="schwab")
        if result.status == "ok":
            ok_days += 1
            total_rows += result.bars_written
            logger.info(
                "  %s: ok rows=%-7d snapshot=%s",
                d, result.bars_written, result.metadata.get("snapshot_id_after"),
            )
        elif result.status == "skipped":
            skipped_days += 1
            logger.info("  %s: skipped reason=%s", d, result.metadata.get("reason"))
        else:
            err_days += 1
            logger.error("  %s: %s err=%s", d, result.status, result.error)

    logger.info(
        "Done: ok=%d skipped=%d errors=%d total_rows=%s",
        ok_days, skipped_days, err_days, f"{total_rows:,}",
    )
    return 1 if err_days else 0


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    s = s.strip().lower()
    if s == "yesterday":
        return (datetime.now(timezone.utc).date() - timedelta(days=1))
    if s == "today":
        return datetime.now(timezone.utc).date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--symbols",
        default="active",
        help="'active' for ClickHouse stream_universe, or comma-separated tickers.",
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--days", type=int, default=None,
                     help="Backfill the last N ET days ending at --end (or yesterday).")
    grp.add_argument("--start", type=_parse_date, default=None,
                     help="ET calendar start date (YYYY-MM-DD).")
    p.add_argument("--end", type=_parse_date, default=None,
                   help="ET calendar end date (default: yesterday in UTC).")
    p.add_argument("--sleep-seconds", type=float, default=0.05,
                   help="Sleep between per-symbol API calls (rate-limit cushion).")
    p.add_argument("--include-weekends", action="store_true",
                   help="Include Sat/Sun (usually empty for equities).")
    p.add_argument("--dry-run", action="store_true",
                   help="Pull from Schwab + canonicalize, but don't write to Iceberg.")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    end = args.end or (datetime.now(timezone.utc).date() - timedelta(days=1))
    if args.days is not None:
        start = end - timedelta(days=args.days - 1)
    elif args.start is not None:
        start = args.start
    else:
        # Default to Schwab's 48-day practical max
        start = end - timedelta(days=47)

    try:
        symbols = _resolve_symbols(args.symbols)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    if not symbols:
        print("FAIL: no symbols resolved.", file=sys.stderr)
        sys.exit(2)

    code = asyncio.run(run_backfill(
        symbols=symbols,
        start=start,
        end=end,
        sleep_seconds=args.sleep_seconds,
        include_weekends=args.include_weekends,
        dry_run=args.dry_run,
    ))
    sys.exit(code)


if __name__ == "__main__":
    main()
