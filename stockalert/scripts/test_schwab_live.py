#!/usr/bin/env python3
"""
Test the real Schwab API with your credentials.

Requires in .env: SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET.
Refresh token: in SCHWAB_REFRESH_TOKEN or in token file (data/.schwab_refresh_token).
  Run scripts/schwab_get_refresh_token.py once; it writes the token to the file so you don't need to add it to .env.
  SCHWAB_CALLBACK_URL is only needed when running the get-refresh-token script.

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
    refresh_token = settings.get_schwab_refresh_token()
    if not refresh_token:
        print("Missing Schwab refresh token (set SCHWAB_REFRESH_TOKEN in .env or run scripts/schwab_get_refresh_token.py to write token file)")
        return

    provider = SchwabProvider(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        refresh_token=refresh_token,
        callback_url=settings.schwab_callback_url or None,
        base_url=settings.schwab_base_url,
        refresh_token_file=settings.schwab_refresh_token_file or None,
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
        print(f"   WARNING: User preference unavailable ({e}); streamer will be skipped. Continuing with Market Data and historical tests.")

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

    # 4–11: Market Data REST endpoints (each step logs OK/FAILED, script continues)
    print("4. GET /quotes (list)...")
    try:
        data = await provider.get_quotes([symbol])
        n = len(data) if isinstance(data, dict) else 0
        print(f"   OK – {n} quote(s)" if n else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("5. GET /{symbol_id}/quotes (single)...")
    try:
        data = await provider.get_quote(symbol)
        has_quote = isinstance(data, dict) and (data.get("quote") or data.get("symbol") or len(data) > 0)
        print(f"   OK – single quote" if has_quote else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("6. GET /chains (option chain)...")
    try:
        data = await provider.get_option_chains(symbol, strikeCount=2)
        has_chain = isinstance(data, dict) and (data.get("callExpDateMap") or data.get("putExpDateMap") or len(data) > 0)
        print(f"   OK – option chain" if has_chain else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("7. GET /expirationchain...")
    try:
        data = await provider.get_expiration_chain(symbol)
        has_exp = isinstance(data, dict) and (data.get("expirationList") or data.get("symbol") or len(data) > 0)
        print(f"   OK – expiration chain" if has_exp else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("8. GET /movers/{symbol_id} ($SPX)...")
    try:
        data = await provider.get_movers("$SPX")
        has_movers = isinstance(data, list) or (isinstance(data, dict) and len(data) > 0)
        n = len(data) if isinstance(data, list) else (len(data) if isinstance(data, dict) else 0)
        print(f"   OK – movers (n={n})" if has_movers else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("9. GET /markets (all market hours)...")
    try:
        data = await provider.get_market_hours()
        has_hours = isinstance(data, dict) and len(data) > 0
        print(f"   OK – market hours" if has_hours else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("10. GET /markets/equity (single market)...")
    try:
        data = await provider.get_market_hours("equity")
        has_hours = isinstance(data, dict) and len(data) > 0
        print(f"   OK – equity hours" if has_hours else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("11. GET /instruments (symbol-search)...")
    try:
        data = await provider.get_instruments([symbol], "symbol-search")
        has_instr = isinstance(data, dict) and (data.get(symbol) or data.get("instruments") or len(data) > 0)
        print(f"   OK – instruments" if has_instr else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("12. GET /instruments/{cusip} (AAPL CUSIP 037833100)...")
    try:
        data = await provider.get_instrument("037833100")
        has_instr = isinstance(data, dict) and (data.get("cusip") or data.get("symbol") or len(data) > 0)
        print(f"   OK – instrument by CUSIP" if has_instr else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("13. Trader API – GET /accounts/accountNumbers...")
    try:
        data = await provider.get_account_numbers()
        items = data if isinstance(data, list) else (data.get("accountNumbers") or data.get("accounts") or [])
        n = len(items) if isinstance(items, list) else 0
        print(f"   OK – {n} account number(s)" if n else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("14. Trader API – GET /accounts (balances/positions)...")
    try:
        data = await provider.get_accounts()
        has_accounts = isinstance(data, dict) and (data.get("accounts") or data.get("securitiesAccount") or len(data) > 0)
        print(f"   OK – accounts data" if has_accounts else "   OK – empty response")
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\nAll steps completed. Review any FAILED lines above.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Schwab API with real credentials")
    parser.add_argument("--symbol", default="SPY", help="Symbol for historical data (default: SPY)")
    parser.add_argument("--days", type=int, default=1, help="Days of history (default: 1)")
    args = parser.parse_args()
    asyncio.run(main(args.symbol, args.days))
