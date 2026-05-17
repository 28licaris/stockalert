"""
Pydantic contracts for the screener service.

Declarative on purpose: an LLM agent can author a `ScreenerSpec`
dict safely. There's no expression evaluation, no DSL, no eval() —
just a closed set of rule kinds with named params. Extending the
screener vocabulary means adding a new `kind` enum value + a
matching rule-evaluator in `rules.py`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


SupportedInterval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

# Filter rule kinds. To add a new one:
#   1. Add a string here.
#   2. Add an evaluator in `app/services/screener/rules.py`.
#   3. Add a test in `tests/test_screener.py`.
# The registry pattern keeps the agent-facing surface tractable and
# adversary-safe (no code injection through a "rules" field).
RuleKind = Literal[
    "close_above_sma",       # latest close > SMA(period)
    "close_below_sma",       # latest close < SMA(period)
    "close_above_ema",       # latest close > EMA(period)
    "close_below_ema",
    "rsi_above",             # RSI(period) > threshold
    "rsi_below",             # RSI(period) < threshold
    "close_at_lower_band",   # close <= Bollinger lower band (volatility-envelope buy setup)
    "close_at_upper_band",   # close >= Bollinger upper band (volatility-envelope short / momentum)
    "atr_pct_above",         # ATR(period)/close > threshold (high volatility filter)
    "atr_pct_below",         # ATR(period)/close < threshold (low volatility / squeeze filter)
    "price_above",           # latest close > value
    "price_below",           # latest close < value
    "volume_above",          # latest volume > value
]


RankBy = Literal[
    "none",        # preserve universe order
    "volume",      # most-traded first
    "atr_pct",     # most volatile first
    "rsi",         # lowest RSI first (most oversold)
    "rsi_desc",    # highest RSI first (most overbought)
]


class ScreenerRule(BaseModel):
    """
    One declarative filter rule.

    `params` is rule-specific:
      - `close_above_sma`/`close_below_sma`: `{period: int}`
      - `close_above_ema`/`close_below_ema`: `{period: int}`
      - `rsi_above`/`rsi_below`: `{period: int, threshold: float}`
      - `close_at_lower_band`/`close_at_upper_band`:
            `{period: int, std_multiplier: float}` (Bollinger params)
      - `atr_pct_above`/`atr_pct_below`:
            `{period: int, threshold: float}` (threshold = fraction, e.g. 0.02 = 2%)
      - `price_above`/`price_below`: `{value: float}`
      - `volume_above`: `{value: float}`

    Bad params (missing keys, wrong type) raise at scan time with a
    clear message naming the rule + the missing key.
    """

    kind: RuleKind
    params: dict[str, Any] = Field(default_factory=dict)
    label: Optional[str] = Field(
        None,
        description=(
            "Optional human label for the rule. Echoed in the response "
            "metrics so the operator/agent can read 'why did this pass?'"
        ),
    )


class ScreenerSpec(BaseModel):
    """
    Full screener configuration. Serializable to YAML/JSON;
    agent-shareable; reproducible (the same spec + same data = the
    same candidates).
    """

    universe: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit symbol list. Either this OR `watchlist_name` must "
            "be set. If both are set, the union is used."
        ),
    )
    watchlist_name: Optional[str] = Field(
        None,
        description=(
            "Pull universe from an existing watchlist by name. Resolved "
            "via the watchlist_service at scan time."
        ),
    )
    interval: SupportedInterval = "1d"
    provider: str = Field(
        "polygon",
        description="Bronze provider for 1m queries; ignored for other intervals.",
    )
    lookback_bars: int = Field(
        250,
        ge=10, le=10_000,
        description=(
            "How many bars per symbol to fetch. Must be >= the slowest "
            "indicator period used by any rule + a small buffer."
        ),
    )
    rules: list[ScreenerRule] = Field(
        ...,
        min_length=1,
        description="One or more rules. All must pass for a symbol to qualify (logical AND).",
    )
    rank_by: RankBy = "volume"
    limit: int = Field(
        20,
        ge=1, le=1_000,
        description="Cap on number of candidates returned. Sorted by `rank_by` before truncation.",
    )

    @model_validator(mode="after")
    def _validate_universe_source(self) -> "ScreenerSpec":
        if not self.universe and not self.watchlist_name:
            raise ValueError(
                "ScreenerSpec must specify `universe` (symbol list) or "
                "`watchlist_name` (or both)."
            )
        return self


class CandidateMetric(BaseModel):
    """One named metric value attached to a Candidate for transparency."""

    name: str
    value: Optional[float] = None


class Candidate(BaseModel):
    """One symbol that passed all rules, with its diagnostic metrics."""

    symbol: str
    score: float = Field(
        ...,
        description=(
            "Sort key — interpretation depends on `ScreenerSpec.rank_by`. "
            "For `volume`/`atr_pct`/`rsi_desc`: higher = better. "
            "For `rsi`: lower = better (sorted ascending). "
            "Echoed for transparency; the response candidates are "
            "ALREADY sorted by this score so consumers can just iterate."
        ),
    )
    metrics: list[CandidateMetric] = Field(
        default_factory=list,
        description="Per-rule metric values that contributed to the pass/rank decision.",
    )


class ScreenerResult(BaseModel):
    """
    Output of a screener scan. Returned by both the HTTP route and
    the MCP tool — single contract across surfaces.
    """

    interval: SupportedInterval
    universe_size: int
    n_passed: int
    candidates: list[Candidate]
    rank_by: RankBy
    as_of: datetime
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned when reading from bronze (1m). "
            "None on CH-backed intervals. Lets a future call replay "
            "the screener against the same data."
        ),
    )
    rejected_count: int = Field(
        0,
        description=(
            "Number of universe symbols that failed AT LEAST one rule. "
            "Equal to `universe_size - n_passed` only when no symbols "
            "errored out; if some symbols had errors (missing data, "
            "etc.) they're counted as rejected."
        ),
    )
    errors: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Symbols that failed the scan with an error message. The "
            "scan completes even when individual symbols fail."
        ),
    )
