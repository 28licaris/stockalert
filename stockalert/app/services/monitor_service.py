"""
Real-time monitoring service for divergence detection.

This service processes live market data bars and detects divergences
in real-time using a sliding window approach.
"""
import asyncio
import logging
import pandas as pd
from datetime import timezone, datetime
from typing import Optional, Callable

from app.db import SessionLocal
from app.models import Bar, Signal
from app.indicators.rsi import RSI
from app.indicators.macd import MACD
from app.indicators.tsi import TSI
from app.divergence import (
    detect_hidden_bullish,
    detect_hidden_bearish,
    detect_regular_bullish,
    detect_regular_bearish
)
from app.config import settings
from app.providers.base import DataProvider
from app.services.historical_loader import HistoricalDataLoader

logger = logging.getLogger(__name__)

# Indicator factory
INDICATOR_MAP = {
    "rsi": RSI,
    "macd": MACD,
    "tsi": TSI
}

# Signal detector factory
DETECTOR_MAP = {
    "hidden_bullish_divergence": detect_hidden_bullish,
    "hidden_bearish_divergence": detect_hidden_bearish,
    "regular_bullish_divergence": detect_regular_bullish,
    "regular_bearish_divergence": detect_regular_bearish,
}


class MonitorService:
    """
    Monitors live price data for divergence signals.
    
    Uses HistoricalDataLoader for intelligent data preloading with:
    - Database cache (fastest)
    - Parquet cache (optional)
    - API fallback (automatic)
    """
    
    def __init__(
        self,
        provider: DataProvider,
        indicator_name: str,
        signal_type: str,
        broadcast_cb: Optional[Callable] = None
    ):
        self.provider = provider
        self.indicator_name = indicator_name
        self.signal_type = signal_type
        self.broadcast_cb = broadcast_cb
        self.buffers = {}
        self.last_bar_time = {}  # Track last bar timestamp per symbol
        
        # Initialize historical data loader
        self.historical_loader = HistoricalDataLoader(provider)
        
        # Initialize indicator
        if indicator_name not in INDICATOR_MAP:
            raise ValueError(f"Unknown indicator: {indicator_name}")
        self.indicator = INDICATOR_MAP[indicator_name]()
        
        # Get detector function
        if signal_type not in DETECTOR_MAP:
            raise ValueError(f"Unknown signal type: {signal_type}")
        self.detector = DETECTOR_MAP[signal_type]
        
        logger.info(f"MonitorService initialized: {indicator_name} / {signal_type}")
    
    async def monitor(self, tickers: list[str]):
        """
        Start monitoring specified tickers with optimized data loading.
        
        This method:
        1. Preloads historical data using config-driven settings
        2. Subscribes to live market data
        3. Processes incoming bars in real-time
        4. Detects divergence signals
        
        Args:
            tickers: List of stock symbols to monitor
        """
        logger.info(f"📊 Starting monitor for {tickers}")
        logger.info(
            f"Config: {settings.monitor_preload_bars} bars, "
            f"{settings.monitor_preload_days} days lookback"
        )
        
        # Preload historical data for each symbol
        for symbol in tickers:
            self.buffers[symbol] = []
            self.last_bar_time[symbol] = datetime.now(timezone.utc)
            
            # Use HistoricalDataLoader with config-driven settings
            # This automatically handles:
            # - Database cache check
            # - API fallback if insufficient data
            # - Saving fetched data to database
            df = await self.historical_loader.load_bars(
                symbol,
                purpose="monitor"  # Uses monitor-specific config defaults
            )
            
            if not df.empty:
                # Convert DataFrame to buffer format
                for ts, row in df.iterrows():
                    bar_dict = {
                        'timestamp': ts,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': int(row.get('volume', 0) or 0),
                    }
                    self.buffers[symbol].append(bar_dict)
                
                self.last_bar_time[symbol] = df.index[-1]
            
            # Log buffer status with detailed info
            buffer_size = len(self.buffers[symbol])
            required_bars = settings.lookback_bars
            
            if buffer_size >= required_bars:
                logger.info(
                    f"✅ {symbol}: Ready for divergence detection "
                    f"({buffer_size} bars loaded, need {required_bars})"
                )
            else:
                logger.warning(
                    f"⚠️  {symbol}: Insufficient data "
                    f"({buffer_size}/{required_bars} bars)"
                )
                logger.warning(
                    f"   Monitor will collect more data as market opens..."
                )
        
        # Define async callback for incoming bars
        async def on_bar(bar):
            try:
                await self._process_bar(bar)
            except Exception as e:
                logger.error(f"Error processing bar: {e}", exc_info=True)
        
        # Subscribe to live data
        self.provider.subscribe_bars(on_bar, tickers)
        
        # Keep service running with heartbeat logging
        try:
            heartbeat_counter = 0
            while True:
                await asyncio.sleep(60)  # Check every minute
                
                heartbeat_counter += 1
                
                # Log heartbeat based on config interval
                if heartbeat_counter >= (settings.heartbeat_interval_seconds // 60):
                    self._log_heartbeat(tickers)
                    heartbeat_counter = 0
                    
        except asyncio.CancelledError:
            logger.info("🛑 Monitor cancelled")
            raise
    
    def _log_heartbeat(self, tickers: list[str]):
        """
        Log periodic heartbeat with monitor status.
        
        Args:
            tickers: List of monitored symbols
        """
        now = datetime.now(timezone.utc)
        status_lines = [f"💓 Monitor heartbeat: {len(tickers)} symbols"]
        
        for symbol in tickers:
            buffer_size = len(self.buffers.get(symbol, []))
            last_bar = self.last_bar_time.get(symbol)
            
            if last_bar:
                idle_seconds = (now - last_bar).total_seconds()
                idle_status = "🟢 active" if idle_seconds < 300 else "🟡 idle"
                status_lines.append(
                    f"  {symbol}: {buffer_size} bars, "
                    f"last bar {idle_seconds:.0f}s ago {idle_status}"
                )
            else:
                status_lines.append(f"  {symbol}: {buffer_size} bars, no data yet")
        
        logger.info("\n".join(status_lines))
    
    async def _process_bar(self, bar):
        """
        Process a single incoming bar.
        
        This method:
        1. Saves bar to database (async)
        2. Adds to rolling buffer
        3. Calculates indicator values
        4. Detects divergence signals
        5. Broadcasts signals if detected
        
        Args:
            bar: Incoming price bar from provider
        """
        symbol = bar.symbol
        
        # Extract timestamp and ensure timezone-aware
        ts = getattr(bar, "timestamp", None) or getattr(bar, "ts", None)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        logger.info(f"📊 {symbol}: ${bar.close:.2f} @ {ts}")
        
        # Update last bar time for heartbeat monitoring
        self.last_bar_time[symbol] = ts
        
        # Save to database (async task, don't block processing)
        asyncio.create_task(self._persist_bar(symbol, ts, bar))
        
        # Add to rolling buffer
        bar_dict = {
            'timestamp': ts,
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(getattr(bar, 'volume', 0) or 0),
        }
        
        self.buffers[symbol].append(bar_dict)
        
        # Trim buffer to prevent memory bloat (keep ~2200 bars)
        if len(self.buffers[symbol]) > 3000:
            self.buffers[symbol] = self.buffers[symbol][-2200:]
            logger.debug(f"🔄 {symbol}: Buffer trimmed to 2200 bars")
        
        # Check if we have enough data for analysis
        buffer_size = len(self.buffers[symbol])
        required_bars = settings.lookback_bars
        
        if buffer_size < required_bars:
            logger.info(
                f"⏳ {symbol}: Collecting data ({buffer_size}/{required_bars})"
            )
            return
        
        # Convert buffer to DataFrame for analysis
        df = pd.DataFrame(self.buffers[symbol])
        df.set_index('timestamp', inplace=True)
        
        # Calculate indicator values
        ind_values = self.indicator.compute(
            df['close'],
            df.get('high'),
            df.get('low')
        )
        
        # Detect divergence using configured parameters
        result = self.detector(
            df['close'],
            ind_values,
            lookback=settings.lookback_bars,
            k=settings.pivot_k
        )
        
        # Handle signal detection
        if result:
            logger.warning(f"🚨 SIGNAL DETECTED: {symbol} - {self.signal_type}")
            logger.warning(f"   Price: ${result['price']:.2f}")
            logger.warning(f"   {self.indicator_name.upper()}: {result['indicator_value']:.2f}")
            logger.warning(f"   Timestamp: {result['p2_ts']}")
            
            # Save signal to database
            await self._persist_signal(symbol, result)
            
            # Broadcast signal via callback (e.g., WebSocket)
            if self.broadcast_cb:
                await self.broadcast_cb({
                    "symbol": symbol,
                    "signal_type": self.signal_type,
                    "indicator": self.indicator_name,
                    "timestamp": result['p2_ts'].isoformat(),
                    "price": float(result['price']),
                    "indicator_value": float(result['indicator_value']),
                })
    
    async def _persist_bar(self, symbol: str, ts, bar):
        """
        Save bar to database asynchronously.
        
        Args:
            symbol: Stock symbol
            ts: Timestamp of bar
            bar: Price bar object
        """
        async with SessionLocal() as session:
            try:
                db_bar = Bar(
                    symbol=symbol,
                    ts=ts,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=int(getattr(bar, 'volume', 0) or 0)
                )
                session.add(db_bar)
                await session.commit()
                logger.debug(f"💾 Saved bar: {symbol} @ {ts}")
            except Exception as e:
                await session.rollback()
                # Ignore duplicate key errors (bar already exists)
                if "duplicate key" not in str(e).lower():
                    logger.error(f"Error saving bar: {e}")
    
    async def _persist_signal(self, symbol: str, result: dict):
        """
        Save detected signal to database.
        
        Args:
            symbol: Stock symbol
            result: Divergence detection result dictionary
        """
        async with SessionLocal() as session:
            try:
                signal = Signal(
                    symbol=symbol,
                    signal_type=self.signal_type,
                    indicator=self.indicator_name,
                    ts_signal=result['p2_ts'],
                    price_at_signal=float(result['price']),
                    indicator_value=float(result['indicator_value']),
                    p1_ts=result['p1_ts'],
                    p2_ts=result['p2_ts']
                )
                session.add(signal)
                await session.commit()
                logger.warning("💾 Signal saved to database")
            except Exception as e:
                await session.rollback()
                # Log error unless it's a duplicate
                if "duplicate key" not in str(e).lower():
                    logger.error(f"Error saving signal: {e}")
                else:
                    logger.debug("Signal already exists in database")