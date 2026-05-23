"""
MCP tools for corp-actions — agent-facing surface.

Thin adapter over `CorpActionsReader`. Identical Pydantic shapes as
the HTTP route in `app/api/routes_corp_actions.py`. Reads
`equities.market_corp_actions` (v2 canonical store, post-CV9/CV10).

USE CASE: an LLM agent reasoning about adjusted prices on a chart,
or a backtest planning around an upcoming ex-date, asks
`get_corp_actions("AAPL", since="2020-01-01")` and gets a list of
every split + dividend + spinoff in that window.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Optional

from app.mcp.server import mcp
from app.services.readers.corp_actions_reader import CorpActionsReader
from app.services.readers.schemas import CorpActionsResponse
from app.services.equities.models import CorpActionKind

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> CorpActionsReader:
    return CorpActionsReader.from_settings()


@mcp.tool()
def get_corp_actions(
    symbol: str,
    since: Optional[date] = None,
    until: Optional[date] = None,
    action_types: Optional[list[CorpActionKind]] = None,
) -> CorpActionsResponse:
    """Return splits + dividends + spinoffs for a symbol.

    USE WHEN: an agent needs to know which corporate actions
    affected a symbol over a window — for backtest filtering
    ("avoid trading 5 days before a split"), for chart annotation
    ("mark dividends on the AAPL chart"), or for understanding
    why a price gap appeared ("did AAPL really drop 75% on
    2020-08-31? Or was that a split?").

    Reads `equities.market_corp_actions` — the canonical, provider-
    precedence-resolved view. Snapshot-pinned for reproducibility (the
    returned `snapshot_id` lets a follow-up call replay against the
    same lake state).

    Args:
        symbol: Ticker (case-insensitive; "aapl" → "AAPL").
        since: Lower bound on ex_date (inclusive). None = full
            history.
        until: Upper bound on ex_date (inclusive). None = through
            today.
        action_types: Filter to specific kinds (e.g. `["split"]`
            for splits only). None = all kinds.

    Returns: `CorpActionsResponse` with the matching actions,
    sorted by `(ex_date, action_type)`, plus the snapshot_id and
    the request echo.

    Edge cases:
        - Unknown / empty symbol → empty `actions`, count=0.
        - `equities.market_corp_actions` doesn't exist yet → empty
          `actions`, count=0 (system hasn't run the corp-actions
          backfill yet).
        - No matching events in window → empty `actions`, count=0.
    """
    return _reader().get_corp_actions(
        symbol,
        since=since,
        until=until,
        action_types=action_types,
    )
