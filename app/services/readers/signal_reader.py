"""
SignalReader — read service for ClickHouse `signals` table.

Returns detector output (divergence / breakout / threshold-cross etc.)
as Pydantic `Signal` objects. Same pattern as `BarReader`: thin wrapper
over `app.db.queries`, no SQL in this module.

Used by:
  - HTTP `/api/signals` routes (live tier)
  - The dashboard signal feed
  - Future MCP tools (`get_recent_signals`, `get_signals_by_symbol`)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.readers.schemas import Signal

logger = logging.getLogger(__name__)


def _row_to_signal(row: dict) -> Signal:
    """
    Convert a `dict` from `app.db.queries.list_signals` into `Signal`.

    queries.list_signals uses short column aliases (`type`, `ts`,
    `price`); the canonical contract uses descriptive names
    (`signal_type`, `ts_signal`, `price_at_signal`). Reconcile here so
    consumers depend on the contract, not the SQL aliases.
    """
    return Signal(
        id=row.get("id"),
        symbol=row["symbol"],
        signal_type=row.get("signal_type") or row.get("type") or "",
        indicator=row.get("indicator") or "",
        ts_signal=row.get("ts_signal") or row.get("ts"),
        price_at_signal=float(row.get("price_at_signal") or row.get("price") or 0.0),
        indicator_value=float(row.get("indicator_value") or 0.0),
        p1_ts=row.get("p1_ts"),
        p2_ts=row.get("p2_ts"),
    )


class SignalReader:
    """Read interface over the CH `signals` table."""

    @classmethod
    def from_settings(cls) -> "SignalReader":
        return cls()

    def get_recent_signals(self, limit: int = 50) -> list[Signal]:
        """
        Return the most recent `limit` signals across all symbols,
        newest-first. Used by the dashboard feed and by agents
        skimming "what just fired."
        """
        if limit <= 0:
            return []
        from app.db import queries

        rows = queries.recent_signals(limit=limit)
        return [_row_to_signal(r) for r in rows] if rows else []

    def get_signals_by_symbol(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> list[Signal]:
        """
        Return signals filtered by `symbol` (or all symbols if None),
        newest-first.
        """
        if limit <= 0:
            return []
        from app.db import queries

        rows = queries.list_signals(symbol, limit)
        return [_row_to_signal(r) for r in rows] if rows else []
