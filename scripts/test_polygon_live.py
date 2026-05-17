#!/usr/bin/env python3
"""
Smoke test for the Polygon (Massive) provider.

Verifies the two paths covered so far:

  1. REST historical bars via PolygonProvider.historical_df.
  2. WebSocket live 1-minute aggregate bars via PolygonProvider.subscribe_bars.

Requires in .env:
  POLYGON_API_KEY   (REST + WebSocket)
  POLYGON_FEED      (defaults to socket.polygon.io)
  POLYGON_MARKET    (defaults to stocks)

Run from the project root (stockalert/stockalert):

  poetry run python scripts/test_polygon_live.py
  poetry run python scripts/test_polygon_live.py --symbol AAPL --days 1
  poetry run python scripts/test_polygon_live.py --skip-stream    # REST only

By default the stream test runs for 30 seconds (or until 3 bars arrive).
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

# Load .env before importing app config so POLYGON_* env vars are visible.
from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.providers.polygon_provider import PolygonProvider  # noqa: E402


def _build_provider() -> PolygonProvider:
    return PolygonProvider(
        api_key=settings.polygon_api_key,
        feed=settings.polygon_feed,
        market=settings.polygon_market,
        secure_ws=settings.polygon_secure_ws,
    )


async def _rest_check(provider: PolygonProvider, symbol: str, days: int) -> bool:
    print(f"1. REST historical {symbol} last {days} day(s) (1-minute aggregates)...")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        df = await provider.historical_df(symbol, start, end, timeframe="1Min")
    except Exception as e:
        print(f"   FAILED: {e}")
        return False

    if df.empty:
        print("   FAILED: Polygon returned no bars (check API tier / market hours).")
        return False

    print(f"   OK – {len(df)} bars; first={df.index[0]}  last={df.index[-1]}")
    print(f"   Sample row: {df.iloc[0].to_dict()}")
    return True


async def _stream_check(
    provider: PolygonProvider,
    symbol: str,
    *,
    duration_s: float,
    target_bars: int,
) -> bool:
    print(f"2. WebSocket stream {symbol} (AM.{symbol}) for up to {duration_s:.0f}s...")
    received: list[dict] = []
    done = asyncio.Event()

    async def on_bar(bar) -> None:
        received.append({
            "symbol": bar.symbol,
            "ts": bar.timestamp.isoformat(),
            "open": bar.open,
            "close": bar.close,
            "volume": bar.volume,
        })
        print(f"   bar #{len(received)}: {bar.symbol} @ {bar.timestamp.isoformat()} "
              f"o={bar.open} c={bar.close} v={bar.volume}")
        if len(received) >= target_bars:
            done.set()

    provider.subscribe_bars(on_bar, [symbol])

    try:
        await asyncio.wait_for(done.wait(), timeout=duration_s)
        print(f"   OK – received {len(received)} bars (target {target_bars}).")
        return True
    except asyncio.TimeoutError:
        if received:
            print(f"   OK – received {len(received)} bars in {duration_s:.0f}s.")
            return True
        print(
            "   No bars received within window. This is normal outside RTH for "
            "delayed-feed subscriptions. Try again during 09:30–16:00 ET."
        )
        return False
    finally:
        provider.unsubscribe_bars([symbol])
        provider.stop_stream()


async def main(symbol: str, days: int, skip_stream: bool, duration: float,
               target_bars: int) -> None:
    if not settings.polygon_api_key:
        print("Missing POLYGON_API_KEY in .env")
        return

    print(f"Provider: polygon  feed={settings.polygon_feed}  market={settings.polygon_market}")
    print(f"Effective stream={settings.effective_stream_provider}  "
          f"history={settings.effective_history_provider}")
    print()

    provider = _build_provider()

    rest_ok = await _rest_check(provider, symbol.upper(), days)

    if skip_stream:
        print("\nDone. Stream check skipped (--skip-stream).")
        return

    stream_ok = await _stream_check(
        provider, symbol.upper(),
        duration_s=duration,
        target_bars=target_bars,
    )

    print()
    print(f"REST   : {'PASS' if rest_ok else 'FAIL'}")
    print(f"STREAM : {'PASS' if stream_ok else 'FAIL'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Smoke test the Polygon provider.")
    p.add_argument("--symbol", default="SPY", help="Ticker symbol (default SPY)")
    p.add_argument("--days", type=int, default=1, help="REST lookback in days")
    p.add_argument("--skip-stream", action="store_true",
                   help="Skip WebSocket check (REST only)")
    p.add_argument("--duration", type=float, default=30.0,
                   help="Stream duration cap in seconds")
    p.add_argument("--target-bars", type=int, default=3,
                   help="Stop early when this many bars arrive")
    args = p.parse_args()
    asyncio.run(main(args.symbol, args.days, args.skip_stream,
                     args.duration, args.target_bars))
