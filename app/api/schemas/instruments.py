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
