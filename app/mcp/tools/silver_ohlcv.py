"""
MCP tools for silver OHLCV + bar-quality — agent-facing surface.

Thin adapters over `AdjustedOhlcvReader`. Identical Pydantic shapes as
the HTTP routes in `app/api/routes_silver.py`. Reads
`silver.ohlcv_1m` + `silver.bar_quality` per the consumer contract.

USE CASES:
  - An LLM agent backtesting a strategy fetches 6 months of NVDA
    1-minute bars via `get_silver_bars` and runs an indicator pass.
  - An agent inspecting "did my last training set have any silent
    gaps?" calls `get_silver_bar_quality` and reasons about
    `disagreement_count` / `gap_count`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from app.mcp.server import mcp
from app.services.readers.schemas import (
    BarQualityResponse,
    SilverBarsResponse,
)
from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> AdjustedOhlcvReader:
    return AdjustedOhlcvReader.from_settings()


@mcp.tool()
def get_silver_bars(
    symbol: str,
    start: datetime,
    end: datetime,
) -> SilverBarsResponse:
    """Return 1-minute OHLCV bars from `silver.ohlcv_1m`.

    USE WHEN: an agent needs canonical 1-minute history for analysis,
    backtesting, ML training, or chart annotation. This is the
    **canonical consumer surface** — silver bars are
    provider-precedence-merged, corp-action-adjusted, and dedup'd.
    Never read bronze directly; always go through silver.

    Every bar carries BOTH `_raw` (what the provider sent) and `_adj`
    (split + cash-dividend back-adjusted) columns. Default consumption
    is `_adj` — that's what makes chart lines, indicator math, and
    backtest equity curves continuous across splits. `_raw` is for
    replay-accuracy use cases (trade-tape reconstruction).

    Snapshot-pinned: the response's `snapshot_id` lets a follow-up
    call replay against the exact lake state.

    Args:
        symbol: Ticker (case-insensitive; "nvda" → "NVDA").
        start: Lower bound on bar timestamp (inclusive), UTC.
        end: Upper bound on bar timestamp (exclusive), UTC. Half-open
            [start, end) interval mirrors Python slicing.

    Returns: `SilverBarsResponse` with the matching bars, sorted by
    timestamp ASC, plus the snapshot_id and the request echo.

    Edge cases:
        - Unknown / empty symbol → empty `bars`, count=0.
        - silver.ohlcv_1m doesn't exist yet (cold start) → empty
          `bars`, count=0.
        - No bars in window → empty `bars`, count=0.
    """
    return _reader().get_bars(symbol, start, end)


@mcp.tool()
def get_silver_bar_quality(
    symbol: str,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> BarQualityResponse:
    """Return per-(symbol, date) bar-quality audit rows.

    USE WHEN: an agent wants to verify data completeness before
    running a backtest or training a model. The audit ledger says,
    for each (symbol, date):
      - `actual_bars` / `expected_bars` — how complete the day was
      - `gap_count` / `max_gap_minutes` — silent gap detection
      - `providers_seen` — which providers contributed
      - `disagreement_count` — minutes where providers disagreed on
        close beyond tolerance (50¢ OR 0.5%)
      - `backfill_attempts` — orchestrator retries

    Reads `silver.bar_quality` — the audit ledger produced alongside
    silver.ohlcv_1m by silver_ohlcv_build.

    Args:
        symbol: Ticker (case-insensitive).
        since: Lower bound on `date` (inclusive). None = full history.
        until: Upper bound on `date` (inclusive). None = through today.

    Returns: `BarQualityResponse` with matching rows sorted by date ASC.

    Edge cases:
        - Unknown / empty symbol → empty `rows`, count=0.
        - silver.bar_quality doesn't exist yet → empty `rows`, count=0.
        - No rows in window → empty `rows`, count=0.
    """
    return _reader().get_bar_quality(symbol, since=since, until=until)
