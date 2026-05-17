import asyncio
from datetime import datetime
from app.config import settings
from app.providers.alpaca_provider import AlpacaProvider
from alpaca.data.enums import DataFeed

async def test_stream(feed: DataFeed = DataFeed.IEX):
    """Test live streaming data from Alpaca"""
    
    # Validate credentials
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        print("❌ Error: Alpaca API credentials not found")
        return
    
    print(f"Using feed: {feed.value}")
    print(f"API Key: {settings.alpaca_api_key[:8]}...")
    
    # Create provider
    provider = AlpacaProvider(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        feed=feed
    )
    
    # Counter for received bars
    bar_count = 0
    max_bars = 5  # Stop after receiving 5 bars
    
    async def on_bar(bar):
        """Callback for each bar received"""
        nonlocal bar_count
        bar_count += 1
        
        print(f"\n📊 Bar #{bar_count} received:")
        print(f"   Symbol: {bar.symbol}")
        print(f"   Timestamp: {bar.timestamp}")
        print(f"   Open: ${bar.open:.2f}")
        print(f"   High: ${bar.high:.2f}")
        print(f"   Low: ${bar.low:.2f}")
        print(f"   Close: ${bar.close:.2f}")
        print(f"   Volume: {bar.volume:,.0f}")
        
        if bar_count >= max_bars:
            print(f"\n✅ Successfully received {max_bars} bars, stopping stream...")
            provider.stop_stream()
    
    # Test parameters
    symbols = ["SPY", "QQQ"]
    
    print(f"\n🔴 Starting live stream for: {', '.join(symbols)}")
    print("Waiting for data (this may take a moment during market hours)...")
    print("Press Ctrl+C to stop\n")
    print("-" * 60)
    
    try:
        # Subscribe to bars
        provider.subscribe_bars(on_bar, symbols)
        
        # Wait for bars or timeout after 60 seconds
        timeout = 60
        start_time = asyncio.get_event_loop().time()
        
        while bar_count < max_bars:
            await asyncio.sleep(1)
            
            # Check for timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                print(f"\n⏱️ Timeout after {timeout} seconds")
                if bar_count == 0:
                    print("⚠️  No bars received - market may be closed")
                    print("   Market hours: 9:30 AM - 4:00 PM ET, Mon-Fri")
                break
        
        if bar_count > 0:
            print(f"\n✅ Test complete! Received {bar_count} bars")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n🛑 Stopping stream...")
        provider.stop_stream()
        # Give stream time to cleanup
        await asyncio.sleep(2)
        print("Done!")

if __name__ == "__main__":
    asyncio.run(test_stream())