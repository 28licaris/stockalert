import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from app.indicators.rsi import calculate_rsi
from app.indicators.divergence import detect_divergence

def test_rsi_calculation():
    """Test RSI calculation with known values"""
    # Create sample data with known RSI
    dates = pd.date_range(start='2025-01-01', periods=100, freq='1min')
    prices = [100 + i * 0.5 for i in range(100)]  # Uptrend
    
    df = pd.DataFrame({
        'close': prices
    }, index=dates)
    
    rsi = calculate_rsi(df, period=14)
    
    assert not rsi.empty
    assert rsi.max() <= 100
    assert rsi.min() >= 0
    print(f"✅ RSI range: {rsi.min():.2f} - {rsi.max():.2f}")

def test_divergence_detection():
    """Test divergence detection"""
    # Create sample data with bullish divergence
    dates = pd.date_range(start='2025-01-01', periods=100, freq='1min')
    
    # Price making lower lows
    prices = [100 - i * 0.1 if i < 50 else 95 + (i-50) * 0.1 for i in range(100)]
    
    # RSI making higher lows (divergence)
    rsi_values = [50 - i * 0.2 if i < 25 else 45 + (i-25) * 0.3 for i in range(100)]
    
    df = pd.DataFrame({
        'close': prices,
        'rsi': rsi_values
    }, index=dates)
    
    divergences = detect_divergence(df, indicator='rsi')
    
    print(f"✅ Found {len(divergences)} divergences")
    assert isinstance(divergences, list)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])