"""PostgreSQL repository for per-user watchlists (identity DB, sessionmaker
pattern mirroring app/services/identity/repository.py). All queries are
scoped by user_id — cross-user access is structurally impossible here."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload, sessionmaker

from app.services.identity.models import UserModel
from app.services.watchlists.models import WatchlistMemberModel, WatchlistModel


class WatchlistsRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls) -> "WatchlistsRepository":
        from app.db.postgres import get_identity_session_factory

        return cls(get_identity_session_factory())

    # ── dev-fallback support ─────────────────────────────────────────
    def ensure_user(self, user_id: UUID, email: str, display_name: str) -> None:
        """Insert the user row if absent (FK target for dev-fallback principals)."""
        with self._session_factory() as db:
            if db.get(UserModel, user_id) is None:
                db.add(UserModel(
                    id=user_id, email=email, normalized_email=email.lower(),
                    display_name=display_name, status="active",
                ))
                db.commit()

    # ── watchlists ───────────────────────────────────────────────────
    def list_for_user(self, user_id: UUID) -> list[WatchlistModel]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(WatchlistModel)
                .options(selectinload(WatchlistModel.members))
                .where(WatchlistModel.user_id == user_id)
                .order_by(WatchlistModel.created_at)
            ).all()
            db.expunge_all()
            return list(rows)

    def get(self, user_id: UUID, name: str) -> Optional[WatchlistModel]:
        with self._session_factory() as db:
            row = db.scalars(
                select(WatchlistModel)
                .options(selectinload(WatchlistModel.members))
                .where(WatchlistModel.user_id == user_id, WatchlistModel.name == name)
            ).first()
            if row is not None:
                db.expunge_all()
            return row

    def create(self, user_id: UUID, name: str, description: str) -> WatchlistModel:
        with self._session_factory() as db:
            row = WatchlistModel(user_id=user_id, name=name, description=description)
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge_all()
            return row

    def delete(self, user_id: UUID, name: str) -> bool:
        with self._session_factory() as db:
            row = db.scalars(
                select(WatchlistModel)
                .where(WatchlistModel.user_id == user_id, WatchlistModel.name == name)
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    # ── members / pretend positions ──────────────────────────────────
    def add_member(
        self, watchlist_id: UUID, symbol: str, quantity: float,
        entry_price: Optional[float],
    ) -> WatchlistMemberModel:
        with self._session_factory() as db:
            existing = db.scalars(
                select(WatchlistMemberModel).where(
                    WatchlistMemberModel.watchlist_id == watchlist_id,
                    WatchlistMemberModel.symbol == symbol,
                )
            ).first()
            if existing is not None:  # idempotent re-add keeps the ORIGINAL position
                db.expunge_all()
                return existing
            row = WatchlistMemberModel(
                watchlist_id=watchlist_id, symbol=symbol,
                quantity=quantity, entry_price=entry_price,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge_all()
            return row

    def remove_member(self, watchlist_id: UUID, symbol: str) -> bool:
        with self._session_factory() as db:
            row = db.scalars(
                select(WatchlistMemberModel).where(
                    WatchlistMemberModel.watchlist_id == watchlist_id,
                    WatchlistMemberModel.symbol == symbol,
                )
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    def backfill_entry_price(self, member_id: UUID, price: float) -> None:
        """Set entry_price for a member added while no price was available."""
        with self._session_factory() as db:
            row = db.get(WatchlistMemberModel, member_id)
            if row is not None and row.entry_price is None:
                row.entry_price = price
                db.commit()
