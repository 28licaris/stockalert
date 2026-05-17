"""
Historical Data Loader Service

Handles loading historical price data with multiple sources and fallback logic.
"""
import logging
import asyncio
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.providers.base import DataProvider
from app.config import settings
from app.db import queries

logger = logging.getLogger(__name__)


def _source_tag() -> str:
    # HistoricalDataLoader only ever loads from the history-role provider, so
    # we tag bars accordingly. Lets STREAM_PROVIDER and HISTORY_PROVIDER
    # operate independently while keeping ClickHouse rows traceable back to
    # the provider that produced them.
    tag = (settings.data_source_tag or "").strip()
    return tag if tag else settings.effective_history_provider


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
        self.parquet_dir = parquet_dir or Path("data/parquet")
        self.use_parquet_cache = (
            use_parquet_cache if use_parquet_cache is not None
            else getattr(settings, 'use_parquet_cache', False)
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
        purpose: str = "monitor"
    ) -> pd.DataFrame:
        """
        Load bars with smart fallback: DB → Parquet → API.

        Args:
            symbol: Stock symbol
            limit: Maximum number of bars
            start: Start datetime
            end: End datetime
            days_lookback: Alternative to start (goes back N days from end)
            purpose: What the data is for (logging only)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if end is None:
            end = datetime.now(timezone.utc)

        if start is None:
            if days_lookback:
                start = end - timedelta(days=days_lookback)
            else:
                start = end - timedelta(days=30)

        logger.info(
            f"Loading {symbol} [{purpose}]: {start.date()} to {end.date()} "
            f"(target: {limit or 'all'} bars, {(end-start).days} days)"
        )

        df = await self._load_from_database(symbol, limit or 10000, start, end)
        if not df.empty and len(df) >= (limit or 0) * 0.8:
            logger.info(f"✅ Database: {len(df)} bars")
            return df

        if not df.empty:
            logger.warning(
                f"⚠️  Database: Only {len(df)}/{limit or 10000} bars for {symbol}"
            )

        if self.use_parquet_cache:
            df = await self._load_from_parquet(symbol, start, end)
            if not df.empty:
                logger.info(f"✅ Parquet: {len(df)} bars")
                return df

        df = await self._fetch_from_provider(symbol, start, end)

        if df.empty:
            logger.warning(f"⚠️  No data available for {symbol}")
            return df

        asyncio.create_task(self._save_to_database(symbol, df))

        if self.use_parquet_cache:
            asyncio.create_task(self._save_to_parquet(symbol, df))

        return df

    async def _load_from_database(
        self,
        symbol: str,
        limit: int,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Load bars from ClickHouse."""
        try:
            return await asyncio.to_thread(
                queries.fetch_bars, symbol, start, end, limit
            )
        except Exception as e:
            logger.error(f"❌ Database error for {symbol}: {e}")
            return pd.DataFrame()

    async def _fetch_from_provider(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Fetch data from provider with timeout."""
        try:
            logger.info(f"🌐 Fetching {symbol} from API...")

            df = await asyncio.wait_for(
                self.provider.historical_df(symbol, start, end, timeframe="1Min"),
                timeout=30.0
            )

            if df.empty:
                logger.warning(f"⚠️  Provider returned empty data for {symbol}")
            else:
                logger.info(f"✅ Fetched {len(df)} bars from API")

            return df

        except asyncio.TimeoutError:
            logger.error(f"❌ API timeout after 30s for {symbol}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"❌ API error for {symbol}: {e}")
            return pd.DataFrame()

    async def _save_to_database(self, symbol: str, df: pd.DataFrame):
        """Save bars to ClickHouse with batch inserts."""
        if df.empty:
            return

        try:
            batch_size = 1000
            total_records = len(df)
            src = _source_tag()

            logger.info(f"💾 Saving {total_records} bars to ClickHouse (batches of {batch_size})...")

            for batch_num, start_idx in enumerate(range(0, total_records, batch_size), 1):
                end_idx = min(start_idx + batch_size, total_records)
                batch_df = df.iloc[start_idx:end_idx]

                records = []
                for ts, row in batch_df.iterrows():
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    records.append({
                        'symbol': symbol,
                        'timestamp': ts,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']),
                        'vwap': float(row.get('vwap', 0) or 0),
                        'trade_count': int(row.get('trade_count', 0) or 0),
                        'source': src,
                    })

                await queries.insert_bars_batch_async(records)

                logger.info(
                    f"   💾 Batch {batch_num}/{(total_records + batch_size - 1) // batch_size}: "
                    f"Saved {end_idx}/{total_records} bars"
                )

            logger.info(f"✅ Successfully saved all {total_records} bars")

        except Exception as e:
            logger.error(f"❌ Failed to save to ClickHouse: {e}")

    async def _load_from_parquet(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """Load from parquet cache (not implemented yet)."""
        return pd.DataFrame()

    async def _save_to_parquet(self, symbol: str, df: pd.DataFrame):
        """Save to parquet cache (not implemented yet)."""
        pass
