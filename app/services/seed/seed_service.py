"""SeedService — thin back-compat alias for StreamService.

The seed/stream module split (FE-CONTRACTS-4 finalisation) moved the
streaming-universe ownership to `app.services.stream.stream_service`.
This file remains so existing imports (`from app.services.seed import
seed_service`) keep working through the transition; all calls delegate
to the singleton `stream_service`.

New code should import from `app.services.stream` directly.
"""
from __future__ import annotations

from typing import Optional

from app.services.stream import StreamService, stream_service


class SeedService:
    """Thin alias preserving the legacy method names.

    Production code is the module-level `seed_service` singleton (also
    a back-compat alias for `stream_service`).
    """

    DEFAULT_OWNER = StreamService.DEFAULT_OWNER

    def __init__(self, _backing: Optional[StreamService] = None) -> None:
        self._backing = _backing or stream_service

    @classmethod
    def from_settings(cls) -> "SeedService":
        return cls()

    # ---- reads ----

    def list_seed(self, *, owner_id: Optional[str] = None) -> list[dict]:
        return self._backing.list_universe(owner_id=owner_id)

    def is_empty(self, *, owner_id: Optional[str] = None) -> bool:
        return self._backing.is_empty(owner_id=owner_id)

    # ---- mutations ----

    def add(
        self,
        symbol: str,
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        asset_type: str = "",
        notes: str = "",
    ) -> dict:
        return self._backing.add(
            symbol,
            owner_id=owner_id,
            added_by=added_by,
            asset_type=asset_type,
            notes=notes,
        )

    def remove(
        self, symbol: str, *, owner_id: Optional[str] = None,
    ) -> dict:
        return self._backing.remove(symbol, owner_id=owner_id)

    def import_bulk(
        self,
        symbols: list[str],
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        notes: str = "",
    ) -> dict:
        return self._backing.import_bulk(
            symbols,
            owner_id=owner_id,
            added_by=added_by,
            notes=notes,
        )

    def bootstrap_if_empty(
        self, *, owner_id: Optional[str] = None,
    ) -> tuple[bool, int]:
        return self._backing.bootstrap_if_empty(owner_id=owner_id)


# Production singleton — alias for stream_service.
seed_service = SeedService(_backing=stream_service)
