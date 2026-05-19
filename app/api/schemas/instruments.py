"""
Instrument search response schema. Backs `/api/v1/instruments/search`.

`asset_type` is intentionally `str` (not the strict `AssetType`
Literal from common.py) because the upstream provider can return
unrecognized values ("WARRANT", "BOND", etc.) that we want to
surface rather than 500 the request. A future pass can tighten this
once we normalize provider asset types upstream.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InstrumentMatch(BaseModel):
    """One autocomplete suggestion."""

    symbol: str
    description: str = ""
    exchange: str = ""
    asset_type: str = Field(
        "",
        description="Upstream-provided asset type ('EQUITY', 'FUTURE', 'OPTION', 'INDEX', 'FUND', or provider-specific string). See common.AssetType for the cockpit's preferred closed set.",
    )


class InstrumentSearchResponse(BaseModel):
    """The autocomplete response. Cached server-side for ~30s per query."""

    query: str = Field(..., description="The cleaned (stripped) query string.")
    results: list[InstrumentMatch]
    cached: bool = Field(
        ...,
        description="True if this response was served from the in-process cache rather than a fresh provider call.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "NVD",
                "results": [
                    {
                        "symbol": "NVDA",
                        "description": "NVIDIA Corp",
                        "exchange": "NASDAQ",
                        "asset_type": "EQUITY",
                    }
                ],
                "cached": False,
            }
        }
    )


class InstrumentLookupResponse(BaseModel):
    """Batch lookup of `{symbol → InstrumentMatch}` for already-known symbols.

    Used by the cockpit to enrich a list of watchlist members (or any
    other symbol collection) with descriptions without N round-trips.
    Symbols the upstream provider couldn't resolve map to a synthetic
    entry with empty description — clients can detect this by
    `description == ""`. Order in `results` follows the input symbol
    list so the cockpit can render in the original order.
    """

    results: list[InstrumentMatch] = Field(
        ...,
        description="One entry per requested symbol, in the order the client asked. Unknown symbols appear with empty `description` rather than being silently dropped — clients always get exactly len(symbols) entries.",
    )
    cached_count: int = Field(
        ...,
        description="How many entries were served from the in-process cache. Useful for tuning TTL.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "results": [
                    {
                        "symbol": "AAPL",
                        "description": "Apple Inc",
                        "exchange": "NASDAQ",
                        "asset_type": "EQUITY",
                    },
                    {
                        "symbol": "FAKEXYZ",
                        "description": "",
                        "exchange": "",
                        "asset_type": "",
                    },
                ],
                "cached_count": 1,
            }
        }
    )
