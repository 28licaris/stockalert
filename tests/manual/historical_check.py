import asyncio
from datetime import datetime, timedelta, timezone
from app.config import settings
from app.providers.alpaca_provider import AlpacaProvider
from alpaca.data.enums import DataFeed

async def test_historical(feed: DataFeed = DataFeed.SIP):
    # Validate credentials first
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        print("❌ Error: Alpaca API credentials not found in environment variables")
        print("Please set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        return
    
    print(f"Using feed: {feed.value}")
    print(f"API Key: {settings.alpaca_api_key[:8]}...")
    
    # Create provider directly with specific config
    provider = AlpacaProvider(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        feed=feed
    )
    
    # Test parameters
    symbol = "SPY"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)  # Last 7 days
    
    print(f"\nFetching historical data for {symbol}")
    print(f"Start: {start}")
    print(f"End: {end}")
    print("-" * 50)
    
    try:
        df = await provider.historical_df(symbol, start, end, timeframe="1Min")
        
        if df.empty:
            print("❌ No data returned")
        else:
            print(f"✅ Success! Retrieved {len(df)} bars")
            print(f"\nFirst 5 bars:")
            print(df.head())
            print(f"\nLast 5 bars:")
            print(df.tail())
            print(f"\nColumns: {df.columns.tolist()}")
            print(f"Date range: {df.index.min()} to {df.index.max()}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_historical())
