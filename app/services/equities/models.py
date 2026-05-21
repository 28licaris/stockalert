"""
Pydantic runtime models for v2 equities domain objects (CV14).

Moved here from `app/services/silver/schemas.py` when the silver
layer was deleted in CV14. The models survived the v1→v2 cutover
because they describe the canonical domain shapes (corp-actions,
adjusted OHLCV bars) that readers + MCP tools + HTTP routes all
exchange — they aren't tied to a specific storage layer.

The Iceberg schemas that lived alongside these in v1 silver
(`SILVER_CORP_ACTIONS_SCHEMA`, `SILVER_OHLCV_1M_SCHEMA`, etc.) are
NOT carried forward — their v2 replacements live in
`app/services/equities/schemas.py` (CV1).

Why "SilverBar" still has the silver name: the class is consumed by
the renamed-in-future-CV-not-CV14 `SilverOhlcvReader` and the v1
public API contract (`/api/silver/bars/...`). Renaming the class +
the routes + the MCP tool surfaces is a follow-up clean-up. The
class today represents "an adjusted OHLCV bar from the lake" — call
it that mentally; the literal name stays for caller compatibility.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# CorpAction — one corporate-action event for one symbol on one ex-date
# ─────────────────────────────────────────────────────────────────────

CorpActionKind = Literal[
    "split",                # forward stock split (factor > 1) or reverse (factor < 1)
    "cash_dividend",        # ordinary cash dividend (Polygon dividend_type CD)
    "lt_capital_gain",      # long-term capital-gains distribution (Polygon dividend_type LT)
    "st_capital_gain",      # short-term capital-gains distribution (Polygon dividend_type ST)
    "stock_dividend",       # stock dividend (paid in shares) (Polygon dividend_type SC)
    "spinoff",              # spin-off distribution (Polygon dividend_type SP)
]
# Why these are separate (not collapsed under cash_dividend):
# A fund/ETF can issue MULTIPLE distributions on the same ex_date —
# e.g. an ordinary cash dividend (CD) + a long-term cap-gains
# distribution (LT) + a short-term cap-gains distribution (ST), all
# on the same day. Collapsing them collides on the identifier
# (symbol, ex_date, action_type). They're also semantically distinct
# for tax + ML feature purposes. Keep them as separate kinds.


class CorpAction(BaseModel):
    """
    One corporate-action event for one symbol on one ex-date.

    Canonical contract: the same shape produced by ingestion is what
    readers return and what MCP tools expose. Pydantic-validated at
    every boundary.
    """

    symbol: str
    ex_date: date = Field(
        ...,
        description=(
            "Ex-dividend / ex-split date in the issuer's calendar. "
            "Bars on or after this date reflect the corporate-action "
            "effect; bars before need adjustment to compare."
        ),
    )
    action_type: CorpActionKind

    factor: Optional[float] = Field(
        None,
        description=(
            "Split ratio for splits + stock dividends (e.g. 4.0 for a "
            "4-for-1 forward split; 0.5 for a 1-for-2 reverse split; "
            "1.05 for a 5% stock dividend). NULL for cash-only actions."
        ),
    )
    cash_amount: Optional[float] = Field(
        None,
        description=(
            "Dividend per share in USD. NULL for splits."
        ),
    )

    announced_at: Optional[datetime] = Field(
        None,
        description="When the action was announced (provider-supplied; UTC).",
    )
    source_provider: str = Field(
        default="polygon",
        description=(
            "Canonical source. `polygon` for everything we ingest today. "
            "When alternative corp-action providers are added, precedence "
            "is `polygon > <new>` (config-driven)."
        ),
    )
    ingestion_ts: Optional[datetime] = Field(
        None,
        description="When this row was written into equities.market_corp_actions (UTC).",
    )
    ingestion_run_id: Optional[str] = Field(
        None,
        description=(
            "Run ID linking this row to an ingest invocation. Lets "
            "operators answer 'which ingest job produced this corp-action?'"
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# SilverBar — one adjusted OHLCV bar (post-CV14 sources from lake)
# ─────────────────────────────────────────────────────────────────────


class SilverBar(BaseModel):
    """One canonical 1-minute split-adjusted OHLCV bar.

    Post-CV11/CV14: sourced from `equities.polygon_adjusted` (built by
    the weekly Spark adjustment job) or `equities.schwab_universe`
    (live + tip-fill). Both stores are pre-adjusted; consumers see one
    set of split-adjusted OHLCV columns — what chart, indicators,
    backtests, screener, and ML all need (continuous lines across
    split events, no fake gaps).

    **If a consumer needs raw prices** (trade-tape replay, fill
    reconciliation): multiply the adjusted value by the bar's
    `adj_factor` (stored on every polygon_adjusted row in CV1's
    schema, defaults to 1.0 on schwab_universe rows). The reader
    surfaces adj_factor only when explicitly asked — the default
    SilverBar carries the adjusted view.
    """

    symbol: str
    timestamp: datetime

    # OHLCV — split-adjusted. Canonical consumer view.
    open: float
    high: float
    low: float
    close: float
    volume: int

    # Optional provider-supplied fields (NULL in some providers).
    vwap: Optional[float] = None
    trade_count: Optional[int] = None

    # Provenance — which provider's data this bar came from.
    source_provider: str = Field(
        ...,
        description=(
            "Provider source tag for this bar. Examples: "
            "'polygon-adjusted' for rows from equities.polygon_adjusted "
            "(the Spark adjustment job's output); 'schwab-rest' / "
            "'schwab-live' for rows from equities.schwab_universe."
        ),
    )
    sources_seen: list[str] = Field(
        default_factory=list,
        description=(
            "v1-silver multi-provider artifact; in v2 the equities tables "
            "are single-provider per row so this stays empty. Preserved on "
            "the contract for backwards compatibility."
        ),
    )

    ingestion_ts: Optional[datetime] = Field(
        None, description="When the underlying lake row was written (UTC).",
    )
    ingestion_run_id: Optional[str] = Field(
        None, description="Ingest/build run that produced the row.",
    )


__all__ = [
    "CorpAction",
    "CorpActionKind",
    "SilverBar",
]
