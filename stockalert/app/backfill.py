"""
Backfill Script - Load historical data and detect signals

This script uses HistoricalDataLoader which handles:
- Loading from database if available
- Fetching from API if needed
- Saving to database automatically
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_provider, settings
from app.db import init_db, SessionLocal
from app.models import Signal
from app.indicators.rsi import RSI
from app.indicators.macd import MACD
from app.indicators.tsi import TSI
from app.divergence import (
    detect_hidden_bullish,
    detect_hidden_bearish,
    detect_regular_bullish,
    detect_regular_bearish
)
from app.services.historical_loader import HistoricalDataLoader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INDICATOR_MAP = {"rsi": RSI, "macd": MACD, "tsi": TSI}
DETECTOR_MAP = {
    "hidden_bullish_divergence": detect_hidden_bullish,
    "hidden_bearish_divergence": detect_hidden_bearish,
    "regular_bullish_divergence": detect_regular_bullish,
    "regular_bearish_divergence": detect_regular_bearish,
}


async def backfill(
    symbols: list[str],
    days: int,
    indicator_name: str,
    signal_type: str
):
    """
    Backfill historical data and detect signals.
    
    Args:
        symbols: List of stock symbols
        days: Number of days to backfill
        indicator_name: Indicator to use (rsi, macd, tsi)
        signal_type: Type of divergence to detect
    """
    # Initialize database
    await init_db()
    logger.info("✅ Database initialized")
    
    # Get provider and create historical loader
    provider = get_provider()
    loader = HistoricalDataLoader(provider)
    
    # Get indicator and detector
    indicator = INDICATOR_MAP[indicator_name]()
    detector = DETECTOR_MAP[signal_type]
    
    # Calculate date range
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    
    logger.info(f"Backfilling {len(symbols)} symbols for {days} days")
    logger.info(f"Indicator: {indicator_name}, Signal: {signal_type}")
    
    for symbol in symbols:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {symbol}")
        logger.info(f"{'='*60}")
        
        # Load historical data (handles DB → API fallback)
        df = await loader.load_bars(symbol, limit=10000, start=start, end=end)
        
        if df.empty:
            logger.warning(f"❌ No data for {symbol}")
            continue
        
        logger.info(f"📊 Analyzing {len(df)} bars for {symbol}")
        
        # Calculate indicator
        ind_values = indicator.compute(
            df['close'],
            df.get('high'),
            df.get('low')
        )
        
        # Scan for signals
        signals_found = 0
        for i in range(settings.lookback_bars, len(df)):
            # Get subset of data up to this point
            close_subset = df['close'].iloc[:i+1]
            ind_subset = ind_values.iloc[:i+1]
            
            # Detect divergence
            result = detector(
                close_subset,
                ind_subset,
                lookback=settings.lookback_bars,
                k=settings.pivot_k
            )
            
            if result:
                signals_found += 1
                logger.info(
                    f"🚨 Signal #{signals_found} at {result['p2_ts']}: "
                    f"${result['price']:.2f}"
                )
                
                # Save signal to database
                async with SessionLocal() as session:
                    signal = Signal(
                        symbol=symbol,
                        signal_type=signal_type,
                        indicator=indicator_name,
                        ts_signal=result['p2_ts'],
                        price_at_signal=float(result['price']),
                        indicator_value=float(result['indicator_value']),
                        p1_ts=result['p1_ts'],
                        p2_ts=result['p2_ts']
                    )
                    session.add(signal)
                    try:
                        await session.commit()
                    except Exception as e:
                        await session.rollback()
                        if "duplicate key" not in str(e).lower():
                            logger.error(f"Error saving signal: {e}")
        
        logger.info(f"✅ {symbol}: {len(df)} bars, {signals_found} signals found")
    
    logger.info(f"\n{'='*60}")
    logger.info("✅ Backfill complete!")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical data and signals")
    parser.add_argument("--tickers", nargs="+", required=True, help="Stock symbols")
    parser.add_argument("--days", type=int, default=30, help="Days to backfill")
    parser.add_argument(
        "--indicator",
        choices=list(INDICATOR_MAP.keys()),
        default="rsi",
        help="Indicator to use"
    )
    parser.add_argument(
        "--signal-type",
        choices=list(DETECTOR_MAP.keys()),
        default="hidden_bullish_divergence",
        help="Type of divergence to detect"
    )
    
    args = parser.parse_args()
    
    asyncio.run(backfill(
        args.tickers,
        args.days,
        args.indicator,
        args.signal_type
    ))