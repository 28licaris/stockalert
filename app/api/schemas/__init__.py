"""
Pydantic models shared across the HTTP API surface.

One file per concern (bars.py, signals.py, watchlists.py, ...). The
common primitives (ErrorResponse, Page[T], AssetType, HealthState)
live in `common.py` and are imported by every other schema file.

See [docs/frontend_api_contracts.md](../../../docs/frontend_api_contracts.md)
for the rules every new schema must follow.
"""

from app.api.schemas.common import (
    AssetType,
    ErrorResponse,
    HealthState,
    Interval,
    OkResponse,
    Page,
)

__all__ = [
    "AssetType",
    "ErrorResponse",
    "HealthState",
    "Interval",
    "OkResponse",
    "Page",
]
