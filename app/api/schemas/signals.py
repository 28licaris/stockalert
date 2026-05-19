"""
Live divergence-signal response schema. Backs the legacy /api/signals
dashboard feed, the cockpit Symbol page's chart markers, and (in
FE-CONTRACTS-7) the `signals` WebSocket topic.

Wire shape preserved byte-for-byte from the legacy /api/signals route
so the static dashboard.html keeps parsing it through the transition.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Signal(BaseModel):
    """A divergence (or other rule-fired) signal at a moment in time."""

    symbol: str
    type: str = Field(
        ...,
        description=(
            "Signal classifier. Examples: 'regular_bullish_divergence', "
            "'hidden_bearish_divergence', 'rsi_oversold'. Values come from "
            "the live monitor; cockpit treats this as a free-form string and "
            "branches on prefixes (bull/bear) where needed."
        ),
    )
    indicator: str = Field(
        ...,
        description="Indicator that produced the signal, e.g. 'rsi', 'macd'.",
    )
    ts: str = Field(
        ...,
        description="ISO 8601 timestamp with `Z` suffix. String (not datetime) to match the legacy `_ts()` formatting exactly.",
    )
    price: Optional[float] = Field(
        default=None,
        description="Close price at the bar that fired the signal.",
    )
    indicator_value: Optional[float] = Field(
        default=None,
        description="Indicator reading at the bar (e.g. RSI=28.3). Null when not stored.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "AAPL",
                "type": "regular_bullish_divergence",
                "indicator": "rsi",
                "ts": "2026-05-18T19:30:00Z",
                "price": 297.42,
                "indicator_value": 31.7,
            }
        }
    )
