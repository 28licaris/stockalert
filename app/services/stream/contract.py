"""StreamService public contract — what callers (routes, watchlist) rely on.

Implementation lives in service.py; callers should depend on this Protocol
(or just the module singleton + schemas) and NEVER import service.py directly.
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol


class StreamServiceProtocol(Protocol):
    """The narrow surface other services / routes call.

    Lifecycle (start/stop) is operated by main_api lifespan; CRUD by
    routes_stream; ensure_streaming by watchlist_service (auto-extend).
    """

    async def start(self) -> None:
        """Read the stream universe table and subscribe everything."""

    async def stop(self) -> None:
        """Unsubscribe everything and stop the streamer."""

    def list_universe(self, *, owner_id: Optional[str] = None) -> list[dict]:
        ...

    def is_streaming(self, symbol: str) -> bool:
        ...

    def add(
        self,
        symbol: str,
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        asset_type: str = "",
        notes: str = "",
    ) -> dict:
        ...

    def remove(self, symbol: str, *, owner_id: Optional[str] = None) -> dict:
        ...

    def import_bulk(
        self,
        symbols: list[str],
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        notes: str = "",
    ) -> dict:
        ...

    def ensure_streaming(
        self,
        symbols: Iterable[str],
        *,
        added_by: str = "",
        source: str = "watchlist",
    ) -> list[str]:
        """Auto-extend the universe for symbols not yet present. Returns added."""

    def is_empty(self, *, owner_id: Optional[str] = None) -> bool:
        ...

    def bootstrap_if_empty(
        self, *, owner_id: Optional[str] = None
    ) -> tuple[bool, int]:
        ...

    def status(self) -> dict:
        ...
