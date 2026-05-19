"""
Seed-universe response schemas. Backs `/api/v1/seed*`.

The "seed universe" is the operator's explicit set of symbols
permanently part of the streaming universe. See
[docs/frontend_api_contracts.md §4.4](../../../docs/frontend_api_contracts.md)
for the sticky-universe model — adding a symbol here subscribes
Schwab stream + triggers backfill; removing here is the only way to
fully stop a symbol from streaming.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SeedEntry(BaseModel):
    """One symbol in the operator's seed universe."""

    symbol: str
    asset_type: str = Field(
        default="",
        description="Free-form upstream asset-type string ('EQUITY', 'FUTURE', 'INDEX', etc.). Closed enum lives at common.AssetType for the cockpit's preferred set.",
    )
    added_at: str = Field(
        ...,
        description="ISO 8601 with `Z` suffix. The moment this symbol was promoted into the seed universe.",
    )
    added_by: str = Field(
        default="",
        description="Principal.userId of the operator who promoted the symbol. Empty for bootstrap-from-env-and-watchlist entries.",
    )
    notes: str = Field(default="", description="Operator-supplied freeform note.")


class SeedUniverseResponse(BaseModel):
    """The seed universe + a bit of side-effect context."""

    items: list[SeedEntry]
    count: int = Field(..., description="Number of active seed members.")
    bootstrapped: bool = Field(
        ...,
        description="True iff this read triggered a one-time bootstrap (the CH table was empty and was just populated from SEED_SYMBOLS ∪ default-watchlist members).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "count": 2,
                "bootstrapped": False,
                "items": [
                    {
                        "symbol": "AAPL",
                        "asset_type": "EQUITY",
                        "added_at": "2026-05-18T19:30:00Z",
                        "added_by": "default-user",
                        "notes": "",
                    },
                    {
                        "symbol": "NVDA",
                        "asset_type": "EQUITY",
                        "added_at": "2026-05-18T19:30:00Z",
                        "added_by": "default-user",
                        "notes": "",
                    },
                ],
            }
        }
    )


class AddSeedRequest(BaseModel):
    """Body for `POST /api/v1/seed`."""

    symbol: str = Field(..., min_length=1, max_length=32)
    asset_type: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Optional — provider-derived if omitted at add time.",
    )
    notes: Optional[str] = Field(default=None, max_length=500)


class ImportSeedRequest(BaseModel):
    """Body for `POST /api/v1/seed/import` — bulk add."""

    symbols: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Symbols to add to the seed universe (idempotent).",
    )
    notes: Optional[str] = Field(default=None, max_length=500)


class SeedMutationResponse(BaseModel):
    """Response shape for add/remove/import."""

    operation: str = Field(
        ...,
        description="'add' | 'remove' | 'import'. Single-source identifier for clients that share a result handler.",
    )
    changed: list[str] = Field(
        default_factory=list,
        description="Symbols actually affected. Empty when the mutation was a no-op (idempotent).",
    )
    items: list[SeedEntry] = Field(
        default_factory=list,
        description="Full active seed universe after the mutation.",
    )
    count: int = Field(..., description="Count after the mutation.")
