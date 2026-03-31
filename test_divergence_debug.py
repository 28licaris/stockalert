"""Quick test to see if divergence detection is working at all."""
import asyncio
from datetime import datetime, timedelta, timezone
from app.config import get_provider, settings
from app.services.historical_loader import HistoricalDataLoader
from app.indicators.rsi import RSI
from app.divergence import detect_hidden_bullish, find_pivot_lows

async def test():
    print("=" * 60)
    print("DIVERGENCE DETECTION DEBUG")
    print("=" * 60)
    
    print("\n1. Current Settings:")
    print(f"   - use_trend_filter: {settings.use_trend_filter}")
    print(f"   - min_price_change_pct: {settings.min_price_change_pct:.2%}")
    print(f"   - min_indicator_change_pct: {settings.min_indicator_change_pct:.2%}")
    print(f"   - min_pivot_separation: {settings.min_pivot_separation}")
    print(f"   - pivot_k: {settings.pivot_k}")
    print(f"   - lookback_bars: {settings.lookback_bars}")
    
    print("\n2. Loading data...")
    provider = get_provider()
    loader = HistoricalDataLoader(provider)
    
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    
    df = await loader.load_bars("SPY", limit=10000, start=start, end=end)
    print(f"   ✓ Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    
    # Calculate RSI
    print("\n3. Calculating RSI...")
    rsi = RSI().compute(df['close'])
    print(f"   ✓ RSI range: {rsi.min():.2f} - {rsi.max():.2f}")
    print(f"   ✓ Current RSI: {rsi.iloc[-1]:.2f}")
    
    # Test pivot detection with different k values
    print("\n4. Testing pivot detection...")
    for k in [3, 4, 5]:
        pivots = find_pivot_lows(df['close'].tail(200), k=k)
        print(f"   k={k}: Found {len(pivots)} pivot lows in last 200 bars")
        if len(pivots) >= 2:
            p1, p2 = pivots[-2], pivots[-1]
            p1_price = df['close'].loc[p1]
            p2_price = df['close'].loc[p2]
            p1_rsi = rsi.loc[p1]
            p2_rsi = rsi.loc[p2]
            
            price_higher = p2_price > p1_price
            rsi_lower = p2_rsi < p1_rsi
            
            price_change_pct = abs(p2_price - p1_price) / p1_price
            rsi_change_pct = abs(p2_rsi - p1_rsi) / abs(p1_rsi + 0.01)
            
            print(f"        Last 2 pivots:")
            print(f"        P1: {p1} | Price=${p1_price:.2f} | RSI={p1_rsi:.2f}")
            print(f"        P2: {p2} | Price=${p2_price:.2f} | RSI={p2_rsi:.2f}")
            print(f"        Price higher? {price_higher} (Δ{price_change_pct:.2%})")
            print(f"        RSI lower? {rsi_lower} (Δ{rsi_change_pct:.2%})")
            
            if price_higher and rsi_lower:
                print(f"        ✅ Hidden bullish divergence pattern detected!")
                print(f"        Meets min price change? {price_change_pct >= settings.min_price_change_pct}")
                print(f"        Meets min RSI change? {rsi_change_pct >= settings.min_indicator_change_pct}")
    
    # Test full divergence detection
    print("\n5. Testing full divergence detection...")
    result = detect_hidden_bullish(
        df['close'],
        rsi,
        lookback=80,
        k=4,
        min_pivot_separation=12
    )
    
    if result:
        print(f"   ✅ FOUND DIVERGENCE!")
        print(f"      P1: {result['p1_ts']}")
        print(f"      P2: {result['p2_ts']}")
        print(f"      Price: ${result['price']:.2f}")
        print(f"      RSI: {result['indicator_value']:.2f}")
    else:
        print("   ❌ No divergence found")
        
        print("\n6. Trying with ultra-relaxed settings...")
        # Temporarily relax all filters
        old_price = settings.min_price_change_pct
        old_ind = settings.min_indicator_change_pct
        old_sep = settings.min_pivot_separation
        
        settings.min_price_change_pct = 0.0001  # 0.01%
        settings.min_indicator_change_pct = 0.001  # 0.1%
        settings.min_pivot_separation = 5
        
        result = detect_hidden_bullish(df['close'], rsi, lookback=120, k=3, min_pivot_separation=5)
        
        # Restore
        settings.min_price_change_pct = old_price
        settings.min_indicator_change_pct = old_ind
        settings.min_pivot_separation = old_sep
        
        if result:
            print(f"   ✅ Found with ultra-relaxed settings!")
            print(f"      This means your thresholds are too strict")
        else:
            print("   ❌ Still nothing")
            print(f"      This might indicate genuine absence of patterns in recent data")

if __name__ == "__main__":
    asyncio.run(test())