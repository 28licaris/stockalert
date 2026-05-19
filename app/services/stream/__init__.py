"""Stream service — owns Schwab live subscriptions + the stream universe.

See README.md and docs/frontend_api_contracts.md §10.1 (locked
sticky-universe model) for the architectural contract.
"""
from app.services.stream.schemas import (
    StreamUniverseEntry,
    StreamUniverseListing,
    StreamMutationResult,
    StreamStatus,
)
from app.services.stream.service import StreamService, stream_service

__all__ = [
    "StreamService",
    "stream_service",
    "StreamUniverseEntry",
    "StreamUniverseListing",
    "StreamMutationResult",
    "StreamStatus",
]
