from abc import ABC, abstractmethod
import pandas as pd

class DataProvider(ABC):
    @abstractmethod
    def start_stream(self): ...
    @abstractmethod
    def stop_stream(self): ...
    @abstractmethod
    def subscribe_bars(self, callback, tickers: list[str]): ...
    @abstractmethod
    def unsubscribe_bars(self, tickers: list[str]): ...
    @abstractmethod
    async def historical_df(self, symbol: str, start, end, timeframe: str="1Min") -> pd.DataFrame: ...

    async def search_instruments(self, query: str, *, limit: int = 10) -> list[dict]:
        """
        Symbol autocomplete. Returns a list of `{symbol, description, exchange,
        asset_type}` records matching `query` (prefix on ticker + substring on
        description), capped at `limit`.

        Default implementation returns an empty list so providers that don't
        support search degrade gracefully (the UI will simply show no
        suggestions). Concrete providers (e.g. SchwabProvider) override.
        """
        return []
