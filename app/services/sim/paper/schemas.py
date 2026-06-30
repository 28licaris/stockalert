"""Paper-trading contracts (M3)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PaperRunConfig(BaseModel):
    """The LOCKED strategy + portfolio settings being paper-traded forward.

    `history_start` seeds the equity curve (backtest context); `go_live` is the
    moment we committed to this exact config — only the slice AFTER it counts as
    the real forward track record (honesty doctrine). Changing any field below
    invalidates the forward record and should start a new paper run.
    """
    name: str
    strategy: str
    strategy_params: dict[str, Any]
    symbols: list[str]
    interval: str = "1d"
    benchmark: Optional[str] = "SPY"
    starting_cash: float = 100_000.0
    max_concurrent_positions: int = 10
    max_portfolio_heat: float = 0.12
    momentum_top_n: Optional[int] = None
    momentum_bottom_n: Optional[int] = None
    momentum_lookback: int = 60
    history_window: int = 300
    history_start: datetime
    go_live: datetime


class PaperPositionView(BaseModel):
    symbol: str
    quantity: float
    avg_entry_price: float
    entry_time: datetime
    unrealized_pnl: float = 0.0


class PaperTradeView(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: datetime
    realized_pnl: float = 0.0
    holding_days: float = 0.0
    is_closing: bool = False
    entry_date: Optional[datetime] = None   # when the position was opened
    exit_date: Optional[datetime] = None    # when it was closed (None if still open)


class PaperEquityPoint(BaseModel):
    t: datetime
    equity: float


class PaperState(BaseModel):
    """Persisted paper-run state (the locked config + the latest computed run)."""
    config: PaperRunConfig
    last_run_at: Optional[datetime] = None
    computed_through: Optional[datetime] = None
    equity_curve: list[tuple[datetime, float]] = Field(default_factory=list)
    trades: list[dict] = Field(default_factory=list)
    open_positions: list[dict] = Field(default_factory=list)


class PaperStatus(BaseModel):
    """The forward track record served to the dashboard.

    The equity curve / trades / balances are REBASED to `starting_capital` as of
    `start_date` (a pure scale of the persisted run — risk-% sizing is ~scale-
    invariant, so this projects the same strategy at a chosen capital instantly,
    without re-running). `start_date` defaults to go_live but can be set earlier to
    replay the strategy forward from a past date.
    """
    name: str
    go_live: datetime
    start_date: datetime              # effective replay start (override or go_live)
    last_run_at: Optional[datetime]
    computed_through: Optional[datetime]
    days_live: int
    starting_capital: float           # capital at start_date (rebased)
    current_balance: float            # latest equity (rebased)
    forward_return: float
    forward_n_trades: int
    forward_win_rate: Optional[float]
    n_open_positions: int
    open_positions: list[PaperPositionView]
    forward_trades: list[PaperTradeView]
    equity_curve: list[PaperEquityPoint]   # full curve; go_live marks the live boundary
    # "Today" = the latest computed bar date — the alertable activity for this run.
    today_entries: list[PaperPositionView] = Field(default_factory=list)
    today_exits: list[PaperTradeView] = Field(default_factory=list)
