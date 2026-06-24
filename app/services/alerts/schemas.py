"""WaveAlert — a self-defining trade plan derived from a wave count."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel


class WaveAlert(BaseModel):
    symbol: str
    asset_class: str
    interval: str
    setup: str                              # e.g. "wave3_entry", "wave5_entry"
    direction: Literal["long", "short"]
    trade_type: Literal["day", "swing"]     # derived from the setup's interval
    probability: float                      # primary count probability
    entry: float                            # current price (the count's as_of price)
    stop: float                             # = count invalidation
    target_1: float
    target_2: Optional[float] = None
    risk_reward: float                      # (target_1 - entry) / (entry - stop), direction-aware
    current_wave: str
    as_of_date: Optional[date] = None
    rationale: str = ""
