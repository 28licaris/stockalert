"""Stream-universe API schemas — backs `/api/v1/stream*`.

The stream universe is the operator's explicit set of symbols that
StreamService keeps subscribed to the live Schwab feed 24/7. Per the
locked sticky-universe model in
[docs/frontend_api_contracts.md §10.1], adding a symbol here
subscribes Schwab + triggers backfill warmup; removing here is the
only path that fully strips a symbol from the live stream. Watchlist
operations auto-extend this universe but never evict from it.

Shape mirrors the existing `seed` schemas one-for-one (`SeedEntry` ≡
`StreamUniverseEntry`, etc.) since the two endpoints describe the
same underlying CH table — the `/seed` namespace is preserved for
back-compat and the `/stream` namespace surfaces the new
StreamStatus snapshot for the cockpit's status tile.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StreamUniverseEntry(BaseModel):
    """One symbol in the stream universe."""

    symbol: str
    asset_type: str = Field(
        default="",
        description="Free-form upstream asset-type string ('EQUITY', 'FUTURE', 'INDEX', etc.).",
    )
    added_at: str = Field(
        ...,
        description="ISO 8601 with `Z` suffix. When this symbol was promoted into the stream universe.",
    )
    added_by: str = Field(
        default="",
        description="Principal.userId of the operator (or 'bootstrap'/'auto-added by watchlist:<name>').",
    )
    notes: str = Field(default="", description="Operator-supplied freeform note.")
    description: str = Field(
        default="",
        description="Human-readable instrument name (e.g. futures root '/ES' → 'E-mini S&P 500'). Empty for equities.",
    )


class StreamUniverseResponse(BaseModel):
    """The active stream universe + a bit of side-effect context."""

    items: list[StreamUniverseEntry]
    count: int = Field(..., description="Number of active stream universe members.")
    bootstrapped: bool = Field(
        ...,
        description="True iff this read triggered a one-time bootstrap.",
    )


class AddStreamRequest(BaseModel):
    """Body for `POST /api/v1/stream` — promote one symbol into the stream universe."""

    symbol: str = Field(..., min_length=1, max_length=32)
    asset_type: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=500)


class ImportStreamRequest(BaseModel):
    """Body for `POST /api/v1/stream/import` — bulk add. Idempotent."""

    symbols: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Symbols to add to the stream universe.",
    )
    notes: Optional[str] = Field(default=None, max_length=500)


class StreamMutationResponse(BaseModel):
    """Response shape for add / remove / import."""

    operation: str = Field(
        ...,
        description="'add' | 'remove' | 'import'.",
    )
    changed: list[str] = Field(
        default_factory=list,
        description="Symbols actually affected. Empty when the mutation was idempotent.",
    )
    items: list[StreamUniverseEntry] = Field(
        default_factory=list,
        description="Full active stream universe after the mutation.",
    )
    count: int = Field(..., description="Count after the mutation.")


class StreamStatusResponse(BaseModel):
    """Live snapshot of stream subscription state — drives the cockpit
    `/app/status` streaming tile.
    """

    started: bool = Field(..., description="Whether StreamService.start() has completed.")
    provider: str = Field(..., description="Effective stream provider (e.g. 'schwab').")
    provider_ready: bool = Field(
        ...,
        description="True iff the streaming provider is initialized and ready.",
    )
    provider_error: Optional[str] = Field(
        default=None,
        description="Set when the provider failed to initialize; surfaced to the operator.",
    )
    streaming_count: int = Field(
        ...,
        description="Number of symbols currently subscribed to the live Schwab stream.",
    )
    streaming_symbols: list[str] = Field(
        default_factory=list,
        description="Sorted list of currently-subscribed symbols.",
    )
    universe_count: int = Field(
        ...,
        description="Number of active rows in the stream_universe CH table.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "started": True,
                "provider": "schwab",
                "provider_ready": True,
                "provider_error": None,
                "streaming_count": 103,
                "streaming_symbols": ["AAPL", "ABBV", "ABT"],
                "universe_count": 103,
            }
        }
    )
