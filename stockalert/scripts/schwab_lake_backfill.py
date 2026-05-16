#!/usr/bin/env python3
"""
Backfill the **S3 stock lake** with Schwab Market Data **pricehistory**
(REST), one Parquet object per **US/Eastern calendar date** for ``kind=minute``.

This is the S3 counterpart to ``schwab_seed_backfill.py`` (which writes
**ClickHouse** only). Rows are merged across all requested symbols for each
day so the S3 key stays unique (same layout as Polygon's lake writer).

Layout (see ``LakeArchiveWriter``)::

    raw/provider=schwab/kind=minute/year=YYYY/date=YYYY-MM-DD.parquet

**ClickHouse** is used only for ``lake_archive_watermarks`` (idempotent
resume / skip). No ``ohlcv_*`` inserts.

Examples::

    poetry run python scripts/schwab_lake_backfill.py \\
        --symbols seed --start 2026-04-01 --end yesterday

    poetry run python scripts/schwab_lake_backfill.py \\
        --symbols SPY,QQQ --start yesterday --end yesterday --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE / ".env", override=False)
load_dotenv(override=False)

from app.config import get_provider, settings  # noqa: E402
from app.data.seed_universe import SEED_SYMBOLS  # noqa: E402
from app.db import init_schema  # noqa: E402
from app.services.legacy.lake_archive import LakeArchiveWriter  # noqa: E402

logger = logging.getLogger(__name__)

# Hive-safe partition value; must match watermarks ``source`` column.
SCHWAB_LAKE_PROVIDER = "schwab"
_US_EASTERN = ZoneInfo("America/New_York")


def _parse_date(s: str) -> date:
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
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    if s in ("all", "*", ""):
        raise ValueError(
            "Use explicit symbols or 'seed'; 'all' is not supported for Schwab REST.",
        )
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


def _us_eastern_day_window(d: date) -> tuple[datetime, datetime]:
    """
    Inclusive window [start, end] for *calendar date* ``d`` in America/New_York.

    Schwab pricehistory with ``periodType=day`` aligns to US/Eastern. Using
    UTC midnight..midnight can make normalized ``startDate``/``endDate`` cross
    in ET and yield HTTP 400 ("Enddate ... is before startDate").
    """
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=_US_EASTERN)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return start, end


def _is_weekend_us_eastern(d: date) -> bool:
    """Saturday/Sunday in New York (noon avoids DST fold edge)."""
    noon = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=_US_EASTERN)
    return noon.weekday() >= 5


def _schwab_1m_to_lake_frame(df: pd.DataFrame, symbol: str, *, source: str) -> pd.DataFrame:
    """Schwab ``historical_df`` (DatetimeIndex) → ``LakeArchiveWriter`` minute schema."""
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
    sym_u = symbol.upper().strip()
    return pd.DataFrame(
        {
            "symbol": sym_u,
            "timestamp": clean["timestamp"],
            "open": clean["open"].astype("float64"),
            "high": clean["high"].astype("float64"),
            "low": clean["low"].astype("float64"),
            "close": clean["close"].astype("float64"),
            "volume": clean["volume"].astype("float64"),
            "vwap": 0.0,
            "trade_count": 0,
            "source": source,
        }
    )


def _iter_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError(f"end ({end}) before start ({start})")
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Schwab REST pricehistory → S3 stock lake (Parquet per US/Eastern day).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--symbols",
        default="seed",
        help="'seed' (100-name curated list) or comma-separated tickers.",
    )
    p.add_argument(
        "--start",
        type=_parse_date,
        required=True,
        help="Inclusive calendar start (interpreted as America/New_York date).",
    )
    p.add_argument(
        "--end",
        type=_parse_date,
        required=True,
        help="Inclusive calendar end (America/New_York date).",
    )
    p.add_argument(
        "--include-weekends",
        action="store_true",
        help="Request Sat/Sun too (default: skip NY weekends; usually empty for equities).",
    )
    p.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.05,
        help="Pause between symbol API calls within a day (default 0.05).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Bypass lake watermark short-circuit for each day.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


async def _async_main(args: argparse.Namespace) -> int:
    if not (settings.stock_lake_bucket or "").strip():
        print("FAIL: STOCK_LAKE_BUCKET is empty.", file=sys.stderr)
        return 2

    try:
        symbols = _resolve_symbols(args.symbols)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    if not symbols:
        print("FAIL: no symbols.", file=sys.stderr)
        return 2

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    await asyncio.to_thread(init_schema)
    provider = get_provider("schwab")
    writer = LakeArchiveWriter.from_settings()

    days = _iter_days(args.start, args.end)
    print(
        f"Schwab → S3 lake: provider={SCHWAB_LAKE_PROVIDER!r} kind=minute "
        f"{len(symbols)} symbol(s) × {len(days)} day(s) "
        f"({args.start} .. {args.end}) bucket={settings.stock_lake_bucket!r}",
    )

    ok = skip = err = 0
    for d in days:
        if not args.include_weekends and _is_weekend_us_eastern(d):
            print(f"  {d}: skip (weekend America/New_York)")
            continue

        t0, t1 = _us_eastern_day_window(d)
        parts: list[pd.DataFrame] = []
        for sym in symbols:
            try:
                raw = await provider.historical_df(sym, t0, t1, timeframe="1Min")
                canon = _schwab_1m_to_lake_frame(raw, sym, source=SCHWAB_LAKE_PROVIDER)
                if not canon.empty:
                    parts.append(canon)
            except Exception as e:
                logger.error("schwab_lake: %s %s: %s", sym, d, e)
            await asyncio.sleep(max(0.0, args.sleep_seconds))

        merged = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if args.dry_run:
            print(f"  {d}: rows={len(merged):>7} symbols_included={len(parts)}")
            continue

        try:
            res = await writer.write_day(
                merged,
                file_date=d,
                kind="minute",
                provider=SCHWAB_LAKE_PROVIDER,
                force=bool(args.force),
            )
            if res.status == "ok":
                ok += 1
                print(f"  {d}: ok bars={res.bars_written} key={res.s3_key}")
            elif res.status == "skipped":
                skip += 1
                print(f"  {d}: skipped ({res.error or 'empty or watermark'})")
            else:
                err += 1
                print(f"  {d}: {res.status} err={res.error}", file=sys.stderr)
        except Exception as e:
            err += 1
            logger.exception("schwab_lake: write_day %s failed: %s", d, e)
            print(f"  {d}: ERROR {e}", file=sys.stderr)

    if not args.dry_run:
        print(f"Done: ok={ok} skipped={skip} errors={err}")
    return 1 if err else 0


def main() -> None:
    args = _build_parser().parse_args()
    try:
        code = asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
