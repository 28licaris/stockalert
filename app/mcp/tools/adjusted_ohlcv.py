"""
MCP tool for adjusted OHLCV — agent-facing v2 surface.

Thin adapter over `AdjustedOhlcvReader`. Identical Pydantic shape
as the HTTP route in `app/api/routes_adjusted.py` — one service,
two surfaces.

USE CASE:
  An LLM agent backtesting a strategy fetches 6 months of NVDA
  1-minute bars via `get_adjusted_bars` and runs an indicator pass.

The v1 `get_silver_bar_quality` tool was retired in CV20 — the
underlying `silver.bar_quality` table is deleted; v2 enforces data-
integrity invariants via the Spark adjustment job + corp-actions
ingest, not a downstream audit table. If/when v2 needs a quality
surface, it'll attach to `equities.polygon_adjusted` directly.
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache

from app.mcp.server import mcp
from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader
from app.services.readers.schemas import SilverBarsResponse

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> AdjustedOhlcvReader:
    return AdjustedOhlcvReader.from_settings()


@mcp.tool()
def get_adjusted_bars(
    symbol: str,
    start: datetime,
    end: datetime,
) -> SilverBarsResponse:
    """Return 1-minute split-adjusted OHLCV bars from the v2 lake.

    USE WHEN: an agent needs canonical 1-minute history for analysis,
    backtesting, ML training, or chart annotation. This is the
    **canonical consumer surface** — equities.polygon_adjusted rows
    are corp-action-adjusted and carry the cumulative future-splits
    factor as `adj_factor` so consumers can recover raw if needed
    (raw = adj × adj_factor).

    Snapshot-pinned: the response's `snapshot_id` lets a follow-up
    call replay against the exact lake state.

    Args:
        symbol: Ticker (case-insensitive; "nvda" → "NVDA").
        start: Lower bound on bar timestamp (inclusive), UTC.
        end: Upper bound on bar timestamp (exclusive), UTC. Half-open
            [start, end) interval mirrors Python slicing.

    Returns: `SilverBarsResponse` with the matching bars, sorted by
    timestamp ASC, plus the snapshot_id and the request echo. The
    class name retains the v1 `SilverBarsResponse` for Pydantic-shape
    compatibility; the data inside is sourced from
    equities.polygon_adjusted.

    Edge cases:
        - Unknown / empty symbol → empty `bars`, count=0.
        - equities.polygon_adjusted doesn't exist yet (cold start
          before the first Spark adjustment run) → empty `bars`.
        - No bars in window → empty `bars`, count=0.
    """
    return _reader().get_bars(symbol, start, end)
