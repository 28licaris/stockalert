"""
Historical Data Loader Service

Handles loading historical price data with multiple sources and fallback logic.
"""
import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.db import SessionLocal
from app.models import Bar
from app.providers.base import DataProvider
from app.config import settings
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """
    Loads historical price data with intelligent fallback logic.
    
    Priority:
    1. Database (if sufficient recent data exists)
    2. Parquet cache (if enabled)
    3. Provider API (with automatic save to DB/cache)
    """
    
    def __init__(
        self,
        provider: DataProvider,
        parquet_dir: Optional[Path] = None,
        use_parquet_cache: Optional[bool] = None
    ):
        self.provider = provider
        self.parquet_dir = parquet_dir or Path(settings.parquet_cache_dir)
        self.use_parquet_cache = (
            use_parquet_cache 
            if use_parquet_cache is not None 
            else settings.use_parquet_cache
        )
        
        if self.use_parquet_cache:
            self.parquet_dir.mkdir(parents=True, exist_ok=True)
    
    async def load_bars(
        self,
        symbol: str,
        limit: Optional[int] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        days_lookback: Optional[int] = None,
        purpose: str = "monitor"  # "monitor", "backfill", or "research"
    ) -> pd.DataFrame:
        """
        Load historical bars with smart defaults based on purpose.
        
        Args:
            symbol: Stock symbol
            limit: Number of bars needed (None = use purpose default)
            start: Start date (None = auto-calculate)
            end: End date (None = now)
            days_lookback: Days to fetch (None = use purpose default)
            purpose: Usage context - affects defaults
                - "monitor": Fast startup, recent data
                - "backfill": Historical analysis
                - "research": Custom range
        
        Returns:
            DataFrame with OHLCV data, indexed by timestamp
        """
        # Set defaults based on purpose
        if limit is None:
            limit = {
                "monitor": settings.monitor_preload_bars,
                "backfill": settings.monitor_preload_bars,
                "research": 1000
            }.get(purpose, settings.monitor_preload_bars)
        
        if days_lookback is None:
            days_lookback = {
                "monitor": settings.monitor_preload_days,
                "backfill": settings.backfill_default_days,
                "research": 30
            }.get(purpose, settings.monitor_preload_days)
        
        # Calculate date range
        if end is None:
            end = datetime.now(timezone.utc)
        
        if start is None:
            start = end - timedelta(days=days_lookback)
        
        logger.info(
            f"Loading {symbol} [{purpose}]: "
            f"{start.date()} to {end.date()} "
            f"(target: {limit} bars, {days_lookback} days)"
        )
        
        # Try database first
        df = await self._load_from_database(symbol, limit, start, end)
        
        # Check if sufficient data
        required_bars = int(limit * settings.data_sufficiency_threshold)
        if not df.empty and len(df) >= required_bars:
            logger.info(
                f"✅ Database: {len(df)} bars for {symbol} "
                f"(needed {required_bars})"
            )
            return df.iloc[-limit:]  # Return only what was requested
        
        if not df.empty:
            logger.warning(
                f"⚠️  Database: Only {len(df)}/{required_bars} bars for {symbol}"
            )
        
        # Try parquet cache
        if self.use_parquet_cache:
            df = await self._load_from_parquet(symbol, start, end)
            if not df.empty and len(df) >= required_bars:
                logger.info(f"✅ Parquet: {len(df)} bars for {symbol}")
                await self._save_to_database(symbol, df)
                return df.iloc[-limit:]
        
        # Fetch from API
        logger.info(f"🌐 Fetching {symbol} from API...")
        
        # Apply safety margin to ensure we get enough bars
        extended_start = start - timedelta(
            days=int(days_lookback * (settings.fetch_safety_margin - 1))
        )
        
        df = await self._fetch_from_provider(symbol, extended_start, end)
        
        if df.empty:
            logger.warning(f"⚠️  No data available for {symbol}")
            return df
        
        # Save to storage
        await self._save_to_database(symbol, df)
        if self.use_parquet_cache:
            await self._save_to_parquet(symbol, df)
        
        # Return requested amount
        if len(df) > limit:
            df = df.iloc[-limit:]
        
        logger.info(f"✅ Loaded {len(df)} bars for {symbol}")
        return df
    
    async def _load_from_database(
        self,
        symbol: str,
        limit: int,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Load bars from database."""
        try:
            async with SessionLocal() as session:
                # Fetch more than limit to account for gaps
                fetch_limit = int(limit * 1.2)
                
                result = await session.execute(
                    select(Bar)
                    .where(
                        Bar.symbol == symbol,
                        Bar.ts >= start,
                        Bar.ts <= end
                    )
                    .order_by(Bar.ts.desc())
                    .limit(fetch_limit)
                )
                bars = result.scalars().all()
                
                if not bars:
                    return pd.DataFrame()
                
                data = [{
                    'timestamp': bar.ts,
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': int(bar.volume),
                } for bar in reversed(bars)]
                
                df = pd.DataFrame(data)
                df.set_index('timestamp', inplace=True)
                return df
                
        except Exception as e:
            logger.error(f"Database error: {e}")
            return pd.DataFrame()
    
    async def _load_from_parquet(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Load bars from parquet cache."""
        try:
            parquet_file = self.parquet_dir / f"{symbol}_1min.parquet"
            if not parquet_file.exists():
                return pd.DataFrame()
            
            df = pd.read_parquet(parquet_file)
            
            # Filter date range
            mask = (df.index >= start) & (df.index <= end)
            df = df[mask]
            
            return df
            
        except Exception as e:
            logger.error(f"Parquet error: {e}")
            return pd.DataFrame()
    
    async def _fetch_from_provider(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Fetch bars from API provider."""
        try:
            df = await self.provider.historical_df(
                symbol,
                start,
                end,
                timeframe="1Min"
            )
            return df
        except Exception as e:
            logger.error(f"API fetch error: {e}", exc_info=True)
            return pd.DataFrame()
    
    async def _save_to_database(self, symbol: str, df: pd.DataFrame):
        """Save bars to database with duplicate handling."""
        if df.empty:
            return
        
        try:
            async with SessionLocal() as session:
                bars_data = [{
                    'symbol': symbol,
                    'ts': ts,
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': int(row.get('volume', 0) or 0)
                } for ts, row in df.iterrows()]
                
                stmt = insert(Bar).values(bars_data)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=['symbol', 'ts']
                )
                
                result = await session.execute(stmt)
                await session.commit()
                
                inserted = result.rowcount or 0
                duplicates = len(bars_data) - inserted
                
                if inserted > 0:
                    logger.info(f"💾 Saved {inserted} bars to DB ({symbol})")
                if duplicates > 0:
                    logger.debug(f"⏭️  Skipped {duplicates} duplicates ({symbol})")
                    
        except Exception as e:
            logger.error(f"DB save error: {e}", exc_info=True)
    
    async def _save_to_parquet(self, symbol: str, df: pd.DataFrame):
        """Save bars to parquet cache (append mode)."""
        if df.empty:
            return
        
        try:
            parquet_file = self.parquet_dir / f"{symbol}_1min.parquet"
            
            # Merge with existing data
            if parquet_file.exists():
                existing = pd.read_parquet(parquet_file)
                df = pd.concat([existing, df])
                df = df[~df.index.duplicated(keep='last')]
                df.sort_index(inplace=True)
            
            df.to_parquet(parquet_file, compression='snappy')
            logger.info(f"💾 Saved {len(df)} bars to parquet ({symbol})")
            
        except Exception as e:
            logger.error(f"Parquet save error: {e}")