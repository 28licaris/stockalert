"""
Divergence Detection Script - Test divergence signals on historical data

Current features:
- Load historical price data
- Calculate technical indicators (RSI, MACD, TSI)
- Detect divergence patterns with quality filters
- Save signals to database for analysis

Quality Filters Applied:
- Minimum pivot separation (20 bars)
- Minimum price change (1%)
- Minimum indicator change (5%)
- Trend filter (EMA-based)
- Cooldown period to avoid duplicates

Future additions:
- Forward return analysis
- Strategy backtesting
- Performance metrics
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import pandas as pd
from pathlib import Path

from app.db import init_schema
from app.config import get_provider, settings
from app.db import queries
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def backfill(
    symbols: list[str],
    days: int,
    indicator_name: str,
    signal_type: str
):
    """
    Detect divergences in historical data and save to database.
    
    Args:
        symbols: List of stock symbols
        days: Number of days to analyze
        indicator_name: 'rsi', 'macd', or 'tsi'
        signal_type: Type of divergence to detect
    """
    # Initialize provider and loader
    provider = get_provider()
    loader = HistoricalDataLoader(provider)
    
    # Setup date range
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    
    # Divergence detection parameters (from config)
    lookback_bars = settings.lookback_bars
    slope_threshold = settings.pivot_k
    min_pivot_separation = settings.min_pivot_separation
    cooldown_bars = lookback_bars // 3
    
    logger.info(f"Parameters:")
    logger.info(f"  - lookback_bars: {lookback_bars}")
    logger.info(f"  - pivot_k: {slope_threshold}")
    logger.info(f"  - min_pivot_separation: {min_pivot_separation} bars")
    logger.info(f"  - min_price_change: {settings.min_price_change_pct:.1%}")
    logger.info(f"  - min_indicator_change: {settings.min_indicator_change_pct:.1%}")
    logger.info(f"  - cooldown_bars: {cooldown_bars}")
    logger.info(f"  - trend_filter: {'Enabled' if settings.use_trend_filter else 'Disabled'}")
    
    # Select indicator
    indicators_map = {
        'rsi': RSI(),
        'macd': MACD(),
        'tsi': TSI()
    }
    indicator = indicators_map.get(indicator_name.lower())
    if not indicator:
        logger.error(f"❌ Unknown indicator: {indicator_name}")
        return
    
    # Select detector
    detectors_map = {
        'hidden_bullish_divergence': detect_hidden_bullish,
        'hidden_bearish_divergence': detect_hidden_bearish,
        'regular_bullish_divergence': detect_regular_bullish,
        'regular_bearish_divergence': detect_regular_bearish
    }
    detector = detectors_map.get(signal_type)
    if not detector:
        logger.error(f"❌ Unknown signal type: {signal_type}")
        return
    
    logger.info(f"Backfilling {len(symbols)} symbols for {days} days")
    logger.info(f"Indicator: {indicator_name}, Signal: {signal_type}")
    logger.info(f"Parameters:")
    logger.info(f"  - lookback_bars: {lookback_bars}")
    logger.info(f"  - pivot_k: {slope_threshold}")
    logger.info(f"  - min_pivot_separation: {min_pivot_separation}")
    logger.info(f"  - cooldown_bars: {cooldown_bars}")
    
    total_signals = 0
    
    for symbol in symbols:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {symbol}")
        logger.info(f"{'='*60}")
        
        # Load historical data
        df = await loader.load_bars(
            symbol=symbol,
            limit=10000,
            start=start,
            end=end
        )
        
        if df.empty:
            logger.warning(f"❌ No data for {symbol}")
            continue
        
        logger.info(f"📊 Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")
        
        # Calculate indicator
        ind_values = indicator.compute(
            df['close'],
            df.get('high'),
            df.get('low')
        )
        
        logger.info(f"🔍 Scanning for HIGH-QUALITY divergences...")
        logger.info(f"   (This filters weak signals - expect fewer but stronger patterns)")
        
        # Walk-forward detection with cooldown
        signals_found = 0
        last_progress = 0
        last_signal_idx = -1  # Track last signal position
        last_signal_ts = None  # Track last signal timestamp
        
        for i in range(lookback_bars, len(df)):
            # Skip cooldown period after finding a signal
            if last_signal_idx != -1 and i <= last_signal_idx + cooldown_bars:
                continue
            
            # Progress logging every 10%
            progress = int((i - lookback_bars) / (len(df) - lookback_bars) * 100)
            if progress >= last_progress + 10:
                logger.info(f"   Progress: {progress}% ({i}/{len(df)} bars)")
                last_progress = progress
            
            # Only use data up to current point (no look-ahead bias)
            close_subset = df['close'].iloc[:i+1]
            ind_subset = ind_values.iloc[:i+1]
            
            # Detect divergence with NEW quality filters
            result = detector(
                close_subset,
                ind_subset,
                lookback_bars,
                slope_threshold,
                min_pivot_separation  # Pass the separation parameter
            )
            
            if result:
                # Check if this is a NEW signal (different timestamp)
                if last_signal_ts is None or result['p2_ts'] != last_signal_ts:
                    signals_found += 1
                    total_signals += 1
                    
                    # Extract pivot values
                    p1_price = float(close_subset.loc[result['p1_ts']])
                    p2_price = float(close_subset.loc[result['p2_ts']])
                    p1_ind = float(ind_subset.loc[result['p1_ts']])
                    p2_ind = float(ind_subset.loc[result['p2_ts']])
                    
                    # Calculate divergence strength metrics
                    price_change_pct = abs(p2_price - p1_price) / p1_price * 100
                    ind_change_pct = abs(p2_ind - p1_ind) / abs(p1_ind + 0.01) * 100
                    pivot_distance = (result['p2_ts'] - result['p1_ts']).total_seconds() / 60  # minutes
                    
                    logger.info(
                        f"   🎯 Signal #{signals_found} at {result['p2_ts']}: "
                        f"Price=${p2_price:.2f} (Δ{price_change_pct:.1f}%), "
                        f"Indicator={p2_ind:.2f} (Δ{ind_change_pct:.1f}%), "
                        f"Pivot Distance={pivot_distance:.0f}min"
                    )
                    
                    try:
                        await queries.insert_signals_batch_async([{
                            "symbol": symbol,
                            "signal_type": signal_type,
                            "indicator": indicator_name,
                            "ts_signal": result['p2_ts'],
                            "price_at_signal": p2_price,
                            "indicator_value": p2_ind,
                            "p1_ts": result['p1_ts'],
                            "p2_ts": result['p2_ts'],
                        }])
                        logger.debug("      ✅ Saved to ClickHouse")
                    except Exception as e:
                        logger.warning(f"      ⚠️  Failed to save signal: {e}")
                    
                    # Update tracking to enforce cooldown
                    last_signal_idx = i
                    last_signal_ts = result['p2_ts']
                    
                    logger.info(f"      ⏸️  Cooldown: Skipping next {cooldown_bars} bars")
        
        logger.info(f"✅ Found {signals_found} HIGH-QUALITY signals for {symbol}")
        
        # Show signal density
        if signals_found > 0:
            days_scanned = (df.index[-1] - df.index[0]).days
            signals_per_day = signals_found / days_scanned if days_scanned > 0 else 0
            logger.info(f"   Signal density: {signals_per_day:.2f} signals/day")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Backfill complete!")
    logger.info(f"{'='*60}")
    logger.info(f"Total signals found: {total_signals}")
    logger.info(f"\nQuality filters applied:")
    logger.info(f"  ✓ Minimum pivot separation: {min_pivot_separation} bars")
    logger.info(f"  ✓ Minimum price change: 1%")
    logger.info(f"  ✓ Minimum indicator change: 5%")
    logger.info(f"  ✓ Cooldown period: {cooldown_bars} bars")
    logger.info(f"  ✓ Trend filter: {'Enabled' if settings.use_trend_filter else 'Disabled'}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Detect high-quality divergence signals in historical data'
    )
    parser.add_argument(
        '--tickers',
        nargs='+',
        required=True,
        help='Stock symbols to analyze (e.g., SPY QQQ AAPL)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Number of days to analyze (default: 30)'
    )
    parser.add_argument(
        '--indicator',
        choices=['rsi', 'macd', 'tsi'],
        default='rsi',
        help='Technical indicator to use (default: rsi)'
    )
    parser.add_argument(
        '--signal-type',
        choices=[
            'hidden_bullish_divergence',
            'hidden_bearish_divergence',
            'regular_bullish_divergence',
            'regular_bearish_divergence'
        ],
        default='hidden_bullish_divergence',
        help='Type of divergence to detect (default: hidden_bullish_divergence)'
    )
    
    args = parser.parse_args()
    
    await asyncio.to_thread(init_schema)
    logger.info("✅ ClickHouse schema initialized")
    
    # Run backfill
    await backfill(
        symbols=args.tickers,
        days=args.days,
        indicator_name=args.indicator,
        signal_type=args.signal_type
    )


if __name__ == "__main__":
    asyncio.run(main())