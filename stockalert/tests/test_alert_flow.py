import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from app.config import settings, get_provider
from app.services.alert_service import AlertService

@pytest.mark.asyncio
async def test_alert_flow():
    """Test complete alert detection flow"""
    provider = get_provider()
    alert_service = AlertService(provider)
    
    # Get recent historical data
    symbol = "SPY"
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=2)
    
    print(f"Fetching data for {symbol}...")
    df = await provider.historical_df(symbol, start, end, timeframe="1Min")
    
    assert not df.empty, "No historical data received"
    print(f"✅ Got {len(df)} bars")
    
    # Process for alerts
    alerts = await alert_service.process_symbol(symbol, df)
    
    print(f"✅ Processed alerts: {len(alerts)} found")
    for alert in alerts:
        print(f"   - {alert.signal_type} at ${alert.price:.2f}")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])