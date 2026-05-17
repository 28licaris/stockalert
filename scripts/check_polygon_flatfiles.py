#!/usr/bin/env python3
"""
Smoke test the Polygon (Massive) Flat Files S3 connection.

Validates everything ``FlatFilesBackfillService`` will rely on, against the
real ``files.massive.com`` endpoint:

    1. ``available_dates`` listing (cheap, lists keys only)
    2. ``download_minute_aggs`` for the most recent listed date
    3. Symbol filter narrows the universe correctly
    4. ``download_day_aggs`` for the same date (daily flat file)

Reuses the production ``PolygonFlatFilesClient`` so a green run here means
the in-app code path will also work.

Requires in .env:
    POLYGON_S3_ACCESS_KEY_ID
    POLYGON_S3_SECRET_ACCESS_KEY
    POLYGON_S3_ENDPOINT       (default https://files.massive.com)
    POLYGON_S3_BUCKET         (default flatfiles)

Run from the project root (stockalert/stockalert):

    poetry run python scripts/check_polygon_flatfiles.py
    poetry run python scripts/check_polygon_flatfiles.py --symbols AAPL,MSFT,SPY
    poetry run python scripts/check_polygon_flatfiles.py --lookback-days 10
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.providers.polygon_flatfiles import (  # noqa: E402
    PolygonFlatFilesClient,
    PolygonFlatFilesError,
)


DEFAULT_SYMBOLS = ("AAPL", "MSFT", "SPY")


def _fail(msg: str, exc: Exception | None = None) -> None:
    print(f"FAIL: {msg}")
    if exc is not None:
        print(f"      cause: {exc}")
    sys.exit(1)


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def main(symbols: list[str], lookback_days: int) -> None:
    if not (settings.polygon_s3_access_key_id and settings.polygon_s3_secret_access_key):
        _fail("POLYGON_S3_ACCESS_KEY_ID / POLYGON_S3_SECRET_ACCESS_KEY missing from .env")

    print(f"Endpoint: {settings.polygon_s3_endpoint}")
    print(f"Bucket  : {settings.polygon_s3_bucket}")
    print(f"Key ID  : {settings.polygon_s3_access_key_id[:8]}...")
    print(f"Symbols : {','.join(symbols)}")
    print()

    try:
        client = PolygonFlatFilesClient.from_settings()
    except Exception as e:
        _fail("PolygonFlatFilesClient.from_settings()", e)

    # ── 1. available_dates over recent window ───────────────────────
    today = date.today()
    start = today - timedelta(days=lookback_days)
    print(f"1. available_dates  minute aggs  {start} .. {today}")
    try:
        files = client.available_dates(start, today, kind="minute")
    except PolygonFlatFilesError as e:
        _fail("available_dates", e)
    if not files:
        _fail(
            f"available_dates returned 0 entries in the last {lookback_days} days. "
            "Either the lookback is too short, your plan lacks flat-files access, "
            "or the bucket/endpoint is wrong."
        )
    print(f"   {len(files)} trading day(s) listed.  Most recent: "
          f"{files[-1].file_date}  size={_format_size(files[-1].size)}")
    for f in files[-3:]:
        print(f"     - {f.file_date}  {_format_size(f.size)}  key={f.key}")

    target = files[-1]
    target_date = target.file_date

    # ── 2. download_minute_aggs filtered by symbols ─────────────────
    print(f"2. download_minute_aggs  {target_date}  symbols={','.join(symbols)}")
    try:
        df = client.download_minute_aggs(target_date, symbols=symbols)
    except PolygonFlatFilesError as e:
        _fail("download_minute_aggs", e)
    if df.empty:
        _fail(
            f"download_minute_aggs returned an empty frame for {target_date} after "
            f"filtering to {symbols}. The file exists but none of your symbols "
            "appeared in it — check the symbol list."
        )
    bars_per_symbol = (
        df.groupby("ticker").size().sort_values(ascending=False)
    )
    print(f"   {len(df)} rows total across {df['ticker'].nunique()} symbol(s)")
    print(f"   time range: {df['timestamp'].min()} .. {df['timestamp'].max()}")
    print("   bars per symbol:")
    for sym, count in bars_per_symbol.items():
        print(f"     {sym:>6}  {count:>5}")
    print("   sample row:")
    head = df.head(1).iloc[0].to_dict()
    print(f"     {head}")

    # ── 3. unfiltered total count sanity check ──────────────────────
    print(f"3. download_minute_aggs  {target_date}  (no symbol filter)")
    try:
        df_all = client.download_minute_aggs(target_date)
    except PolygonFlatFilesError as e:
        _fail("download_minute_aggs (no filter)", e)
    if df_all.empty:
        _fail("download_minute_aggs returned empty frame even without filter")
    print(f"   {len(df_all):,} rows across {df_all['ticker'].nunique():,} tickers "
          f"(full US-equities tape for {target_date})")

    # ── 4. download_day_aggs same date ──────────────────────────────
    print(f"4. download_day_aggs  {target_date}  symbols={','.join(symbols)}")
    try:
        ddf = client.download_day_aggs(target_date, symbols=symbols)
    except PolygonFlatFilesError as e:
        _fail("download_day_aggs", e)
    if ddf.empty:
        print(
            "   (no daily file for that date — that's fine if it's the same day; "
            "daily files typically land ~1 hour after RTH close)"
        )
    else:
        print(f"   {len(ddf)} daily row(s) returned")
        print(f"     {ddf.head(1).iloc[0].to_dict()}")

    print()
    print(f"PASS: Polygon Flat Files reachable; downloaded {target_date} OK.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols to filter (default: AAPL,MSFT,SPY)",
    )
    p.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="How far back to look for the most recent available file (default 7)",
    )
    args = p.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not syms:
        _fail("--symbols cannot be empty")
    main(syms, args.lookback_days)
