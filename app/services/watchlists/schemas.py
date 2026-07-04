"""Pydantic contracts for per-user watchlists — the module's public surface."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class MemberAdd(BaseModel):
    symbols: list[str] = Field(min_length=1)
    quantity: Optional[float] = Field(
        None, gt=0,
        description="Pretend-position shares; defaults to settings.watchlist_default_qty (100).",
    )


class MemberOut(BaseModel):
    """One watched symbol with its pretend position, returns computed at read."""

    symbol: str
    quantity: float
    entry_price: Optional[float]
    entry_at: datetime
    current_price: Optional[float] = None
    pnl_usd: Optional[float] = Field(None, description="(current - entry) * quantity")
    pnl_pct: Optional[float] = Field(None, description="current / entry - 1")


class WatchlistOut(BaseModel):
    id: UUID
    name: str
    description: str
    created_at: datetime
    n_members: int = 0


class WatchlistDetail(WatchlistOut):
    members: list[MemberOut] = []
    total_pnl_usd: Optional[float] = None
