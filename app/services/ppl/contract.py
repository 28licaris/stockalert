"""Public interface for the PPL service.

Cross-service callers (API routes, MCP tools, the calibration harness) depend
on this Protocol and on `schemas.py` — never on `service.py`.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from app.services.ppl.schemas import PplParams, PplResult


@runtime_checkable
class PplBuilder(Protocol):
    """Builds one posterior-predictive snapshot for one symbol."""

    def build(self, symbol: str, params: Optional[PplParams] = None) -> PplResult:
        """Return a `PplResult`.

        Predictable failures (no data, not enough history, unsupported
        interval) come back as a non-`ok` status with an explanatory `error`.
        Only genuinely exceptional conditions raise.
        """
        ...
