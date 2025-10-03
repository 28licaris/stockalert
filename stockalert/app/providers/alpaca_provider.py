# import threading
# import pandas as pd
# import asyncio
# from datetime import datetime
# from alpaca.data.live import StockDataStream
# from alpaca.data.historical import StockHistoricalDataClient
# from alpaca.data.requests import StockBarsRequest
# from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
# from app.providers.base import DataProvider

# class AlpacaProvider(DataProvider):
#     def __init__(self, api_key: str, secret_key: str, feed: str="iex"):
#         self._stream = StockDataStream(api_key, secret_key, feed=feed)
#         self._hist = StockHistoricalDataClient(api_key, secret_key)
#         self._thread = None
#         self._started = False

#     def start_stream(self):
#         if self._started: return
#         def _run(): self._stream.run()
#         self._thread = threading.Thread(target=_run, daemon=True)
#         self._thread.start()
#         self._started = True

#     def stop_stream(self): 
#         try: self._stream.stop()
#         finally: self._started=False

#     def subscribe_bars(self, callback, tickers: list[str]):
#         # callback must be async function accepting Alpaca bar
#         for t in tickers: self._stream.subscribe_bars(callback, t)
#         self.start_stream()

#     def unsubscribe_bars(self, tickers: list[str]):
#         for t in tickers:
#             try: self._stream.unsubscribe_bars(t)
#             except Exception: pass

#     async def historical_df(self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Min") -> pd.DataFrame:
#         """Fetch historical data and return as DataFrame"""
#         request = StockBarsRequest(
#             symbol_or_symbols=symbol,
#             timeframe=TimeFrame(1, TimeFrameUnit.Minute) if timeframe == "1Min" else TimeFrame(1, TimeFrameUnit.Day),
#             start=start,
#             end=end
#         )
        
#         # Run the synchronous API call in an executor to avoid blocking
#         loop = asyncio.get_event_loop()
#         bars_set = await loop.run_in_executor(None, self._hist.get_stock_bars, request)
        
#         # Access the data dictionary from BarSet
#         bars_dict = bars_set.data
        
#         # Get bars for the symbol
#         if symbol not in bars_dict:
#             return pd.DataFrame()
        
#         bars = bars_dict[symbol]
        
#         if not bars:
#             return pd.DataFrame()
        
#         # Convert to DataFrame
#         data = []
#         for bar in bars:
#             data.append({
#                 'timestamp': bar.timestamp,
#                 'open': bar.open,
#                 'high': bar.high,
#                 'low': bar.low,
#                 'close': bar.close,
#                 'volume': bar.volume,
#                 'vwap': bar.vwap if hasattr(bar, 'vwap') else None,
#                 'trade_count': bar.trade_count if hasattr(bar, 'trade_count') else None
#             })
        
#         df = pd.DataFrame(data)
#         if not df.empty:
#             df.set_index('timestamp', inplace=True)
#             df.sort_index(inplace=True)
        
#         return df

import threading
import asyncio
import pandas as pd
import logging
from datetime import datetime
from alpaca.data.live import StockDataStream
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from app.providers.base import DataProvider

logger = logging.getLogger(__name__)


class AlpacaProvider(DataProvider):
    def __init__(self, api_key: str, secret_key: str, feed: str = "iex"):
        self._stream = StockDataStream(api_key, secret_key, feed=feed)
        self._hist = StockHistoricalDataClient(api_key, secret_key)
        self._thread = None
        self._started = False
        self._main_loop = None

    def start_stream(self):
        """Start stream in separate thread"""
        if self._started:
            logger.debug("⚠️  Stream already started, skipping")
            return
        
        def _run():
            try:
                logger.info("🌐 Starting Alpaca WebSocket stream in background thread...")
                self._stream.run()
                logger.info("✅ Alpaca stream running")
            except Exception as e:
                logger.error(f"❌ Alpaca stream error: {e}", exc_info=True)
        
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._started = True
        logger.info("✅ Alpaca stream thread started")

    def stop_stream(self):
        """Stop the stream gracefully"""
        if not self._started:
            return
        
        try:
            logger.info("🛑 Stopping Alpaca stream...")
            self._stream.stop()
        except Exception as e:
            logger.error(f"Error stopping stream: {e}")
        finally:
            self._started = False
            self._main_loop = None
            logger.info("✅ Alpaca stream stopped")

    def subscribe_bars(self, callback, tickers: list[str]):
        """
        Subscribe to bar updates.
        
        Alpaca SDK requires async handler, but runs in separate thread/loop.
        We bridge the callback to the main application event loop.
        
        Args:
            callback: Async function that processes bars (runs in main loop)
            tickers: List of symbols to subscribe to
        """
        logger.info(f"📡 Subscribing to bars for {tickers}")
        
        # Capture the main event loop (where our app runs)
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
                logger.info("✅ Captured main event loop")
            except RuntimeError:
                logger.error("❌ No event loop running! Call from async context.")
                return
        
        # Create async handler that bridges to main loop
        async def alpaca_handler(bar):
            """
            This runs in Alpaca's thread/loop.
            Bridge the callback to the main application loop.
            """
            if self._main_loop and not self._main_loop.is_closed():
                # Schedule callback in main loop from Alpaca's thread
                asyncio.run_coroutine_threadsafe(
                    callback(bar),
                    self._main_loop
                )
            else:
                logger.error("❌ Main event loop not available!")
        
        # Subscribe to each ticker with the async handler
        for ticker in tickers:
            try:
                logger.info(f"🔗 Subscribing to {ticker}...")
                self._stream.subscribe_bars(alpaca_handler, ticker)
                logger.info(f"✅ Subscribed to {ticker}")
            except Exception as e:
                logger.error(f"❌ Failed to subscribe to {ticker}: {e}", exc_info=True)
        
        # Start the stream
        logger.info("🚀 Starting WebSocket stream...")
        self.start_stream()
        logger.info("✅ Subscription complete")

    def unsubscribe_bars(self, tickers: list[str]):
        """Unsubscribe from bar updates"""
        for ticker in tickers:
            try:
                self._stream.unsubscribe_bars(ticker)
                logger.info(f"🔕 Unsubscribed from {ticker}")
            except Exception as e:
                logger.debug(f"Error unsubscribing {ticker}: {e}")

    async def historical_df(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min"
    ) -> pd.DataFrame:
        """Fetch historical data and return as DataFrame"""
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=(
                TimeFrame(1, TimeFrameUnit.Minute)
                if timeframe == "1Min"
                else TimeFrame(1, TimeFrameUnit.Day)
            ),
            start=start,
            end=end
        )
        
        # Run the synchronous API call in an executor to avoid blocking
        loop = asyncio.get_event_loop()
        bars_set = await loop.run_in_executor(None, self._hist.get_stock_bars, request)
        
        # Access the data dictionary from BarSet
        bars_dict = bars_set.data
        
        # Get bars for the symbol
        if symbol not in bars_dict:
            return pd.DataFrame()
        
        bars = bars_dict[symbol]
        
        if not bars:
            return pd.DataFrame()
        
        # Convert to DataFrame
        data = []
        for bar in bars:
            data.append({
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
                'vwap': bar.vwap if hasattr(bar, 'vwap') else None,
                'trade_count': bar.trade_count if hasattr(bar, 'trade_count') else None
            })
        
        df = pd.DataFrame(data)
        if not df.empty:
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)
        
        return df