import pandas as pd
from app.providers.base import DataProvider

class PolygonProvider(DataProvider):
    """Stub: implement WebSocket + REST when switching providers."""
    def __init__(self, api_key: str): self.api_key = api_key
    def start_stream(self): raise NotImplementedError("Polygon streaming not implemented yet")
    def stop_stream(self): pass
    def subscribe_bars(self, callback, tickers: list[str]): raise NotImplementedError("Polygon streaming not implemented yet")
    def unsubscribe_bars(self, tickers: list[str]): pass
    async def historical_df(self, symbol, start, end, timeframe="1Min") -> pd.DataFrame:
        # Implement with polygon RESTClient when ready
        return pd.DataFrame()
