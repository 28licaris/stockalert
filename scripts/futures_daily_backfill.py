#!/usr/bin/env python3
"""Futures DAILY history backfill — pull months of daily bars per continuous
root from Schwab (``frequencyType=daily``) into ``futures.schwab_futures_daily``.

Schwab caps *minute* history at ~48 days but serves years of *daily* — so this
is the deep-history daily tier that lets charts + Elliott Wave see real history
on futures (the 1-minute lake only covers ~48 days).

Idempotency: Iceberg append + merge-on-read identifier (symbol, timestamp), so a
re-run dedups on read. The window is chunked to stay inside Schwab's
periodType=month daily window.

Run:
    poetry run python scripts/futures_daily_backfill.py --symbols seed --months 12
    poetry run python scripts/futures_daily_backfill.py --symbols /ES --months 12 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.futures.schemas import FUTURES_SEED_ROOTS  # noqa: E402
from app.services.futures.sink import futures_daily_iceberg_sink  # noqa: E402
from scripts.schwab_history_backfill import _schwab_1m_to_canonical  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("futures-daily-backfill")

_CHUNK_DAYS = 150  # stay within Schwab's periodType=month daily windows


def _resolve_symbols(spec: str) -> list[str]:
    s = (spec or "").strip().lower()
    if s in ("seed", ""):
        return list(FUTURES_SEED_ROOTS)
    out: list[str] = []
    for tok in spec.split(","):
        t = tok.strip().upper()
        if t:
            out.append(t if t.startswith("/") else "/" + t)
    return out


async def _pull_root(provider, root: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Daily bars for one root over [start, end), chunked + deduped."""
    frames: list[pd.DataFrame] = []
    t0 = start
    while t0 < end:
        t1 = min(t0 + timedelta(days=_CHUNK_DAYS), end)
        df = await provider.historical_df(root, t0, t1, timeframe="1d")
        if df is not None and not df.empty:
            frames.append(df)
        await asyncio.sleep(0.3)  # be polite to Schwab REST
        t0 = t1
    if not frames:
        return pd.DataFrame()
    canon = _schwab_1m_to_canonical(pd.concat(frames), root)  # resolution-agnostic
    if canon.empty:
        return canon
    return canon.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")


async def run(symbols: list[str], months: int, dry_run: bool) -> None:
    from app.config import get_provider

    provider = get_provider("schwab")
    sink = futures_daily_iceberg_sink() if not dry_run else None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(months * 30.5))

    total = 0
    for root in symbols:
        try:
            canon = await _pull_root(provider, root, start, end)
        except Exception as exc:  # one bad root must not kill the run
            logger.warning("%s: FAILED: %s", root, exc)
            continue
        n = len(canon)
        if n == 0:
            logger.warning("%s: no daily bars returned", root)
            continue
        span = f"{canon['timestamp'].min().date()} → {canon['timestamp'].max().date()}"
        if dry_run:
            logger.info("%s: %d daily bars (%s) [dry-run]", root, n, span)
            continue
        res = await sink.write(canon, file_date=date.today(), kind="day", provider="schwab")
        logger.info("%s: wrote %d daily bars (%s) status=%s", root, n, span, res.status)
        total += res.bars_written
    logger.info("DONE: %d roots, %d daily rows written", len(symbols), total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="seed", help="'seed' or CSV of roots (/ES,/GC)")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    syms = _resolve_symbols(args.symbols)
    logger.info("Futures daily backfill: %d roots, %d months", len(syms), args.months)
    asyncio.run(run(syms, args.months, args.dry_run))


if __name__ == "__main__":
    main()
