"""Research rankings API schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RankingRow(BaseModel):
    symbol: str
    name: str = ""
    price: float | None = None
    chg_1d_pct: float | None = Field(default=None, description="1-day % change since prior close.")
    ret_pct: float | None = Field(default=None, description="Return over the lookback window (%).")
    dollar_vol: float | None = Field(default=None, description="Avg daily dollar-volume.")
    up_streak: int = 0
    down_streak: int = 0


class RankingsResponse(BaseModel):
    as_of: str = Field(description="Latest close date in the ranking universe (results are as-of this).")
    preset: str
    lookback_days: int
    count: int
    rows: list[RankingRow]
