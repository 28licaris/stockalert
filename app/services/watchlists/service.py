"""Per-user watchlist service.

Orchestration on top of the Postgres repository:
  - Adding a symbol stamps the pretend position (quantity, default from
    settings.watchlist_default_qty; entry price = latest live price) and
    joins the symbol to the stream universe under the OWNER's id so it
    receives live prices from then on (tracked-instruments rule).
  - Reads compute returns at read time from latest prices; entry prices
    missing at add time (never-streamed symbol) are backfilled on the
    first read that finds one.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from app.services.watchlists.repository import WatchlistsRepository
from app.services.watchlists.schemas import (
    MemberOut,
    WatchlistDetail,
    WatchlistOut,
)

logger = logging.getLogger(__name__)


class WatchlistsService:
    def __init__(self, repository: WatchlistsRepository, *, default_qty: float = 100.0) -> None:
        self._repo = repository
        self._default_qty = default_qty

    @classmethod
    def from_settings(cls) -> "WatchlistsService":
        from app.config import settings

        return cls(
            WatchlistsRepository.from_settings(),
            default_qty=float(getattr(settings, "watchlist_default_qty", 100.0)),
        )

    # ── price plumbing (engine-side IO; degrades to None, never raises) ─
    @staticmethod
    def _latest_prices(symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        try:
            from app.db.queries import latest_bar_per_symbol

            rows = latest_bar_per_symbol(symbols)
            return {r["symbol"]: float(r["close"]) for r in rows if r.get("close")}
        except Exception as exc:  # noqa: BLE001 — display degrades, watchlist still works
            logger.warning("watchlists: latest-price lookup failed: %s", exc)
            return {}

    @staticmethod
    def _ensure_streaming(symbols: list[str], owner_id: str, list_name: str) -> None:
        """Join watched symbols to the (shared) stream universe so they get live
        prices — the tracked-instruments rule. Best-effort: a stream hiccup must
        not block the watchlist mutation, but it is logged, never swallowed."""
        try:
            from app.services.stream.service import stream_service

            stream_service.ensure_streaming(
                symbols, added_by=f"user:{owner_id}", source=f"user-watchlist:{list_name}"
            )
        except Exception as exc:  # noqa: BLE001 — stream join is best-effort
            logger.warning("watchlists: ensure_streaming failed for %s: %s", symbols, exc)

    # ── API surface ──────────────────────────────────────────────────
    def ensure_user(self, user_id: UUID, email: str, display_name: str) -> None:
        self._repo.ensure_user(user_id, email, display_name)

    def list(self, user_id: UUID) -> list[WatchlistOut]:
        return [
            WatchlistOut(
                id=w.id, name=w.name, description=w.description,
                created_at=w.created_at, n_members=len(w.members),
            )
            for w in self._repo.list_for_user(user_id)
        ]

    def create(self, user_id: UUID, name: str, description: str = "") -> WatchlistOut:
        w = self._repo.create(user_id, name, description)
        return WatchlistOut(id=w.id, name=w.name, description=w.description,
                            created_at=w.created_at, n_members=0)

    def delete(self, user_id: UUID, name: str) -> bool:
        return self._repo.delete(user_id, name)

    def add_symbols(
        self, user_id: UUID, name: str, symbols: list[str],
        quantity: Optional[float] = None,
    ) -> WatchlistDetail:
        w = self._repo.get(user_id, name)
        if w is None:
            raise KeyError(f"watchlist {name!r} not found")
        qty = quantity if quantity is not None else self._default_qty
        clean = sorted({s.strip().upper() for s in symbols if s.strip()})
        prices = self._latest_prices(clean)
        for sym in clean:
            self._repo.add_member(w.id, sym, qty, prices.get(sym))
        self._ensure_streaming(clean, owner_id=str(user_id), list_name=name)
        return self.detail(user_id, name)

    def remove_symbol(self, user_id: UUID, name: str, symbol: str) -> bool:
        w = self._repo.get(user_id, name)
        if w is None:
            return False
        return self._repo.remove_member(w.id, symbol.strip().upper())

    def detail(self, user_id: UUID, name: str) -> WatchlistDetail:
        w = self._repo.get(user_id, name)
        if w is None:
            raise KeyError(f"watchlist {name!r} not found")
        prices = self._latest_prices([m.symbol for m in w.members])
        members: list[MemberOut] = []
        total = 0.0
        any_pnl = False
        for m in w.members:
            cur = prices.get(m.symbol)
            entry = m.entry_price
            if entry is None and cur is not None:
                # Late backfill: symbol had no price at add time (now streaming).
                self._repo.backfill_entry_price(m.id, cur)
                entry = cur
            pnl_usd = pnl_pct = None
            if entry is not None and cur is not None and entry > 0:
                pnl_usd = (cur - entry) * m.quantity
                pnl_pct = cur / entry - 1.0
                total += pnl_usd
                any_pnl = True
            members.append(MemberOut(
                symbol=m.symbol, quantity=m.quantity, entry_price=entry,
                entry_at=m.entry_at, current_price=cur,
                pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            ))
        return WatchlistDetail(
            id=w.id, name=w.name, description=w.description, created_at=w.created_at,
            n_members=len(members), members=members,
            total_pnl_usd=total if any_pnl else None,
        )
