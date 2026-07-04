"""
Hourly-triggered 1-day swing entries (EXP-49 / H-37,H-38).

Two screen-validated triggers (EXP-48), locked:
  gap_hold          — day opens >= gap_min above the prior close AND is
                      still at/above its open at the 2nd bar's close
                      (the gap survived its first test) -> buy.
  first_hour_break  — the first bar closes above the PRIOR session's
                      high -> buy.
Exit: fixed timer — decision `hold_bars` bars after entry (fill next
open ≈ the same time next session for hold_bars=7).

Pure strategy: per-symbol session state (prior day high/close, today's
first open, bar count) is derived from the bars alone on the ET session
clock (naive timestamps are UTC by platform convention).
"""
from __future__ import annotations

import logging
import math
from datetime import timezone as _tz
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


class HourlySwingParams(BaseModel):
    trigger: Literal["gap_hold", "first_hour_break"] = "gap_hold"
    gap_min: float = Field(0.01, gt=0, description="gap_hold: minimum overnight gap.")
    hold_bars: int = Field(7, ge=1, description="Exit decision this many bars after entry.")
    position_size_pct: float = Field(0.12, gt=0.0, le=1.0)


class _DayState(BaseModel):
    date: str = ""
    bar_idx: int = -1
    first_open: float = 0.0
    day_high: float = 0.0
    last_close: float = 0.0
    prev_high: float = 0.0
    prev_close: float = 0.0


class HourlySwingStrategy(BaseStrategy):
    name: str = "hourly_swing"
    version: str = "0.1"
    interval: str = "1h"

    def __init__(
        self,
        params: Optional[HourlySwingParams] = None,
        *,
        interval: str = "1h",
    ) -> None:
        self.params = params or HourlySwingParams()
        self.interval = interval
        self._day: dict[str, _DayState] = {}
        self._bars_held: dict[str, int] = {}

    def on_bar(self, ctx: Context) -> Action:
        p = self.params
        bar = ctx.bar
        ts = bar.timestamp
        if ts.tzinfo is None:  # naive = UTC by platform convention
            ts = ts.replace(tzinfo=_tz.utc)
        day = ts.astimezone(_ET).date().isoformat()
        symbol = bar.symbol

        st = self._day.setdefault(symbol, _DayState())
        if st.date != day:  # session roll: yesterday's aggregates become "prior"
            st.prev_high, st.prev_close = st.day_high, st.last_close
            st.date, st.bar_idx = day, 0
            st.first_open, st.day_high = bar.open, bar.high
        else:
            st.bar_idx += 1
            st.day_high = max(st.day_high, bar.high)
        st.last_close = bar.close

        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        if has_position:
            self._bars_held[symbol] = self._bars_held.get(symbol, 0) + 1
            if self._bars_held[symbol] >= p.hold_bars:
                self._bars_held.pop(symbol, None)
                ctx.log(event="swing_exit", trigger=p.trigger)
                return Action(kind="sell", symbol=symbol, size=position.quantity,
                              note=f"hourly_swing[{p.trigger}]: timer exit")
            return hold()

        if st.prev_close <= 0:  # first session in history — no prior day yet
            return hold()

        triggered = False
        if p.trigger == "gap_hold" and st.bar_idx == 1:
            gapped = st.first_open / st.prev_close - 1.0 >= p.gap_min
            triggered = gapped and bar.close >= st.first_open
        elif p.trigger == "first_hour_break" and st.bar_idx == 0:
            triggered = st.prev_high > 0 and bar.close > st.prev_high

        if triggered:
            price = bar.close
            if price <= 0:
                return hold()
            qty = math.floor((ctx.portfolio.cash * p.position_size_pct) / price)
            if qty <= 0:
                return hold()
            self._bars_held[symbol] = 0
            ctx.log(event="swing_enter", trigger=p.trigger, bar_idx=st.bar_idx)
            return Action(kind="buy", symbol=symbol, size=float(qty),
                          note=f"hourly_swing[{p.trigger}]: entry")
        return hold()
