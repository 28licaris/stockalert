"""
Pydantic contracts for the trading-subsystem (`app/services/sim/`).

These shapes are the **public interface** used by strategies, the
backtester, the evaluator, the registry, and any consumer (CLI, MCP
tool, future feature-server). Implementations import these and
nothing else from the sim package — that's how the modularity
contracts in `trading_subsystem_design.md` get enforced.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# Bar Protocol — minimal interface both BronzeBar and LiveBar satisfy.
# Strategies and the backtester depend on this Protocol, not on the
# concrete classes, so different bar sources plug in interchangeably.
# ─────────────────────────────────────────────────────────────────────


@runtime_checkable
class Bar(Protocol):
    """Anything with symbol + timestamp + OHLCV is a Bar."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


# ─────────────────────────────────────────────────────────────────────
# Action — strategy output. One per Strategy.on_bar call.
# ─────────────────────────────────────────────────────────────────────


ActionKind = Literal["hold", "buy", "sell", "set_position"]


class Action(BaseModel):
    """
    What the strategy wants to do next bar.

    Fill semantics live in the SlippageModel — by default the next
    bar's open. Strategies emit *intent*; the harness produces the
    *fill*.

    `set_position` is a target-quantity action: the harness computes
    the delta against the current position and emits an internal
    buy/sell. Useful for portfolio strategies that think in terms of
    "I want 100 shares of AAPL right now."
    """

    kind: ActionKind = "hold"
    symbol: str = ""
    size: float = Field(
        0.0,
        description=(
            "For 'buy'/'sell': shares (or fractional) to trade. "
            "For 'set_position': target absolute quantity. "
            "For 'hold': ignored. Negative not allowed — sell short "
            "must be expressed via a separate signed-quantity contract."
        ),
    )
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    note: str = Field("", description="Optional reason from the strategy (for audit + log).")


def hold() -> Action:
    """Convenience: the no-op action."""
    return Action(kind="hold")


# ─────────────────────────────────────────────────────────────────────
# Position + Trade — portfolio state primitives.
# ─────────────────────────────────────────────────────────────────────


class Position(BaseModel):
    """One open position. quantity > 0 for long, < 0 reserved for short (Phase TA-5+)."""

    symbol: str
    quantity: float
    avg_entry_price: float
    entry_time: datetime
    unrealized_pnl: float = 0.0


class Trade(BaseModel):
    """One executed fill. Recorded in the portfolio's trade log."""

    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float          # the fill price after slippage
    timestamp: datetime   # the bar that produced the fill
    fees: float = 0.0
    realized_pnl: float = 0.0    # populated on a closing trade
    holding_days: float = 0.0    # calendar days held (populated on a closing leg)
    is_closing: bool = False     # True on the leg that realizes P&L (sell-to-close long OR buy-to-cover short)
    note: str = ""


class PortfolioSnapshot(BaseModel):
    """
    Immutable view of portfolio state at one moment. Passed to the
    strategy via `Context.portfolio` so strategies can't mutate
    portfolio state (only the harness can).
    """

    cash: float
    equity: float
    positions: dict[str, Position] = Field(default_factory=dict)
    n_trades: int = 0


# ─────────────────────────────────────────────────────────────────────
# RunMetrics — what the evaluator produces.
# ─────────────────────────────────────────────────────────────────────


class RunMetrics(BaseModel):
    """Canonical performance metrics for one backtest run."""

    total_return: float = Field(..., description="final_equity / starting_cash - 1")
    annualized_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: float = Field(0.0, description="Negative number; -0.15 = 15% peak-to-trough decline.")
    longest_drawdown_days: int = 0
    win_rate: Optional[float] = Field(None, description="n_winning / n_trades. None when n_trades == 0.")
    profit_factor: Optional[float] = Field(None, description="sum(wins) / abs(sum(losses)). None when no losses.")
    n_trades: int = 0
    avg_trade_pnl: Optional[float] = None
    avg_winner_pnl: Optional[float] = None
    avg_loser_pnl: Optional[float] = None
    avg_holding_days: Optional[float] = Field(
        None, description="Mean calendar days held per round-trip (time-in-trade).",
    )
    final_equity: float = 0.0


# ─────────────────────────────────────────────────────────────────────
# BacktestConfig — declarative spec for one run.
# ─────────────────────────────────────────────────────────────────────


SupportedInterval = Literal["1d", "1h", "30m", "15m", "5m", "1m"]
SupportedProvider = Literal["polygon", "schwab"]


class BacktestConfig(BaseModel):
    """
    Declarative spec for one backtest run. Serializable to YAML/JSON
    so runs are reproducible and agent-shareable.
    """

    symbols: list[str] = Field(..., min_length=1)
    start: datetime
    end: datetime
    interval: SupportedInterval = Field(
        "1d",
        description=(
            "Single-timeframe interval. For multi-TF strategies this "
            "must match `intervals[-1]` (the execution interval). The "
            "Backtester validates strategy/config interval agreement."
        ),
    )
    intervals: Optional[list[SupportedInterval]] = Field(
        None,
        description=(
            "Optional multi-timeframe list (coarsest-to-finest). When "
            "set, takes precedence over `interval`; bars are fetched "
            "for every entry, and the Context exposes them via "
            "`history_at(interval)` and `indicator(..., interval=...)`. "
            "The execution interval (the one the harness iterates on) "
            "is `intervals[-1]`. Strategies declare their required "
            "intervals as a class attribute; this config field is the "
            "operator's chance to override at runtime."
        ),
    )
    provider: SupportedProvider = "polygon"
    benchmark: Optional[str] = Field(
        None,
        description=(
            "Optional benchmark symbol (e.g. 'SPY'). When set, the engine loads "
            "it once and exposes a MarketContext on `ctx.market` for "
            "market-relative filters (regime, relative strength)."
        ),
    )
    starting_cash: float = 40_000.0
    history_window: int = Field(
        200,
        ge=1,
        le=10_000,
        description="Maximum bars retained in Context.history per interval. Sized to the slowest indicator a strategy uses.",
    )

    # Fees + slippage are referenced by name; their params live in
    # the dict so different models can be swapped without schema
    # changes. The Backtester resolves names against the FeeModel /
    # SlippageModel registries.
    fees_model: Literal["zero", "per_share", "percent"] = "per_share"
    fees_params: dict[str, Any] = Field(default_factory=dict)
    slippage_model: Literal["next_bar_open", "percent"] = "next_bar_open"
    slippage_params: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# RunResult — what the backtester returns + what we persist.
# ─────────────────────────────────────────────────────────────────────


class RunResult(BaseModel):
    """
    Everything a consumer needs to evaluate and reproduce a backtest.
    Persisted (slimmed) into `agent_runs`; full version returned to
    the caller.
    """

    run_id: UUID = Field(default_factory=uuid4)
    started_at: datetime
    finished_at: datetime

    strategy_name: str
    strategy_version: str
    strategy_params: dict[str, Any]

    config: BacktestConfig
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned at run start. Identifies the "
            "exact bronze data the run read. None if the bar source "
            "couldn't produce a snapshot id (live tier, test fixtures)."
        ),
    )
    git_sha: str = ""

    metrics: RunMetrics
    equity_curve: list[tuple[datetime, float]] = Field(default_factory=list)
    trades: list[Trade] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Convenience: explicit re-export of supported intervals.
# ─────────────────────────────────────────────────────────────────────

SUPPORTED_INTERVALS: tuple[str, ...] = (
    "1d", "1h", "30m", "15m", "5m", "1m",
)
