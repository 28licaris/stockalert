"""
Turn-of-month calendar strategy (EXP-43 / H-23,H-24).

The EXP-41 screen validated month-boundary flow returns (last ~5 trading
days of the month + first 2 of the next; p = 0.011, q = 0.044,
noise-ROBUST). This is the tradeable next-open rendering:

  - ENTER: decision on the first bar with <= `enter_calendar_days`
    calendar days remaining in the month (fill next open, landing
    ~5 trading days before month-end). The calendar is ex-ante
    knowledge — no look-ahead.
  - EXIT: decision at the close of the `exit_after_bars`-th trading day
    of the NEW month (fill next open).

Modularity contract (docs/standards/trading_subsystem.md): pure function
of bar timestamps + portfolio state; no data-layer imports;
interval-agnostic in principle but registered for daily use.
"""
from __future__ import annotations

import calendar
import logging
import math
from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CalendarTomParams(BaseModel):
    enter_calendar_days: int = Field(
        8, ge=1, le=15,
        description="Enter when this many (or fewer) calendar days remain in the month.",
    )
    exit_after_bars: int = Field(
        2, ge=1, le=10,
        description="Exit at the close of the Nth trading day of the new month.",
    )
    position_size_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Fraction of cash deployed per symbol on entry.",
    )


class CalendarTomStrategy(BaseStrategy):
    """Long the turn-of-month window; flat the rest of the month."""

    name: str = "calendar_tom"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[CalendarTomParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or CalendarTomParams()
        self.interval = interval
        # Per-symbol count of bars seen in the current (new) month while
        # holding — drives the exit. Strategy-owned state per the contract.
        self._bars_into_month: dict[str, int] = {}
        self._entry_month: dict[str, tuple[int, int]] = {}

    def on_bar(self, ctx: Context) -> Action:
        p = self.params
        ts = ctx.bar.timestamp
        symbol = ctx.bar.symbol
        days_in_month = calendar.monthrange(ts.year, ts.month)[1]
        days_remaining = days_in_month - ts.day

        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        if not has_position:
            self._bars_into_month.pop(symbol, None)
            if days_remaining <= p.enter_calendar_days:
                price = ctx.bar.close
                if price <= 0:
                    return hold()
                qty = math.floor((ctx.portfolio.cash * p.position_size_pct) / price)
                if qty <= 0:
                    return hold()
                self._entry_month[symbol] = (ts.year, ts.month)
                ctx.log(event="tom_enter", days_remaining=days_remaining, qty=qty)
                return Action(
                    kind="buy", symbol=symbol, size=float(qty),
                    note=f"calendar_tom: {days_remaining} calendar days to month-end",
                )
            return hold()

        # Holding: count trading days once the NEW month begins, exit after N.
        entry_ym = self._entry_month.get(symbol)
        if entry_ym is not None and (ts.year, ts.month) != entry_ym:
            n = self._bars_into_month.get(symbol, 0) + 1
            self._bars_into_month[symbol] = n
            if n >= p.exit_after_bars:
                ctx.log(event="tom_exit", bars_into_month=n, qty=position.quantity)
                self._bars_into_month.pop(symbol, None)
                self._entry_month.pop(symbol, None)
                return Action(
                    kind="sell", symbol=symbol, size=position.quantity,
                    note=f"calendar_tom: day {n} of new month",
                )
        return hold()
