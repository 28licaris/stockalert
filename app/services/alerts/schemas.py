"""Alert payloads — WaveAlert (wave count) + MACrossoverAlert (MA cross)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MACrossoverAlert(BaseModel):
    """
    A price-vs-moving-average crossover event.

    Carries its source aggregation explicitly: a crossover is only
    interpretable later if you know which timeframe the MA was computed
    over. "AAPL crossed above the 200 SMA" is ambiguous; this payload says
    `{ma: sma, source_agg: 1d, length: 200, display_agg: 5m}` — a daily
    200-SMA crossed on a 5-minute chart. See the MA timeframe spec's
    "Alert Semantics".
    """

    symbol: str
    direction: Literal["bullish", "bearish"]  # price crossed above / below the MA
    ma: Literal["sma", "ema", "wma"]
    length: int = Field(..., description="MA bar count (the 200 in '200 SMA').")
    source_agg: str = Field(
        ...,
        description="Aggregation the MA was computed over, e.g. '1d'. Equals "
        "display_agg for an ordinary same-interval crossover.",
    )
    display_agg: str = Field(..., description="Chart interval the close came from, e.g. '5m'.")
    crossed_at: datetime = Field(..., description="Timestamp of the display bar that crossed.")
    price: float = Field(..., description="Display-TF close at the crossover bar.")
    ma_value: float = Field(..., description="Forward-filled MA value at the crossover bar.")

    @property
    def setup(self) -> str:
        """Stable identifier, e.g. 'sma200_1d_cross_above'."""
        side = "above" if self.direction == "bullish" else "below"
        return f"{self.ma}{self.length}_{self.source_agg}_cross_{side}"


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
