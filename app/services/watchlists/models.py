"""SQLAlchemy models for per-user watchlists (identity PostgreSQL).

Each member row IS its pretend position: quantity + entry price stamped
at add time. Returns are never stored — computed at read time against
latest prices (lean-silver rule).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.services.identity.models import IdentityBase, TimestampMixin


class WatchlistModel(TimestampMixin, IdentityBase):
    __tablename__ = "watchlists"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False, default="")

    members: Mapped[list["WatchlistMemberModel"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan",
        order_by="WatchlistMemberModel.entry_at",
    )

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlists_user_name"),)


class WatchlistMemberModel(IdentityBase):
    __tablename__ = "watchlist_members"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    watchlist_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[float] = mapped_column(Float(), nullable=False, default=100.0)
    # None when no price was available at add time (e.g. never-streamed symbol);
    # the service backfills it on the next read that finds a price.
    entry_price: Mapped[float | None] = mapped_column(Float(), nullable=True)
    entry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    watchlist: Mapped[WatchlistModel] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_members_symbol"),
    )
