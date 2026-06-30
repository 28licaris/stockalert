"""Strategy-library contracts. Public/private split is the whole point."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StrategyDefinition(BaseModel):
    """OWNER-ONLY full definition. `config` is the SECRET recipe (signal source,
    filters, params, universe-selection, risk) — NEVER serialized to a subscriber.
    Persisted locally + backed up to S3."""
    name: str                      # slug / id (also the paper-run name for track record)
    title: str                     # public display name
    tagline: str = ""              # short public hook
    description: str = ""          # public description
    category: str = "momentum"
    version: int = 1
    visibility: str = "subscribers"  # private | subscribers | public
    config: dict[str, Any]         # THE SECRET — owner-only
    created_at: Optional[datetime] = None


class StrategyPublic(BaseModel):
    """Subscriber-facing card — REDACTED. No `config` field exists here, so the
    recipe is structurally impossible to leak through this surface."""
    name: str
    title: str
    tagline: str
    description: str
    category: str
    version: int
    visibility: str
    # Performance summary from the live paper track record (results, not recipe).
    inception: Optional[datetime] = None
    days_live: int = 0
    forward_return: Optional[float] = None
    forward_win_rate: Optional[float] = None
    forward_n_trades: int = 0
    n_open_positions: int = 0


class StrategyAlert(BaseModel):
    """One actionable alert — what the subscription delivers. Prices + direction,
    no recipe."""
    symbol: str
    direction: str                 # long | short
    status: str                    # open | closed
    date: datetime                 # entry date (open) / exit date (closed)
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    current: Optional[float] = None
    exit: Optional[float] = None
    pnl: Optional[float] = None


class BackupResult(BaseModel):
    local_path: str
    s3_uri: Optional[str] = None
    s3_error: Optional[str] = None
