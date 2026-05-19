"""
Watchlist response schemas. Backs `/api/v1/watchlists*` and the legacy
single-watchlist `/api/v1/watchlist*` family.

Wire shapes preserved byte-for-byte from the legacy endpoints so the
static dashboard.html and symbol.html parsing code keeps working
through the transition.

Two URL families coexist:
  - Multi-watchlist (Phase 1.3): `/api/v1/watchlists` + `/api/v1/watchlists/{name}/*`
  - Legacy single-watchlist:    `/api/v1/watchlist[/add|/remove|/snapshot]`
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Multi-watchlist family
# ─────────────────────────────────────────────────────────────────────


class Watchlist(BaseModel):
    """A user-owned named watchlist."""

    name: str
    kind: str = Field(
        ...,
        description="One of: 'user', 'baseline', 'adhoc'. Free-form on the wire so a future kind doesn't break existing clients.",
    )
    description: str = ""
    is_active: bool = Field(
        ...,
        description="Soft-delete flag. Active=True is the happy path; inactive watchlists are hidden from default list responses.",
    )
    updated_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 with `Z` suffix. Null if never updated.",
    )
    # Optional embedded members (returned when `with_members=true`).
    members: Optional[list[str]] = Field(
        default=None,
        description="Active member symbols, in insertion order. Null when the endpoint didn't include members.",
    )
    member_count: Optional[int] = Field(
        default=None,
        description="Convenience count; null when members aren't included.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "tech_focus",
                "kind": "user",
                "description": "Mega-cap tech longs",
                "is_active": True,
                "updated_at": "2026-05-18T18:30:00Z",
                "members": ["AAPL", "NVDA", "GOOGL"],
                "member_count": 3,
            }
        }
    )


class CreateWatchlistRequest(BaseModel):
    """Body for `POST /api/v1/watchlists`."""

    name: str = Field(..., min_length=1, max_length=64)
    kind: str = Field("user", description="One of: user, baseline, adhoc")
    description: str = Field("", max_length=500)


class RenameWatchlistRequest(BaseModel):
    """Body for `PATCH /api/v1/watchlists/{name}`."""

    new_name: str = Field(..., min_length=1, max_length=64)


class SymbolsRequest(BaseModel):
    """Body for member-mutation endpoints."""

    symbols: list[str] = Field(
        ..., description="Stock symbols, e.g. ['SPY', 'AAPL']"
    )


class DeleteWatchlistResponse(BaseModel):
    """Response for `DELETE /api/v1/watchlists/{name}`."""

    deleted: str = Field(..., description="Name of the watchlist that was soft-deleted.")


class WatchlistMembersMutationResponse(BaseModel):
    """
    Shape returned by `POST/DELETE /api/v1/watchlists/{name}/members`.

    Wire shape preserved from `WatchlistService.add_members` /
    `.remove_members` so the legacy dashboard keeps parsing this.
    """

    watchlist: str = Field(..., description="The watchlist that was mutated.")
    added: list[str] = Field(
        default_factory=list,
        description="Symbols newly activated for this watchlist (POST). Empty for remove operations.",
    )
    removed: list[str] = Field(
        default_factory=list,
        description="Symbols deactivated (DELETE). Empty for add operations.",
    )
    members: list[str] = Field(
        ..., description="Full active member list after the mutation."
    )


# ─────────────────────────────────────────────────────────────────────
# Watchlist snapshot (latest bar per symbol)
# ─────────────────────────────────────────────────────────────────────


class WatchlistSnapshotItem(BaseModel):
    """Latest-bar snapshot for one watchlist member.

    Wire shape preserved from `_snapshot_for()` in routes_watchlist.
    Note: `bar_count` is a watchlist-quality metric (it indicates
    whether the symbol has been receiving bars), not a market metric,
    so it deliberately doesn't live on the canonical `Bar` shape.
    """

    symbol: str
    bar_count: int = Field(
        ...,
        description="Number of bars stored for this symbol. 0 means the symbol is configured but no bars have arrived yet.",
    )
    ts: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────
# Legacy single-watchlist family (`/api/v1/watchlist[/...]`)
# ─────────────────────────────────────────────────────────────────────


class WatchlistStatus(BaseModel):
    """Shape returned by the legacy `GET /api/v1/watchlist` — global stream status."""

    started: bool = Field(..., description="Whether the live-bar stream has been started.")
    provider: str = Field(..., description="Active stream provider (e.g. 'schwab', 'polygon').")
    provider_ready: bool = Field(
        ...,
        description="True iff the provider's underlying client/OAuth/etc. is initialized.",
    )
    provider_error: Optional[str] = Field(
        default=None, description="Last provider init error; null when healthy."
    )
    symbol_count: int = Field(
        ..., description="Member count of the DEFAULT watchlist (legacy field)."
    )
    symbols: list[str] = Field(
        ...,
        description="DEFAULT watchlist members (legacy field; new code reads /api/v1/watchlists/<name>/members).",
    )
    streaming_symbols: list[str] = Field(
        ...,
        description="Symbols currently subscribed across ALL watchlists + baseline. The new global subscription set.",
    )
    subscribed_count: int
    baseline_count: int = Field(
        ...,
        description="Count of symbols held by 'baseline' watchlists (always-streamed).",
    )
    refcounted_count: int = Field(
        ...,
        description="Count of symbols held by ref-counted (user/adhoc) watchlists.",
    )
    watchlist_count: int = Field(
        ..., description="Total active watchlists (default + user + baseline + adhoc)."
    )


class LegacyWatchlistMutationResponse(BaseModel):
    """Shape returned by legacy `POST /api/v1/watchlist/add` and `/remove`.

    Different from the multi-list mutation response — the legacy shape
    omits `watchlist` and uses `symbols` instead of `members`.
    """

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(
        ..., description="Full active member list of the default watchlist after the mutation."
    )
