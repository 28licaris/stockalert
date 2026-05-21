"""
Watchlist Service - multi-watchlist CRUD over `app.db.watchlist_repo`.

Architecture: per docs/frontend_api_contracts.md §10.1 (locked sticky-
universe model), the *live streaming* set is owned by
`app.services.stream.stream_service`, not this module. Watchlists are
pure user-facing organization:

  - Adding a symbol to a watchlist auto-extends the stream universe
    (`stream_service.ensure_streaming`) if the symbol isn't already
    being streamed. The new symbol gets a silver-derived warmup so
    its chart is usable immediately.
  - Removing a symbol from a watchlist does NOT touch the stream.
    Universe membership is sticky; only an explicit
    `stream_service.remove` (the cockpit's Stream Universe page)
    strips a symbol from the live stream.

This module is therefore a thin CRUD shim on top of `watchlist_repo`
plus the auto-extend hook into the stream service.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.db import watchlist_repo
from app.services.ingest.backfill_service import backfill_service

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_NAME = "default"
BASELINE_WATCHLIST_NAME = "baseline"


class WatchlistService:
    """Multi-watchlist CRUD + auto-extend hook into StreamService.

    Singleton (`watchlist_service`) is the production instance.
    """

    def __init__(self, backfill=backfill_service) -> None:
        self._started = False
        self._backfill = backfill

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle (no-op subscription-wise; StreamService owns that)
    # ─────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Mark service started. Stream subscriptions are owned by
        `stream_service` and started by main_api lifespan separately.
        """
        if self._started:
            return
        self._started = True
        logger.info("Watchlist service started (CRUD-only; streaming owned by stream_service)")

    async def stop(self) -> None:
        self._started = False

    # ─────────────────────────────────────────────────────────────────
    # Multi-watchlist CRUD
    # ─────────────────────────────────────────────────────────────────

    def list_watchlists(self, include_inactive: bool = False) -> list[dict]:
        return watchlist_repo.list_watchlists(include_inactive=include_inactive)

    def get_watchlist(self, name: str) -> Optional[dict]:
        return watchlist_repo.get_watchlist(name)

    def create_watchlist(
        self, name: str, kind: str = "user", description: str = "",
    ) -> dict:
        return watchlist_repo.create_watchlist(
            name, kind=kind, description=description,
        )

    def delete_watchlist(self, name: str) -> bool:
        """Soft-delete a watchlist. Sticky-universe invariant: members
        stay in the stream universe (only StreamService.remove can
        evict them from streaming).
        """
        wl = watchlist_repo.get_watchlist(name)
        if wl is None or not wl["is_active"]:
            return False
        return watchlist_repo.delete_watchlist(name)

    def rename_watchlist(self, old: str, new: str) -> dict:
        return watchlist_repo.rename_watchlist(old, new)

    def list_members(self, name: str) -> list[str]:
        return watchlist_repo.list_members(name)

    def add_members(self, name: str, symbols: list[str]) -> dict:
        """Add `symbols` to watchlist `name`. Auto-creates the watchlist
        if missing.

        Side-effects (locked sticky-universe model):
          1. CH write: `watchlist_repo.add_members`.
          2. Stream auto-extend: any newly-added symbol not already in
             the stream universe gets promoted via
             `stream_service.ensure_streaming` (which subscribes Schwab
             + queues a silver-derived warmup).
          3. Backfill warmup: for any symbol already in the stream but
             new to this watchlist, fire the legacy backfill path if
             `lake_warmup_enabled` is off (otherwise
             stream_service.add already fired the silver path).

        Returns:
            {
                "watchlist": name,
                "added": [symbols newly active in this watchlist],
                "members": [full active list after the change],
            }
        """
        newly = watchlist_repo.add_members(name, symbols)

        if newly:
            # Auto-extend the stream universe (sticky). Lazy import to
            # avoid a circular at module load. For symbols not yet in
            # the stream, this subscribes Schwab + (when the flag is
            # on) fires the silver-derived warmup chain in stream_service.
            try:
                from app.services.stream import stream_service

                promoted = stream_service.ensure_streaming(
                    newly, source=f"watchlist:{name}",
                )
                if promoted:
                    logger.info(
                        "Watchlist '%s': auto-extended stream universe with %d new symbol(s): %s",
                        name, len(promoted), promoted,
                    )
            except Exception as exc:  # noqa: BLE001 — boundary
                logger.warning(
                    "Watchlist '%s': stream auto-extend failed: %s",
                    name, exc,
                )

            # Legacy quick/intraday/daily backfill — fires only when the
            # silver-derived flag is OFF. When the flag is ON, stream
            # service's add() already handles warmup for symbols it just
            # promoted; symbols already in the stream are assumed warm
            # (the nightly silver chain keeps them current).
            try:
                from app.config import settings as _s

                use_legacy = not getattr(
                    _s, "lake_warmup_enabled", False,
                )
            except Exception:  # noqa: BLE001 — boundary
                use_legacy = True
            if use_legacy:
                self._enqueue_warmup_legacy(newly)

        return {
            "watchlist": name,
            "added": newly,
            "members": watchlist_repo.list_members(name),
        }

    def remove_members(self, name: str, symbols: list[str]) -> dict:
        """Remove `symbols` from watchlist `name`.

        Sticky-universe invariant: this does NOT touch the stream
        universe or any Schwab subscriptions. Symbols continue to
        stream until an operator explicitly calls
        `stream_service.remove`.
        """
        removed = watchlist_repo.remove_members(name, symbols)
        return {
            "watchlist": name,
            "removed": removed,
            "members": watchlist_repo.list_members(name),
        }

    # ─────────────────────────────────────────────────────────────────
    # Backfill warmup (legacy Path ②, used only when silver-derived
    # add_members is disabled)
    # ─────────────────────────────────────────────────────────────────

    def _enqueue_warmup_legacy(self, symbols: list[str]) -> None:
        """Fire-and-forget: quick/intraday/daily backfill via
        `backfill_service`. Caller controls the silver-vs-legacy flag
        decision; this method just dispatches.
        """
        if not symbols or self._backfill is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for sym in symbols:
            try:
                self._backfill.enqueue_quick(sym, days=30)
                self._backfill.enqueue_intraday(sym, days=270)
                self._backfill.enqueue_daily(sym, days=365 * 2)
            except Exception as e:  # noqa: BLE001 — boundary
                logger.warning("Auto-backfill enqueue failed for %s: %s", sym, e)

    # ─────────────────────────────────────────────────────────────────
    # Observability
    # ─────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Compose a status dict for the legacy /watchlist endpoint.

        Streaming fields delegate to `stream_service` so existing
        cockpit code that reads `streaming_symbols` / `subscribed_count`
        continues to work without modification.
        """
        default_members = self.list_symbols()
        stream_status: dict = {}
        try:
            from app.services.stream import stream_service

            stream_status = stream_service.status()
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning("watchlist status: stream_service.status() failed: %s", exc)
            stream_status = {}
        return {
            "started": self._started,
            "provider": stream_status.get("provider"),
            "provider_ready": stream_status.get("provider_ready"),
            "provider_error": stream_status.get("provider_error"),
            "symbol_count": len(default_members),
            "symbols": default_members,
            "streaming_symbols": stream_status.get("streaming_symbols", []),
            "subscribed_count": stream_status.get("streaming_count", 0),
            # Retained for back-compat; the new model has neither a
            # baseline nor a refcount.
            "baseline_count": 0,
            "refcounted_count": 0,
            "watchlist_count": len(watchlist_repo.list_watchlists()),
        }

    # ─────────────────────────────────────────────────────────────────
    # Legacy single-watchlist shim (kept for routes_watchlist.py)
    # ─────────────────────────────────────────────────────────────────

    def list_symbols(self) -> list[str]:
        try:
            return watchlist_repo.list_members(DEFAULT_WATCHLIST_NAME)
        except Exception as e:  # noqa: BLE001 — boundary
            logger.error("Watchlist legacy list_symbols failed: %s", e)
            return []

    def add(self, symbols: list[str]) -> dict:
        result = self.add_members(DEFAULT_WATCHLIST_NAME, symbols)
        return {"added": result["added"], "symbols": result["members"]}

    def remove(self, symbols: list[str]) -> dict:
        result = self.remove_members(DEFAULT_WATCHLIST_NAME, symbols)
        return {"removed": result["removed"], "symbols": result["members"]}


watchlist_service = WatchlistService()
