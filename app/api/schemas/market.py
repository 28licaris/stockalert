"""
Market-tape (banner) and movers response schemas.

Banner backs `/api/v1/market/banner`. Movers backs `/api/v1/movers`.

Wire shape preserved byte-for-byte from the legacy routes — only
typing tightens; no fields renamed, no fields removed.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────


class BannerItem(BaseModel):
    """A single row on the market tape (index, future, or equity)."""

    symbol: str
    label: str = Field(
        ...,
        description="Short display label. Strips `$` from index symbols and `/` from futures roots; falls back to the description when short enough; otherwise the raw symbol.",
    )
    description: str = ""
    asset_type: Optional[str] = Field(
        default=None,
        description="Upstream asset type (e.g. 'EQUITY', 'FUTURE', 'INDEX'). Null when the provider didn't report one.",
    )
    last: Optional[float] = None
    net_change: Optional[float] = None
    change_pct: Optional[float] = Field(
        default=None,
        description="Daily change as a percent (NOT a fraction): `1.25` means +1.25%.",
    )
    close: Optional[float] = Field(
        default=None, description="Previous session close used to compute change."
    )


class BannerError(BaseModel):
    """A per-symbol or whole-batch error explaining why a row is missing."""

    symbol: Optional[str] = None
    message: str


class MarketBannerResponse(BaseModel):
    """The full banner payload."""

    as_of: str = Field(
        ...,
        description="UTC ISO 8601 timestamp of when this snapshot was taken.",
    )
    provider: Optional[str] = Field(
        default=None,
        description="Quote provider used (e.g. 'schwab'). Null when no provider is configured.",
    )
    items: list[BannerItem]
    errors: list[BannerError] = Field(
        default_factory=list,
        description="Per-symbol or whole-batch failures. Cockpit surfaces these as a small warning chip; the empty case is the happy path.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "as_of": "2026-05-18T21:30:00+00:00",
                "provider": "schwab",
                "items": [
                    {
                        "symbol": "$SPX",
                        "label": "SPX",
                        "description": "S&P 500 Index",
                        "asset_type": "INDEX",
                        "last": 5825.10,
                        "net_change": -14.30,
                        "change_pct": -0.245,
                        "close": 5839.40,
                    }
                ],
                "errors": [],
            }
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Movers
# ─────────────────────────────────────────────────────────────────────


class Mover(BaseModel):
    """A single mover row, fanned out across one or more indexes."""

    symbol: str
    description: Optional[str] = None
    last: Optional[float] = None
    change: Optional[float] = Field(
        default=None,
        description="Net dollar change. Sign matches direction.",
    )
    percent_change: Optional[float] = Field(
        default=None,
        description="Change as a percent. Sign matches direction.",
    )
    direction: Optional[str] = Field(
        default=None,
        description="Provider-reported direction string when present ('up' / 'down').",
    )
    volume: Optional[int] = None
    total_volume: Optional[int] = None
    trades: Optional[int] = None
    market_share: Optional[float] = None
    source_indexes: list[str] = Field(
        default_factory=list,
        description="Which fanned-out indexes contributed this row. Multiple entries when a symbol appears in more than one index (e.g. SPX + NDX).",
    )


class MoversResponse(BaseModel):
    """The full movers payload — wire shape preserved from legacy /api/movers."""

    index: str = Field(
        ...,
        description="The original `index` query (or a comma-joined string for fan-outs). For consumer display, prefer `indexes`.",
    )
    indexes: list[str] = Field(
        ...,
        description="Indexes that were queried (after pseudo-index expansion).",
    )
    provider: Optional[str] = None
    sort: str = Field(
        ...,
        description="Sort key used by upstream. One of: VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN.",
    )
    frequency: int = Field(
        ...,
        description="Lookback window in minutes (0 = since open).",
    )
    count: int
    upstream_count: int = Field(
        ...,
        description="How many unique movers the upstream returned before filtering by sort direction.",
    )
    filtered_out: int = Field(
        ...,
        description="upstream_count − count. Useful for the 'No matches but Schwab returned X' UX.",
    )
    per_index_counts: dict[str, int] = Field(
        ...,
        description="Per-index row counts BEFORE deduplication.",
    )
    errors: Optional[dict[str, str]] = Field(
        default=None,
        description="Per-index error map (index → message) when fan-out partially failed; null when all calls succeeded.",
    )
    fetched_at: str = Field(
        ...,
        description="UTC ISO 8601 timestamp of when this snapshot was taken.",
    )
    movers: list[Mover]
