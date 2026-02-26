#!/usr/bin/env python3
"""
Test the real Schwab API with your credentials.

Requires in .env (or environment):
  SCHWAB_CLIENT_ID
  SCHWAB_CLIENT_SECRET
  SCHWAB_REFRESH_TOKEN   <-- from one-time OAuth (see scripts/schwab_get_refresh_token.py)
  SCHWAB_CALLBACK_URL    (only needed for getting the refresh token)

Run from project root (stockalert/stockalert):
  poetry run python scripts/test_schwab_live.py

Or with symbol and days:
  poetry run python scripts/test_schwab_live.py --symbol AAPL --days 2
"""
import asyncio
import argparse
from datetime import datetime, timedelta, timezone

# Load .env and app config
from dotenv import load_dotenv
load_dotenv()

from app.config import settings
from app.providers.schwab_provider import SchwabProvider


async def main(symbol: str, days: int) -> None:
    if not settings.schwab_client_id or not settings.schwab_client_secret:
        print("Missing SCHWAB_CLIENT_ID or SCHWAB_CLIENT_SECRET in .env")
        return
    if not settings.schwab_refresh_token:
        print("Missing SCHWAB_REFRESH_TOKEN in .env")
        print("Run scripts/schwab_get_refresh_token.py once to get a refresh token, then add it to .env")
        return

    provider = SchwabProvider(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        refresh_token=settings.schwab_refresh_token,
        callback_url=settings.schwab_callback_url or None,
        base_url=settings.schwab_base_url,
    )

    print("1. Getting access token (refresh_token exchange)...")
    try:
        token = await provider._ensure_token()
        print(f"   OK – token received ({token[:20]}...)")
    except Exception as e:
        print(f"   FAILED: {e}")
        return

    print("2. Getting user principals (streamer connection info)...")
    try:
        principals = await provider._get_user_principals()
        print(f"   OK – streamer_url present: {bool(provider._streamer_url)}")
        ids = provider._streamer_ids()
        if ids.get("SchwabClientCustomerId"):
            print(f"   SchwabClientCustomerId: {ids['SchwabClientCustomerId'][:8]}...")
    except Exception as e:
        print(f"   FAILED: {e}")
        return

    print(f"3. Fetching historical bars for {symbol} (last {days} day(s), 1-min)...")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        df = await provider.historical_df(symbol, start, end, timeframe="1Min")
        if df.empty:
            print("   No bars returned (market closed or symbol/range issue).")
        else:
            print(f"   OK – {len(df)} bars")
            print(df.head(10).to_string())
            print("   ...")
            print(df.tail(5).to_string())
    except Exception as e:
        print(f"   FAILED: {e}")
        return

    print("\nAll checks passed. Your Schwab keys and refresh token are working.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Schwab API with real credentials")
    parser.add_argument("--symbol", default="SPY", help="Symbol for historical data (default: SPY)")
    parser.add_argument("--days", type=int, default=1, help="Days of history (default: 1)")
    args = parser.parse_args()
    asyncio.run(main(args.symbol, args.days))
