"""Public protocols for the options service boundary."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.services.options.schemas import OptionChainParseResult


class OptionChainParser(Protocol):
    """Provider payload parser that returns canonical option DTOs."""

    def parse_chain(
        self,
        payload: dict[str, Any],
        *,
        snapshot_ts: datetime,
        request_params: dict[str, Any] | None = None,
        ingestion_run_id: str | None = None,
    ) -> OptionChainParseResult:
        """Normalize a provider chain payload into canonical rows."""
