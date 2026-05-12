"""
Watchlist Service - continuously ingest live bars for a configurable set of symbols.

Distinct from `monitor_manager` (which runs divergence detection): this service only
subscribes to the provider, persists every bar to ClickHouse via the shared batcher,
and survives process restarts by persisting the symbol set to disk.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Optional

from app.config import get_provider, settings
from app.db import get_bar_batcher
from app.providers.base import DataProvider

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_PATH = "data/watchlist.json"


class WatchlistService:
    """
    Singleton service that subscribes to live bars for every symbol in a persistent watchlist
    and forwards each bar to the OHLCV batcher.
    """

    def __init__(self, path: str = DEFAULT_WATCHLIST_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._symbols: set[str] = set()
        self._provider: Optional[DataProvider] = None
        self._provider_error: Optional[str] = None
        self._started = False
        self._source = (settings.data_source_tag or "").strip() or settings.data_provider

    def _load_from_disk(self) -> set[str]:
        if not os.path.isfile(self._path):
            return set()
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            raw = data.get("symbols", []) if isinstance(data, dict) else data
            return {str(s).strip().upper() for s in raw if str(s).strip()}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Watchlist: could not read %s: %s", self._path, e)
            return set()

    def _save_to_disk(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w") as f:
                json.dump({"symbols": sorted(self._symbols)}, f, indent=2)
        except OSError as e:
            logger.error("Watchlist: failed to write %s: %s", self._path, e)

    async def _on_bar(self, bar) -> None:
        """Callback invoked by the data provider for each live bar."""
        ts = getattr(bar, "timestamp", None) or getattr(bar, "ts", None)
        symbol = getattr(bar, "ticker", None) or getattr(bar, "symbol", None)
        if not ts or not symbol:
            return
        try:
            await get_bar_batcher().add(
                {
                    "symbol": symbol,
                    "timestamp": ts,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": float(getattr(bar, "volume", 0) or 0),
                    "vwap": float(getattr(bar, "vwap", 0) or 0),
                    "trade_count": int(getattr(bar, "trade_count", 0) or 0),
                    "source": self._source,
                }
            )
        except Exception as e:
            logger.error("Watchlist: failed to enqueue bar for %s: %s", symbol, e)

    def _ensure_provider(self) -> Optional[DataProvider]:
        if self._provider is not None:
            return self._provider
        try:
            self._provider = get_provider()
            self._provider_error = None
            logger.info("Watchlist: data provider initialized (%s)", settings.data_provider)
            return self._provider
        except Exception as e:
            self._provider_error = str(e)
            logger.error("Watchlist: could not initialize data provider: %s", e)
            return None

    async def start(self) -> None:
        """Load the persisted watchlist and subscribe to live bars for all symbols."""
        if self._started:
            return
        self._started = True
        with self._lock:
            self._symbols = self._load_from_disk()
        if not self._symbols:
            logger.info("Watchlist: empty on startup; add symbols via POST /watchlist/add")
            return
        provider = self._ensure_provider()
        if provider is None:
            logger.warning(
                "Watchlist: provider unavailable (%s); %d symbol(s) will not stream until fixed",
                self._provider_error,
                len(self._symbols),
            )
            return
        try:
            provider.subscribe_bars(self._on_bar, sorted(self._symbols))
            logger.info("Watchlist: subscribed to %d symbol(s): %s", len(self._symbols), sorted(self._symbols))
        except Exception as e:
            logger.error("Watchlist: subscribe_bars failed: %s", e, exc_info=True)

    async def stop(self) -> None:
        """Unsubscribe and tear down the provider stream."""
        if not self._started:
            return
        self._started = False
        if self._provider and self._symbols:
            try:
                self._provider.unsubscribe_bars(sorted(self._symbols))
            except Exception as e:
                logger.error("Watchlist: unsubscribe_bars failed: %s", e)
        if self._provider:
            try:
                self._provider.stop_stream()
            except Exception as e:
                logger.error("Watchlist: stop_stream failed: %s", e)

    def list_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._symbols)

    def status(self) -> dict:
        return {
            "started": self._started,
            "provider": settings.data_provider,
            "provider_ready": self._provider is not None,
            "provider_error": self._provider_error,
            "symbol_count": len(self._symbols),
            "symbols": self.list_symbols(),
            "watchlist_path": self._path,
        }

    def add(self, symbols: list[str]) -> dict:
        """Add symbols and (if running) subscribe to their bar stream."""
        new = {s.strip().upper() for s in symbols if s and s.strip()}
        if not new:
            return {"added": [], "symbols": self.list_symbols()}
        with self._lock:
            actually_new = sorted(new - self._symbols)
            self._symbols |= new
            self._save_to_disk()
            current = sorted(self._symbols)
        if actually_new and self._started:
            provider = self._ensure_provider()
            if provider is not None:
                try:
                    provider.subscribe_bars(self._on_bar, actually_new)
                except Exception as e:
                    logger.error("Watchlist: subscribe_bars failed for %s: %s", actually_new, e)
        return {"added": actually_new, "symbols": current}

    def remove(self, symbols: list[str]) -> dict:
        """Remove symbols and (if running) unsubscribe from their bar stream."""
        gone = {s.strip().upper() for s in symbols if s and s.strip()}
        if not gone:
            return {"removed": [], "symbols": self.list_symbols()}
        with self._lock:
            actually_removed = sorted(gone & self._symbols)
            self._symbols -= gone
            self._save_to_disk()
            current = sorted(self._symbols)
        if actually_removed and self._provider:
            try:
                self._provider.unsubscribe_bars(actually_removed)
            except Exception as e:
                logger.error("Watchlist: unsubscribe_bars failed for %s: %s", actually_removed, e)
        return {"removed": actually_removed, "symbols": current}


watchlist_service = WatchlistService()
