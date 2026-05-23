"""StreamService — owns the Schwab live subscription set + the
`stream_universe` CH table.

Architecture: see docs/frontend_api_contracts.md §10.1 (locked
sticky-universe model). The CH `stream_universe` table is the source
of truth for what's currently being streamed. Watchlists auto-extend
this universe via `ensure_streaming()` but **never** remove from it
(sticky semantics: only an explicit StreamService.remove call strips
a symbol from the stream).

Lifecycle:
  - `start()` reads the active universe and subscribes everything.
  - `add()` writes a CH row and immediately subscribes the symbol.
  - `remove()` marks the row inactive and immediately unsubscribes.
  - `stop()` unsubscribes everything.

All callbacks emit bars to the OHLCV batcher with source tag
`{provider}-stream` so `live_lake_writer` can disambiguate them
from REST-backfilled rows.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.config import get_stream_provider, settings
from app.db.client import get_client
from app.providers.base import DataProvider

logger = logging.getLogger(__name__)


STREAM_UNIVERSE_TABLE = "stream_universe"


def _normalize_symbol(s: str) -> str:
    """Use the watchlist repo's normalization so cross-service comparisons match."""
    from app.db import watchlist_repo

    return watchlist_repo.normalize_member_symbol(s)


def _ts(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


class StreamService:
    """Owns Schwab live subscriptions and the stream-universe CH table.

    Singleton (`stream_service`) is the production instance. The class
    constructor + `from_settings()` exist for test injection.
    """

    DEFAULT_OWNER = "default-tenant"

    def __init__(self, *, bar_batcher_factory=None) -> None:
        self._lock = threading.Lock()
        self._provider: Optional[DataProvider] = None
        self._provider_error: Optional[str] = None
        self._started = False
        self._subscribed: set[str] = set()
        # The asyncio event loop captured at `start()` time. API routes
        # call `add` / `remove` via `asyncio.to_thread`, which strips the
        # loop from the worker thread; we use `run_coroutine_threadsafe`
        # to route provider.subscribe_bars + warmup back to this loop.
        # `None` outside a started service (tests / pre-start).
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # Source-tag every streamed bar so the live_lake_writer (TA-5.7)
        # can distinguish stream-sourced rows from REST-backfilled ones
        # (which use the bare provider name). Suffix `-stream` is the
        # contract: live_lake_writer reads `WHERE source = "{provider}-stream"`.
        base_tag = (
            (settings.data_source_tag or "").strip()
            or settings.effective_stream_provider
        )
        if settings.data_source_tag:
            self._source = base_tag
        else:
            self._source = f"{base_tag}-stream" if base_tag else ""

        self._bar_batcher_factory = bar_batcher_factory

    @classmethod
    def from_settings(cls) -> "StreamService":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Provider plumbing
    # ─────────────────────────────────────────────────────────────────

    def ensure_provider(self) -> Optional[DataProvider]:
        """Lazy-init + return the live streaming provider.

        Constructs the provider on first call (post-CV15 routes that need
        the provider before StreamService.start() runs — e.g.
        `routes_movers` — call this instead of `get_provider`). On
        construction failure, records the error and returns None so the
        caller can return a clean 503.
        """
        if self._provider is not None:
            return self._provider
        try:
            self._provider = get_stream_provider()
            self._provider_error = None
            logger.info(
                "Stream: provider initialized (%s)",
                settings.effective_stream_provider,
            )
            return self._provider
        except Exception as e:  # noqa: BLE001 — boundary
            self._provider_error = str(e)
            logger.error("Stream: could not initialize provider: %s", e)
            return None

    def get_provider(self) -> Optional[DataProvider]:
        """Read the live streaming provider handle WITHOUT initializing it.

        Returns `None` if the provider hasn't been initialized yet (e.g.
        StreamService.start() hasn't run, or initialization failed).
        Callers that want lazy-init on first access should use
        `ensure_provider()` instead.
        """
        return self._provider

    async def _on_bar(self, bar) -> None:
        """Forward every bar to the OHLCV batch writer."""
        from app.db import get_bar_batcher

        ts = getattr(bar, "timestamp", None) or getattr(bar, "ts", None)
        symbol = getattr(bar, "ticker", None) or getattr(bar, "symbol", None)
        if not ts or not symbol:
            return
        try:
            batcher = (
                self._bar_batcher_factory()
                if self._bar_batcher_factory
                else get_bar_batcher()
            )
            await batcher.add(
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
        except Exception as e:  # noqa: BLE001 — boundary
            logger.error("Stream: failed to enqueue bar for %s: %s", symbol, e)

    def _apply_subscription_diff(
        self,
        before: set[str],
        after: set[str],
    ) -> tuple[list[str], list[str]]:
        to_sub = sorted(after - before)
        to_unsub = sorted(before - after)
        if not to_sub and not to_unsub:
            return [], []
        provider = self.ensure_provider()
        if provider is None:
            logger.warning(
                "Stream: provider unavailable, "
                "skipping subscribe=%s unsubscribe=%s",
                to_sub, to_unsub,
            )
            return [], []

        # `provider.subscribe_bars` (Schwab) needs a running asyncio
        # loop so it can capture it for sending WS commands. API
        # routes invoke this service via `asyncio.to_thread`, which
        # leaves the worker thread with no running loop — we route
        # through the captured main loop in that case.
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        try:
            if in_loop:
                if to_sub:
                    provider.subscribe_bars(self._on_bar, to_sub)
                if to_unsub:
                    provider.unsubscribe_bars(to_unsub)
            elif self._main_loop is not None and not self._main_loop.is_closed():
                if to_sub:
                    asyncio.run_coroutine_threadsafe(
                        self._subscribe_on_main(to_sub),
                        self._main_loop,
                    ).result(timeout=5)
                if to_unsub:
                    asyncio.run_coroutine_threadsafe(
                        self._unsubscribe_on_main(to_unsub),
                        self._main_loop,
                    ).result(timeout=5)
            else:
                logger.warning(
                    "Stream: no event loop available "
                    "(main loop not captured); skipping "
                    "subscribe=%s unsubscribe=%s",
                    to_sub, to_unsub,
                )
                return [], []
        except Exception as e:  # noqa: BLE001 — boundary
            logger.error(
                "Stream: subscribe/unsubscribe error: %s", e, exc_info=True
            )
        return to_sub, to_unsub

    async def _subscribe_on_main(self, to_sub: list[str]) -> None:
        """Helper invoked on the main loop from worker-thread callers."""
        if self._provider is not None:
            self._provider.subscribe_bars(self._on_bar, to_sub)

    async def _unsubscribe_on_main(self, to_unsub: list[str]) -> None:
        if self._provider is not None:
            self._provider.unsubscribe_bars(to_unsub)

    # ─────────────────────────────────────────────────────────────────
    # CH repo
    # ─────────────────────────────────────────────────────────────────

    def _read_universe(self, *, owner_id: Optional[str] = None) -> list[dict]:
        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()
        rows = client.query(
            f"""
            SELECT symbol, asset_type, added_at, added_by, notes
            FROM {STREAM_UNIVERSE_TABLE}
            FINAL
            WHERE owner_id = {{owner:String}}
              AND is_active = 1
            ORDER BY added_at ASC, symbol ASC
            """,
            parameters={"owner": owner},
        )
        return [
            {
                "symbol": r[0],
                "asset_type": r[1] or "",
                "added_at": _ts(r[2]),
                "added_by": r[3] or "",
                "notes": r[4] or "",
            }
            for r in rows.result_rows
        ]

    def _is_active(self, symbol: str, *, owner_id: Optional[str] = None) -> bool:
        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()
        rows = client.query(
            f"""
            SELECT 1 FROM {STREAM_UNIVERSE_TABLE} FINAL
            WHERE owner_id = {{owner:String}}
              AND symbol = {{sym:String}}
              AND is_active = 1
            LIMIT 1
            """,
            parameters={"owner": owner, "sym": symbol},
        )
        return bool(rows.result_rows)

    def _write_row(
        self,
        sym: str,
        owner: str,
        is_active: int,
        *,
        asset_type: str = "",
        added_by: str = "",
        notes: str = "",
    ) -> None:
        version = int(datetime.now(timezone.utc).timestamp() * 1000)
        client = get_client()
        client.insert(
            STREAM_UNIVERSE_TABLE,
            [[
                sym,
                owner,
                asset_type,
                datetime.now(timezone.utc),
                added_by,
                notes,
                is_active,
                version,
            ]],
            column_names=[
                "symbol", "owner_id", "asset_type", "added_at",
                "added_by", "notes", "is_active", "version",
            ],
        )

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe to every active symbol in the stream_universe table.

        Idempotent. Provider failure is logged but does not raise — the
        cockpit's status tile surfaces `provider_error` so the operator
        can act.
        """
        if self._started:
            return
        self._started = True
        # Capture the main event loop so worker-thread callers (API
        # routes invoking us via asyncio.to_thread) can schedule
        # provider.subscribe_bars + warmup tasks back onto it.
        self._main_loop = asyncio.get_running_loop()

        # Empty-table bootstrap: on first startup the CH table will be
        # empty even if SEED_SYMBOLS has 100 tickers. Populate it so the
        # stream actually has something to subscribe to.
        try:
            self.bootstrap_if_empty()
        except Exception as e:  # noqa: BLE001 — boundary
            logger.warning("Stream: bootstrap_if_empty failed: %s", e)

        try:
            active = {row["symbol"] for row in self._read_universe()}
        except Exception as e:  # noqa: BLE001 — boundary
            logger.error("Stream: could not read universe table: %s", e)
            active = set()

        with self._lock:
            before = set(self._subscribed)
            self._apply_subscription_diff(before, active)
            self._subscribed = active

        logger.info(
            "Stream: started (subscribed=%d, provider=%s)",
            len(self._subscribed),
            settings.effective_stream_provider,
        )
        if self._provider_error:
            logger.warning("Stream: provider error: %s", self._provider_error)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        with self._lock:
            currently = sorted(self._subscribed)
            self._subscribed.clear()
        if self._provider and currently:
            try:
                self._provider.unsubscribe_bars(currently)
            except Exception as e:  # noqa: BLE001 — boundary
                logger.error("Stream: unsubscribe during stop failed: %s", e)
        if self._provider:
            try:
                self._provider.stop_stream()
            except Exception as e:  # noqa: BLE001 — boundary
                logger.error("Stream: stop_stream failed: %s", e)
        self._main_loop = None

    # ─────────────────────────────────────────────────────────────────
    # Public API — universe CRUD
    # ─────────────────────────────────────────────────────────────────

    def list_universe(self, *, owner_id: Optional[str] = None) -> list[dict]:
        return self._read_universe(owner_id=owner_id)

    def list_active_symbols(
        self, *, owner_id: Optional[str] = None,
    ) -> set[str]:
        """Lightweight: just the active symbols, no metadata.

        Used by `get_active_universe()` and the nightly job universe
        filters. Returns a set so callers can union with seed fallbacks
        cheaply. Tolerates CH outages by returning an empty set (the
        caller decides the fallback).
        """
        try:
            return {row["symbol"] for row in self._read_universe(owner_id=owner_id)}
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning(
                "Stream: list_active_symbols read failed: %s", exc,
            )
            return set()

    def is_streaming(self, symbol: str) -> bool:
        sym = _normalize_symbol(symbol)
        with self._lock:
            return sym in self._subscribed

    def add(
        self,
        symbol: str,
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        asset_type: str = "",
        notes: str = "",
    ) -> dict:
        """Add a symbol to the stream universe + subscribe it immediately.

        Idempotent: re-adding an already-active symbol returns `changed=[]`
        and skips the subscribe (already subscribed).
        """
        sym = _normalize_symbol(symbol)
        if not sym:
            raise ValueError(f"invalid symbol {symbol!r} after normalization")

        owner = owner_id or self.DEFAULT_OWNER
        already_active = self._is_active(sym, owner_id=owner)
        self._write_row(
            sym, owner, is_active=1,
            asset_type=asset_type, added_by=added_by, notes=notes,
        )

        if not already_active:
            with self._lock:
                if sym not in self._subscribed:
                    before = set(self._subscribed)
                    after = before | {sym}
                    self._apply_subscription_diff(before, after)
                    self._subscribed = after
            self._enqueue_warmup([sym])

        items = self._read_universe(owner_id=owner)
        return {
            "operation": "add",
            "changed": [] if already_active else [sym],
            "items": items,
            "count": len(items),
        }

    def remove(
        self, symbol: str, *, owner_id: Optional[str] = None,
    ) -> dict:
        """Remove a symbol from the stream universe + unsubscribe.

        This is the ONLY path that strips a symbol from the live stream;
        watchlist removes do NOT call this (sticky-universe invariant).
        """
        sym = _normalize_symbol(symbol)
        if not sym:
            raise ValueError(f"invalid symbol {symbol!r} after normalization")

        owner = owner_id or self.DEFAULT_OWNER
        was_active = self._is_active(sym, owner_id=owner)
        if was_active:
            self._write_row(sym, owner, is_active=0)
            with self._lock:
                if sym in self._subscribed:
                    before = set(self._subscribed)
                    after = before - {sym}
                    self._apply_subscription_diff(before, after)
                    self._subscribed = after

        items = self._read_universe(owner_id=owner)
        return {
            "operation": "remove",
            "changed": [sym] if was_active else [],
            "items": items,
            "count": len(items),
        }

    def import_bulk(
        self,
        symbols: list[str],
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        notes: str = "",
    ) -> dict:
        """Bulk add. Idempotent. Returns one combined result."""
        owner = owner_id or self.DEFAULT_OWNER
        changed: list[str] = []
        for raw in symbols:
            sym = _normalize_symbol(raw)
            if not sym:
                continue
            result = self.add(
                sym,
                owner_id=owner,
                added_by=added_by,
                notes=notes,
            )
            changed.extend(result["changed"])
        items = self._read_universe(owner_id=owner)
        return {
            "operation": "import",
            "changed": changed,
            "items": items,
            "count": len(items),
        }

    def ensure_streaming(
        self,
        symbols: Iterable[str],
        *,
        added_by: str = "",
        source: str = "watchlist",
    ) -> list[str]:
        """Auto-extend the universe for symbols not yet present.

        Called by `WatchlistService.add_members` so a user adding a
        non-universe symbol to a watchlist auto-promotes it into the
        stream (per locked sticky-universe model §10.1).

        Returns the symbols actually added (no-ops for already-active
        symbols are excluded).
        """
        added: list[str] = []
        notes_for_source = f"auto-added by {source}"
        for raw in symbols:
            sym = _normalize_symbol(raw)
            if not sym:
                continue
            if self._is_active(sym):
                continue
            result = self.add(
                sym, added_by=added_by, notes=notes_for_source,
            )
            added.extend(result["changed"])
        return added

    # ─────────────────────────────────────────────────────────────────
    # Bootstrap (first-run only)
    # ─────────────────────────────────────────────────────────────────

    def is_empty(self, *, owner_id: Optional[str] = None) -> bool:
        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()
        rows = client.query(
            f"""
            SELECT 1 FROM {STREAM_UNIVERSE_TABLE} FINAL
            WHERE owner_id = {{owner:String}} AND is_active = 1
            LIMIT 1
            """,
            parameters={"owner": owner},
        )
        return not rows.result_rows

    def bootstrap_if_empty(
        self, *, owner_id: Optional[str] = None,
    ) -> tuple[bool, int]:
        """Seed the universe from SEED_SYMBOLS ∪ active-watchlist members
        iff the table is empty. Returns `(did_bootstrap, count)`.
        """
        owner = owner_id or self.DEFAULT_OWNER
        if not self.is_empty(owner_id=owner):
            return False, len(self._read_universe(owner_id=owner))

        from app.data.seed_universe import SEED_SYMBOLS

        seed_pool: set[str] = {s for s in SEED_SYMBOLS if s}
        try:
            from app.db import watchlist_repo

            seed_pool.update(watchlist_repo.list_all_active_symbols())
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning(
                "Stream bootstrap: could not read watchlists: %s", exc,
            )

        if not seed_pool:
            return False, 0

        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)
        rows = [
            [
                sym, owner, "", now, "bootstrap",
                "imported from SEED_SYMBOLS + watchlists", 1, version,
            ]
            for sym in sorted(seed_pool)
        ]
        client = get_client()
        client.insert(
            STREAM_UNIVERSE_TABLE,
            rows,
            column_names=[
                "symbol", "owner_id", "asset_type", "added_at",
                "added_by", "notes", "is_active", "version",
            ],
        )
        logger.info(
            "stream_universe: bootstrapped with %d symbols", len(rows),
        )
        return True, len(rows)

    # ─────────────────────────────────────────────────────────────────
    # Observability
    # ─────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        try:
            universe_count = len(self._read_universe())
        except Exception:  # noqa: BLE001 — boundary
            universe_count = 0
        with self._lock:
            return {
                "started": self._started,
                "provider": settings.effective_stream_provider,
                "provider_ready": self._provider is not None,
                "provider_error": self._provider_error,
                "streaming_count": len(self._subscribed),
                "streaming_symbols": sorted(self._subscribed),
                "universe_count": universe_count,
            }

    # ─────────────────────────────────────────────────────────────────
    # Backfill warmup (lifted from watchlist_service; only fires when
    # lake_warmup_enabled is set)
    # ─────────────────────────────────────────────────────────────────

    def _enqueue_warmup(self, symbols: list[str]) -> None:
        from app.config import settings as _s

        if not symbols:
            return
        if not getattr(_s, "lake_warmup_enabled", False):
            return

        # Same dispatch story as _apply_subscription_diff: if we're in
        # the main asyncio loop, create_task directly; if we're in a
        # worker thread (asyncio.to_thread from an API route), schedule
        # onto the captured main loop via run_coroutine_threadsafe.
        try:
            loop = asyncio.get_running_loop()
            for sym in symbols:
                loop.create_task(
                    self._lake_warmup_one(sym),
                    name=f"stream_warmup_{sym}",
                )
            return
        except RuntimeError:
            pass

        if self._main_loop is not None and not self._main_loop.is_closed():
            for sym in symbols:
                asyncio.run_coroutine_threadsafe(
                    self._lake_warmup_one(sym),
                    self._main_loop,
                )
        else:
            logger.warning(
                "Stream: no event loop captured; warmup of %s skipped "
                "(lake_warmup_enabled is on but the "
                "service was never started)", symbols,
            )

    async def _lake_warmup_one(self, symbol: str) -> None:
        """Hot-path warmup chain for a brand-new symbol (CV12 / v2).

        Function + flag name kept stable through Phase 1C; rename
        to `_lake_warmup_one` / `lake_warmup_enabled` happens in CV14.

        Two PARALLEL phases (no on-demand silver_build step — v2's
        equities.polygon_adjusted is populated whole-market weekly
        by the Spark adjustment job, so per-symbol Python compute is
        no longer needed):

          a. schwab_rest_tip_fill(symbol)
             — Schwab REST → equities.schwab_universe + CH ohlcv_1m
               for the ~48-day lookback window. Dual-write so the
               chart's "today" is correct immediately.

          b. lake_to_ch_backfill(symbol)
             — equities.polygon_adjusted → CH ohlcv_1m for 730 days
               (DEFAULT_BACKFILL_DAYS). polygon_adjusted is bucketed
               by symbol (CV1's bucket(32, symbol)), so single-symbol
               scans read 1/32 of each month — typically ~3-5s for
               2y, ~5-8s for 5y on a warm Iceberg cache.

        Both writers target CH.ohlcv_1m (ReplacingMergeTree); they
        don't compete because tip_fill writes the recent 48 days and
        lake covers the deep history before that. Overlapping rows
        merge cleanly.

        Errors are logged but never raised: the symbol is already
        subscribed to Schwab WS so live ticks land regardless, and
        the chart's worst-case fallback is "live-only until the next
        nightly Polygon refresh catches up".

        Wall-clock target (the v2 spec's latency gate <5s for new
        symbols): chart populated end-to-end in under 10 seconds
        for actively-traded names. Brand-new IPOs without deep-
        history coverage get the 48-day tip_fill window
        immediately + the deep history on the next weekly Spark run.
        """
        tip_task = asyncio.create_task(
            self._warmup_tip_fill_one(symbol),
            name=f"stream_warmup_tip_fill_{symbol}",
        )
        lake_task = asyncio.create_task(
            self._warmup_lake_to_ch_one(symbol),
            name=f"stream_warmup_lake_to_ch_{symbol}",
        )
        await asyncio.gather(tip_task, lake_task, return_exceptions=False)

    async def _warmup_tip_fill_one(self, symbol: str) -> bool:
        """Schwab REST tip-fill for the 48-day window."""
        try:
            from app.services.ingest.schwab_tip_fill import SchwabTipFill

            tip = SchwabTipFill.from_settings()
            tf = await tip.tip_fill(symbol)
            logger.info(
                "Stream warmup tip-fill: %s fetched=%d equities=%d ch=%d",
                symbol, tf.bars_fetched,
                tf.bars_written_bronze, tf.bars_written_ch,
            )
            if not tf.succeeded:
                logger.warning(
                    "Stream warmup tip-fill for %s failed: %s",
                    symbol, tf.error,
                )
            return tf.succeeded
        except Exception as e:  # noqa: BLE001 — boundary
            logger.exception(
                "Stream warmup tip-fill for %s raised: %s", symbol, e,
            )
            return False

    async def _warmup_lake_to_ch_one(self, symbol: str) -> bool:
        """Bulk-copy equities.polygon_adjusted → CH.ohlcv_1m so the
        chart can serve 5y of deep history immediately.

        Sources from equities.polygon_adjusted via
        AdjustedOhlcvReader → LakeToChBackfill (CV15 rename pass).
        """
        try:
            from app.services.ingest.lake_to_ch_backfill import (
                DEFAULT_BACKFILL_DAYS,
                LakeToChBackfill,
            )

            lake_to_ch = LakeToChBackfill.from_settings()
            s2c = await asyncio.to_thread(
                lake_to_ch.backfill_symbol,
                symbol, days=DEFAULT_BACKFILL_DAYS,
            )
            logger.info(
                "Stream warmup lake→CH: %s read=%d written=%d snapshot=%s",
                symbol, s2c.bars_read, s2c.bars_written, s2c.snapshot_id,
            )
            if not s2c.succeeded:
                logger.warning(
                    "Stream warmup lake→CH for %s failed: %s",
                    symbol, s2c.error,
                )
            return s2c.succeeded
        except Exception as e:  # noqa: BLE001 — boundary
            logger.exception(
                "Stream warmup lake→CH for %s raised: %s", symbol, e,
            )
            return False


# Module-level singleton — production callers go through this.
stream_service = StreamService()
