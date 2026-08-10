"""Public interface for the Vol Premium screen.

Other services and the API depend on this Protocol, never on `service.py`
(`docs/standards/service_modules.md` rule 1).
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from app.services.vol_premium.schemas import (
    ScreenParams,
    VolPremiumRowResult,
    VolPremiumScreen,
)


@runtime_checkable
class VolPremiumBuilder(Protocol):
    """Ranks a universe by how expensive its options are against measured
    behaviour."""

    def build_row(
        self, symbol: str, params: Optional[ScreenParams] = None
    ) -> VolPremiumRowResult:
        """One symbol. Predictable failures come back as a status, not a raise."""
        ...

    def build_screen(
        self,
        symbols: Optional[Sequence[str]] = None,
        params: Optional[ScreenParams] = None,
    ) -> VolPremiumScreen:
        """The full ranked table. `symbols=None` uses whatever the options
        ingest currently covers."""
        ...
