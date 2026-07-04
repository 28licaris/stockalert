"""
Hourly pre-FOMC drift (EXP-47 / H-32) — the screen-validated sharpening
of calendar_fomc: enter at the PRIOR day's close (decision at the
14:30 session bar -> fill at the 15:30 bar's open), hold overnight and
through the announcement morning, exit before the 2pm decision
(decision at the 12:30 bar -> fill at the 13:30 bar's open). Runs on
1-hour session bars. Announcement dates are ex-ante public; defaults to
the built-in Fed calendar.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategies.fomc_calendar import FOMC_ANNOUNCEMENT_DATES
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CalendarFomcHourlyParams(BaseModel):
    announcement_dates: list[str] = Field(
        default_factory=lambda: list(FOMC_ANNOUNCEMENT_DATES))
    entry_decision_time: str = Field(
        "14:30", description="Bar START time whose close triggers entry (fill next bar).")
    exit_decision_time: str = Field(
        "12:30", description="Announcement-day bar START whose close triggers exit "
                             "(fill next bar open — before the 2pm decision).")
    position_size_pct: float = Field(0.95, gt=0.0, le=1.0)


class CalendarFomcHourlyStrategy(BaseStrategy):
    """Long from the pre-announcement afternoon into ~13:30 on decision day."""

    name: str = "calendar_fomc_hourly"
    version: str = "0.1"
    interval: str = "1h"

    def __init__(
        self,
        params: Optional[CalendarFomcHourlyParams] = None,
        *,
        interval: str = "1h",
    ) -> None:
        self.params = params or CalendarFomcHourlyParams()
        self.interval = interval
        self._dates = sorted(date.fromisoformat(d) for d in self.params.announcement_dates)

    def _is_pre_announcement_day(self, today: date) -> bool:
        """Is the NEXT weekday after `today` an announcement day?"""
        nxt = today + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt in set(self._dates)

    def on_bar(self, ctx: Context) -> Action:
        p = self.params
        ts = ctx.bar.timestamp
        today = ts.date()
        hhmm = ts.strftime("%H:%M")
        symbol = ctx.bar.symbol
        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        if not has_position:
            if hhmm == p.entry_decision_time and self._is_pre_announcement_day(today):
                price = ctx.bar.close
                if price <= 0:
                    return hold()
                qty = math.floor((ctx.portfolio.cash * p.position_size_pct) / price)
                if qty <= 0:
                    return hold()
                ctx.log(event="fomc_hourly_enter", decision_bar=hhmm)
                return Action(kind="buy", symbol=symbol, size=float(qty),
                              note=f"calendar_fomc_hourly: prior-close entry ({hhmm} bar)")
            return hold()

        if today in set(self._dates) and hhmm == p.exit_decision_time:
            ctx.log(event="fomc_hourly_exit", decision_bar=hhmm, qty=position.quantity)
            return Action(kind="sell", symbol=symbol, size=position.quantity,
                          note="calendar_fomc_hourly: exit before the decision")
        return hold()
