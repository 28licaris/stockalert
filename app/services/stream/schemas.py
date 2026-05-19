"""Stream service DTOs — the only file other services should import.

Per service_modules.md: schemas.py + contract.py are the cross-service
boundary. service.py is implementation detail.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StreamUniverseEntry(BaseModel):
    """One symbol in the stream universe."""

    symbol: str
    asset_type: str = ""
    added_at: str = ""
    added_by: str = ""
    notes: str = ""


class StreamUniverseListing(BaseModel):
    """Result of `stream_service.list_universe()` — what the cockpit renders."""

    items: list[StreamUniverseEntry]
    count: int
    bootstrapped: bool = False


class StreamMutationResult(BaseModel):
    """Result of add / remove / import_bulk."""

    operation: Literal["add", "remove", "import"]
    changed: list[str] = Field(default_factory=list)
    items: list[StreamUniverseEntry]
    count: int


class StreamStatus(BaseModel):
    """Live snapshot of subscription state. Drives the /app/status tile."""

    started: bool
    provider: str
    provider_ready: bool
    provider_error: str | None = None
    streaming_count: int
    streaming_symbols: list[str]
    universe_count: int
