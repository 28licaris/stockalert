"""
Pre-FOMC drift calendar strategy (EXP-44 / H-25,H-26).

The EXP-41 screen found the platform's strongest signal in the single
trading day before scheduled FOMC announcements (p = 0.0010, PF 1.565
pooled, 2006-2018 — the Lucca-Moench drift). Tradeable next-open
rendering: enter so the fill lands `pre_weekdays` trading days before
the announcement; exit at the announcement day's open (before the 2pm
decision). Announcement dates are ex-ante public (the Fed publishes the
calendar years ahead) and are injected via params — the strategy stays
pure (no file/data-layer IO). Weekday counting approximates trading-day
counting; a market holiday adjacent to a meeting shifts the window by
one day, accepted as deterministic rendering noise.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


def _weekdays_between(a: date, b: date) -> int:
    """Count weekdays strictly between a and b (a < b)."""
    n, d = 0, a + timedelta(days=1)
    while d < b:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


class CalendarFomcParams(BaseModel):
    announcement_dates: list[str] = Field(
        description="ISO dates of scheduled FOMC announcements (ex-ante public).")
    pre_weekdays: int = Field(
        1, ge=1, le=5,
        description="Enter so the fill lands this many weekdays before the announcement.")
    position_size_pct: float = Field(0.95, gt=0.0, le=1.0)


class CalendarFomcStrategy(BaseStrategy):
    """Long the pre-FOMC window; flat otherwise."""

    name: str = "calendar_fomc"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[CalendarFomcParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        if params is None:
            raise ValueError("calendar_fomc requires params (announcement_dates)")
        self.params = params
        self.interval = interval
        self._dates = sorted(date.fromisoformat(d) for d in params.announcement_dates)

    def _next_announcement(self, today: date) -> Optional[date]:
        for d in self._dates:
            if d > today:
                return d
        return None

    def on_bar(self, ctx: Context) -> Action:
        p = self.params
        today = ctx.bar.timestamp.date()
        symbol = ctx.bar.symbol
        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0
        nxt = self._next_announcement(today)

        if not has_position:
            if nxt is None:
                return hold()
            # Enter when tomorrow's fill lands pre_weekdays before the meeting:
            # weekdays strictly between today and the announcement, minus the
            # fill day itself, equals pre_weekdays - 1  <=>  between == pre_weekdays.
            if _weekdays_between(today, nxt) == p.pre_weekdays:
                price = ctx.bar.close
                if price <= 0:
                    return hold()
                qty = math.floor((ctx.portfolio.cash * p.position_size_pct) / price)
                if qty <= 0:
                    return hold()
                ctx.log(event="fomc_enter", announcement=str(nxt))
                return Action(kind="buy", symbol=symbol, size=float(qty),
                              note=f"calendar_fomc: fill {p.pre_weekdays} weekday(s) before {nxt}")
            return hold()

        # Holding: exit decision at the close of the last bar BEFORE the
        # announcement day -> fill at the announcement day's open.
        if nxt is not None and _weekdays_between(today, nxt) == 0:
            ctx.log(event="fomc_exit", announcement=str(nxt), qty=position.quantity)
            return Action(kind="sell", symbol=symbol, size=position.quantity,
                          note=f"calendar_fomc: exit into {nxt} open")
        return hold()
