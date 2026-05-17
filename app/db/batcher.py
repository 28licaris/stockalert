"""Async batched inserts for live OHLCV (N rows or T seconds)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.db import queries

logger = logging.getLogger(__name__)

_DEFAULT_SIZE = 500
_DEFAULT_INTERVAL = 5.0

_batcher: Optional["AsyncBarBatcher"] = None


def get_bar_batcher() -> "AsyncBarBatcher":
    global _batcher
    if _batcher is None:
        _batcher = AsyncBarBatcher()
    return _batcher


def reset_bar_batcher() -> None:
    global _batcher
    _batcher = None


class AsyncBarBatcher:
    def __init__(
        self,
        flush_size: int = _DEFAULT_SIZE,
        flush_interval_seconds: float = _DEFAULT_INTERVAL,
    ):
        self._flush_size = flush_size
        self._flush_interval = flush_interval_seconds
        self._buf: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    async def _tick_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval)
                await self.flush()
        except asyncio.CancelledError:
            pass

    async def add(self, row: Dict[str, Any]) -> None:
        async with self._lock:
            self._buf.append(row)
            if len(self._buf) >= self._flush_size:
                batch = self._buf
                self._buf = []
                await self._send_batch(batch)

    async def flush(self) -> None:
        async with self._lock:
            if not self._buf:
                return
            batch = self._buf
            self._buf = []
        await self._send_batch(batch)

    async def _send_batch(self, batch: List[Dict[str, Any]]) -> None:
        if not batch:
            return
        try:
            await queries.insert_bars_batch_async(batch)
            logger.debug("Flushed %s OHLCV rows to ClickHouse", len(batch))
        except Exception as e:
            logger.error("ClickHouse batch insert failed: %s", e, exc_info=True)
