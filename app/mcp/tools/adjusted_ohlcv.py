"""
MCP tool for adjusted OHLCV — agent-facing v2 surface.

Thin adapter over `AdjustedOhlcvReader`. Identical Pydantic shape
as the HTTP route in `app/api/routes_adjusted.py` — one service,
two surfaces.

USE CASES:
  - An LLM agent backtesting a strategy fetches 6 months of NVDA
    1-minute bars via `get_adjusted_bars` and runs an indicator pass.
  - An agent rendering a chart that includes today's session uses
    `include_live=True` to stitch polygon_adjusted's deep history
    with equities.schwab_universe's live data in one call.

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
from app.services.readers.schemas import SilverBarsResponse, SymbolCoverageResponse

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> AdjustedOhlcvReader:
    return AdjustedOhlcvReader.from_settings()


@mcp.tool()
def get_adjusted_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    include_live: bool = False,
) -> SilverBarsResponse:
    """Return 1-minute split-adjusted OHLCV bars from the v2 lake.

    USE WHEN: an agent needs canonical 1-minute history for analysis,
    backtesting, ML training, or chart annotation. equities.polygon_adjusted
    rows are corp-action-adjusted and carry the cumulative future-splits
    factor as `adj_factor` so consumers can recover raw if needed
    (raw = adj × adj_factor).

    USE include_live=True WHEN: the window includes today's session and
    you need both deep history AND today's live bars in one response.
    UNIONs equities.polygon_adjusted (lags real-time by up to one
    weekly Spark run) with equities.schwab_universe (live + tip-fill,
    pre-adjusted with adj_factor=1.0). Polygon wins duplicates on
    (symbol, timestamp). Costs ~2x the I/O of the default mode but
    closes the polygon-staleness gap that's otherwise visible at the
    right edge of charts and end of ML training windows.

    Snapshot-pinned: the response's `snapshot_id` lets a follow-up
    call replay against the exact lake state. When include_live=True,
    snapshot_id reflects the polygon side (the canonical adjusted
    source); schwab snapshots are not exposed since they shift
    constantly with live writes.

    Args:
        symbol: Ticker (case-insensitive; "nvda" → "NVDA").
        start: Lower bound on bar timestamp (inclusive), UTC.
        end: Upper bound on bar timestamp (exclusive), UTC. Half-open
            [start, end) interval mirrors Python slicing.
        include_live: Default False. Set True to UNION schwab_universe
            (closes the polygon-staleness gap for windows that include
            today).

    Returns: `SilverBarsResponse` with the matching bars, sorted by
    timestamp ASC, plus the snapshot_id and the request echo. The
    class name retains the v1 `SilverBarsResponse` for Pydantic-shape
    compatibility; the data inside is sourced from
    equities.polygon_adjusted (default) or polygon_adjusted ∪
    schwab_universe (include_live=True).

    Edge cases:
        - Unknown / empty symbol → empty `bars`, count=0.
        - equities.polygon_adjusted doesn't exist yet (cold start
          before the first Spark adjustment run) AND include_live=False
          → empty `bars`. With include_live=True, schwab_universe
          alone fills the response.
        - No bars in window → empty `bars`, count=0.
    """
    if include_live:
        return _reader().get_bars_union(symbol, start, end)
    return _reader().get_bars(symbol, start, end)


@mcp.tool()
def get_symbol_coverage(symbol: str) -> SymbolCoverageResponse:
    """Coverage stats for `symbol` across the v2 adjusted sources.

    USE WHEN: an agent is about to queue a long-running backtest or
    deep-history query and wants to verify the lake actually has the
    data first. Cheaper than failing-and-retrying a multi-minute
    query.

    Returns per-table row counts, earliest/latest timestamps, and
    snapshot IDs for:
      - equities.polygon_adjusted — deep adjusted history (weekly Spark)
      - equities.schwab_universe  — live + tip-fill (continuous)

    Common interpretations:
      - polygon_adjusted.row_count == 0 → cold-start; the first
        polygon_adjustment_job hasn't run yet. Suggest the operator
        run it before backtesting.
      - polygon_adjusted.latest_timestamp older than ~7 days → the
        weekly Spark adjustment job is lagging or hasn't run since
        the symbol was last in the universe.
      - schwab_universe.latest_timestamp older than ~5 min during
        market hours → live writer is stalled or the symbol isn't
        subscribed to Schwab WS.
      - schwab_universe.row_count > 0 AND polygon_adjusted.row_count
        == 0 → brand-new symbol; deep history will arrive on the
        next weekly Spark run.

    Args:
        symbol: Ticker (case-insensitive; "nvda" → "NVDA").

    Returns: `SymbolCoverageResponse` with separate SourceCoverage
    blocks for polygon_adjusted and schwab_universe. Either side
    independently degrades to row_count=0 with None timestamps on
    cold-start / scan failure — never raises.
    """
    return _reader().get_symbol_coverage(symbol)
