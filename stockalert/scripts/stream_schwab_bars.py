#!/usr/bin/env python3
"""
Stream live Schwab chart bars (CHART_EQUITY), print them, and persist to ClickHouse.

Run from project root (stockalert/stockalert):
  poetry run python scripts/stream_schwab_bars.py --symbols SPY,AAPL

Pass --no-db to skip the ClickHouse writes (print only).
"""

import argparse
import asyncio
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from app.config import settings
from app.db import get_bar_batcher, init_schema, ping, reset_bar_batcher
from app.providers.schwab_provider import SchwabProvider


async def main(symbols: list[str], persist: bool) -> None:
    if not settings.schwab_client_id or not settings.schwab_client_secret:
        raise SystemExit("Missing SCHWAB_CLIENT_ID or SCHWAB_CLIENT_SECRET in .env")

    refresh_token = settings.get_schwab_refresh_token()
    if not refresh_token:
        raise SystemExit(
            "Missing Schwab refresh token (set SCHWAB_REFRESH_TOKEN in .env or run scripts/schwab_get_refresh_token.py)"
        )

    if persist:
        if not ping():
            raise SystemExit(
                f"ClickHouse not reachable at {settings.clickhouse_host}:{settings.clickhouse_port}. "
                "Start it (e.g. `docker compose --profile ch up -d`) or pass --no-db."
            )
        init_schema()
        await get_bar_batcher().start()
        print(
            f"ClickHouse OK ({settings.clickhouse_host}:{settings.clickhouse_port}/{settings.clickhouse_database}); "
            "batched inserts into ohlcv_1m every 5s.",
            flush=True,
        )

    provider = SchwabProvider(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        refresh_token=refresh_token,
        callback_url=settings.schwab_callback_url or None,
        base_url=settings.schwab_base_url,
        refresh_token_file=settings.schwab_refresh_token_file or None,
    )

    try:
        await provider._ensure_token()
        await provider._get_user_principals()
    except RuntimeError as e:
        raise SystemExit(str(e)) from None

    source_tag = (settings.data_source_tag or "").strip() or settings.data_provider

    async def on_bar(bar) -> None:
        ts = getattr(bar, "timestamp", None) or getattr(bar, "ts", None)
        ts_s = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        print(
            f"{ts_s} {bar.ticker} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}",
            flush=True,
        )
        if persist and isinstance(ts, datetime):
            try:
                await get_bar_batcher().add(
                    {
                        "symbol": bar.ticker,
                        "timestamp": ts,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(getattr(bar, "volume", 0) or 0),
                        "source": source_tag,
                    }
                )
            except Exception as e:
                print(f"  ! persist error: {e}", flush=True)

    provider.subscribe_bars(on_bar, symbols)

    print(f"Streaming CHART_EQUITY bars for {symbols}. Ctrl+C to stop.", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        provider.stop_stream()
        if persist:
            await get_bar_batcher().stop()
            reset_bar_batcher()
            print("Flushed remaining bars to ClickHouse.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream live Schwab chart bars; print and persist to ClickHouse")
    parser.add_argument("--symbols", default="SPY", help="Comma-separated symbols (default: SPY)")
    parser.add_argument("--no-db", action="store_true", help="Print only; do not write to ClickHouse")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    asyncio.run(main(symbols, persist=not args.no_db))
