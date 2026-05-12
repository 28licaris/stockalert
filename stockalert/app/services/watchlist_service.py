"""
Watchlist Service - multi-watchlist live-bar ingestion with per-symbol refcounting.

Source of truth for the persisted watchlist data is `app.db.watchlist_repo`
(ClickHouse). This service is the *behavioural* layer:
  - tracks an in-memory refcount per symbol (= number of active watchlists
    that contain it),
  - subscribes to / unsubscribes from the configured `DataProvider` only when
    a symbol crosses the 0 <-> 1 boundary,
  - keeps baseline symbols subscribed regardless of refcount,
  - forwards every incoming live bar to the OHLCV batcher.

The legacy single-watchlist API (`add`, `remove`, `list_symbols`) is kept as a
thin compatibility shim that operates on the `DEFAULT_WATCHLIST_NAME`
watchlist. It is used by `routes_watchlist.py` until Phase 1.3 introduces the
multi-watchlist HTTP endpoints.

Design constraints honoured here:
  - Provider-agnostic: only `app.providers.base.DataProvider` is referenced.
  - All persistent state goes through the repo (no JSON file writes).
  - Adds / removes are idempotent and safe to retry.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Iterable, Optional

from app.config import get_provider, settings
from app.db import get_bar_batcher, watchlist_repo
from app.providers.base import DataProvider
from app.services.backfill_service import backfill_service

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_NAME = "default"
BASELINE_WATCHLIST_NAME = "baseline"


class WatchlistService:
    """
    Singleton orchestrator that keeps the live-bar subscription set in sync
    with the union of every active watchlist's active members. See module
    docstring for the design.
    """

    def __init__(self, backfill=backfill_service) -> None:
        self._lock = threading.Lock()
        self._provider: Optional[DataProvider] = None
        self._provider_error: Optional[str] = None
        self._started = False
        self._source = (settings.data_source_tag or "").strip() or settings.data_provider

        # Symbol -> # of active watchlists containing it (excludes baseline membership;
        # baseline is tracked separately so refcount==0 cannot evict a baseline symbol).
        self._refcount: dict[str, int] = {}
        # Symbols currently subscribed via the provider.
        self._subscribed: set[str] = set()
        # Symbols that must remain subscribed regardless of refcount.
        self._baseline: set[str] = set()
        # Backfill service (None disables auto-enqueue; used by unit tests).
        self._backfill = backfill

    # ---------- helpers ----------

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

    async def _on_bar(self, bar) -> None:
        """Provider callback - forward every bar to the OHLCV batch writer."""
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

    def _desired_subscriptions(self) -> set[str]:
        """Symbols that *should* be subscribed right now."""
        return self._baseline | {s for s, n in self._refcount.items() if n > 0}

    def _apply_subscription_diff(
        self,
        before: set[str],
        after: set[str],
    ) -> tuple[list[str], list[str]]:
        """
        Compute the (to_subscribe, to_unsubscribe) deltas and call the provider.
        Returns the lists actually issued (empty if no provider).
        """
        to_sub = sorted(after - before)
        to_unsub = sorted(before - after)
        provider = self._ensure_provider() if (to_sub or to_unsub) else self._provider
        if provider is None:
            if to_sub or to_unsub:
                logger.warning(
                    "Watchlist: provider unavailable, skipping subscribe=%s unsubscribe=%s",
                    to_sub, to_unsub,
                )
            return [], []
        try:
            if to_sub:
                provider.subscribe_bars(self._on_bar, to_sub)
            if to_unsub:
                provider.unsubscribe_bars(to_unsub)
        except Exception as e:
            logger.error("Watchlist: provider subscribe/unsubscribe error: %s", e, exc_info=True)
        return to_sub, to_unsub

    def _increment(self, symbols: Iterable[str]) -> None:
        for s in symbols:
            self._refcount[s] = self._refcount.get(s, 0) + 1

    def _decrement(self, symbols: Iterable[str]) -> None:
        for s in symbols:
            n = self._refcount.get(s, 0)
            if n <= 1:
                self._refcount.pop(s, None)
            else:
                self._refcount[s] = n - 1

    def _reconcile(self) -> None:
        """Bring the provider's subscription set in line with `_desired_subscriptions()`."""
        with self._lock:
            desired = self._desired_subscriptions()
            before = set(self._subscribed)
            self._apply_subscription_diff(before, desired)
            self._subscribed = desired

    # ---------- lifecycle ----------

    async def start(self) -> None:
        """
        Rebuild in-memory refcount from the repo and subscribe to the union
        of all active members + baseline. Idempotent.
        """
        if self._started:
            return
        self._started = True

        # Rebuild refcount from repo.
        with self._lock:
            self._refcount.clear()
            self._baseline = watchlist_repo.list_all_active_symbols(kinds={"baseline"})
            for wl in watchlist_repo.list_watchlists():
                if wl["kind"] == "baseline":
                    continue  # baseline tracked separately so we never evict it
                for sym in watchlist_repo.list_members(wl["name"]):
                    self._refcount[sym] = self._refcount.get(sym, 0) + 1

        self._reconcile()
        status = self.status()
        logger.info(
            "Watchlist: started (baseline=%d, refcounted=%d, subscribed=%d)",
            len(self._baseline), len(self._refcount), len(self._subscribed),
        )
        if status["provider_error"]:
            logger.warning("Watchlist: provider error: %s", status["provider_error"])

        # Auto-enqueue backfill for every symbol we just subscribed to.
        # Three resolutions go out in parallel - server-side coverage checks
        # turn each into a no-op if the data is already there.
        #   - QUICK 1-min (30d):    fills streamer-downtime gaps.
        #   - INTRADAY 5-min (270d): so 5m/15m/30m/1h/4h charts cover ~9 months.
        #   - DAILY (2y):            so the 1d chart covers ~2 years.
        subs = sorted(self._subscribed)
        self._enqueue_backfill(subs, kind="quick", days=30)
        self._enqueue_backfill(subs, kind="intraday", days=270)
        self._enqueue_backfill(subs, kind="daily", days=365 * 2)

    async def stop(self) -> None:
        """Unsubscribe everything and tear down the stream."""
        if not self._started:
            return
        self._started = False
        with self._lock:
            currently = sorted(self._subscribed)
            self._subscribed.clear()
            self._refcount.clear()
            self._baseline.clear()
        if self._provider and currently:
            try:
                self._provider.unsubscribe_bars(currently)
            except Exception as e:
                logger.error("Watchlist: unsubscribe_bars during stop failed: %s", e)
        if self._provider:
            try:
                self._provider.stop_stream()
            except Exception as e:
                logger.error("Watchlist: stop_stream failed: %s", e)

    # ---------- multi-watchlist CRUD ----------

    def list_watchlists(self, include_inactive: bool = False) -> list[dict]:
        return watchlist_repo.list_watchlists(include_inactive=include_inactive)

    def get_watchlist(self, name: str) -> Optional[dict]:
        return watchlist_repo.get_watchlist(name)

    def create_watchlist(self, name: str, kind: str = "user", description: str = "") -> dict:
        wl = watchlist_repo.create_watchlist(name, kind=kind, description=description)
        # Newly-created watchlists are empty, so no refcount change. If `kind=='baseline'`
        # the baseline set is updated lazily by add_members/start (an empty baseline
        # watchlist has no effect anyway).
        return wl

    def delete_watchlist(self, name: str) -> bool:
        """Soft-delete and decrement refcount for every member."""
        wl = watchlist_repo.get_watchlist(name)
        if wl is None or not wl["is_active"]:
            return False
        members = watchlist_repo.list_members(name)
        deleted = watchlist_repo.delete_watchlist(name)
        if not deleted:
            return False
        with self._lock:
            if wl["kind"] == "baseline":
                # Baseline membership came from this watchlist; recompute from repo.
                self._baseline = watchlist_repo.list_all_active_symbols(kinds={"baseline"})
            else:
                self._decrement(members)
        self._reconcile()
        return True

    def rename_watchlist(self, old: str, new: str) -> dict:
        # Refcount totals do not change on rename; the same members move with it.
        return watchlist_repo.rename_watchlist(old, new)

    def list_members(self, name: str) -> list[str]:
        return watchlist_repo.list_members(name)

    def add_members(self, name: str, symbols: list[str]) -> dict:
        """
        Add `symbols` to watchlist `name`. Auto-creates the watchlist if missing.
        Returns:
            {
                "watchlist": name,
                "added": [symbols newly activated for this watchlist],
                "members": [full active list after the change],
            }
        """
        # Discover whether this is a baseline watchlist for refcount accounting.
        wl = watchlist_repo.get_watchlist(name)
        kind = wl["kind"] if wl else "user"
        newly = watchlist_repo.add_members(name, symbols)
        with self._lock:
            if kind == "baseline":
                # Pull a fresh baseline set so we account for cross-baseline overlaps.
                self._baseline = watchlist_repo.list_all_active_symbols(kinds={"baseline"})
            else:
                self._increment(newly)
        self._reconcile()
        # Fill in whatever history we're missing for the newly-added symbols.
        # Fire-and-forget. Three backfills go out (one per storage table):
        #   - QUICK 1-min for the last 30d (intraday chart populates immediately).
        #   - INTRADAY 5-min for the last 270d (5m/15m/30m/1h/4h charts).
        #   - DAILY for the last 2y (1d chart).
        # All three short-circuit on the server if coverage is already enough.
        if newly:
            self._enqueue_backfill(newly, kind="quick", days=30)
            self._enqueue_backfill(newly, kind="intraday", days=270)
            self._enqueue_backfill(newly, kind="daily", days=365 * 2)
        return {
            "watchlist": name,
            "added": newly,
            "members": watchlist_repo.list_members(name),
        }

    def remove_members(self, name: str, symbols: list[str]) -> dict:
        wl = watchlist_repo.get_watchlist(name)
        kind = wl["kind"] if wl else "user"
        removed = watchlist_repo.remove_members(name, symbols)
        with self._lock:
            if kind == "baseline":
                self._baseline = watchlist_repo.list_all_active_symbols(kinds={"baseline"})
            else:
                self._decrement(removed)
        self._reconcile()
        return {
            "watchlist": name,
            "removed": removed,
            "members": watchlist_repo.list_members(name),
        }

    def _enqueue_backfill(self, symbols: list[str], *, kind: str, days: int) -> None:
        """Fire-and-forget backfill enqueue. Tolerates no-loop / no-backfill state."""
        if not symbols or self._backfill is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (called from sync test or pre-startup); skip.
            return
        for sym in symbols:
            try:
                if kind == "deep":
                    self._backfill.enqueue_deep(sym, days=days)
                elif kind == "daily":
                    self._backfill.enqueue_daily(sym, days=days)
                elif kind == "intraday":
                    self._backfill.enqueue_intraday(sym, days=days)
                else:
                    self._backfill.enqueue_quick(sym, days=days)
            except Exception as e:
                logger.warning("Auto-backfill enqueue failed for %s: %s", sym, e)

    # ---------- observability ----------

    def streaming_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._subscribed)

    def refcounts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._refcount)

    def status(self) -> dict:
        # `symbols` / `symbol_count` mirror the DEFAULT watchlist members so the
        # current dashboard keeps working. `streaming_symbols` is the new field
        # that reflects the global subscription set across all watchlists +
        # baseline; the Phase 1.3 dashboard will use that one.
        default_members = self.list_symbols()
        with self._lock:
            return {
                "started": self._started,
                "provider": settings.data_provider,
                "provider_ready": self._provider is not None,
                "provider_error": self._provider_error,
                "symbol_count": len(default_members),
                "symbols": default_members,
                "streaming_symbols": sorted(self._subscribed),
                "subscribed_count": len(self._subscribed),
                "baseline_count": len(self._baseline),
                "refcounted_count": len(self._refcount),
                "watchlist_count": len(watchlist_repo.list_watchlists()),
            }

    # ---------- legacy single-watchlist shim (kept for routes_watchlist.py) ----------
    # These operate on DEFAULT_WATCHLIST_NAME so existing /watchlist endpoints
    # keep working until Phase 1.3 swaps them out.

    def list_symbols(self) -> list[str]:
        try:
            return watchlist_repo.list_members(DEFAULT_WATCHLIST_NAME)
        except Exception as e:
            logger.error("Watchlist legacy list_symbols failed: %s", e)
            return []

    def add(self, symbols: list[str]) -> dict:
        result = self.add_members(DEFAULT_WATCHLIST_NAME, symbols)
        return {"added": result["added"], "symbols": result["members"]}

    def remove(self, symbols: list[str]) -> dict:
        result = self.remove_members(DEFAULT_WATCHLIST_NAME, symbols)
        return {"removed": result["removed"], "symbols": result["members"]}


watchlist_service = WatchlistService()
