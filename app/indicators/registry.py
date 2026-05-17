"""
Indicator registry — string name → class mapping.

Strategies request indicators by name via `Context.indicator(name,
**params)`. This module is the **single source of truth** for the
name→class map; adding an indicator = add to this registry, done.
Strategies don't import indicator classes directly.

This indirection is what makes "swap an indicator without touching
strategy code" possible — a future ATR-based strategy doesn't need
to know ATR's import path, just its name.
"""
from __future__ import annotations

from typing import Callable

from app.indicators.base import Indicator
from app.indicators.ema import EMA
from app.indicators.macd import MACD
from app.indicators.rsi import RSI
from app.indicators.sma import SMA
from app.indicators.tsi import TSI


# Factory entries — each value is a callable that takes the params
# dict and returns an Indicator instance. Using a callable (vs the
# class directly) keeps the registry uniform when an indicator has
# non-trivial param wiring (e.g. MACD's three periods).
_INDICATOR_REGISTRY: dict[str, Callable[..., Indicator]] = {
    # Moving averages
    "sma": SMA,
    "ema": EMA,
    # Momentum
    "rsi": RSI,
    "macd": MACD,
    "tsi": TSI,
}


def get_indicator(name: str, **params) -> Indicator:
    """
    Build an indicator instance by name.

    Raises `ValueError` for unknown names with a list of supported
    names — agents calling with a typo get useful feedback.
    """
    factory = _INDICATOR_REGISTRY.get(name.lower())
    if factory is None:
        supported = ", ".join(sorted(_INDICATOR_REGISTRY))
        raise ValueError(f"Unknown indicator {name!r}. Supported: {supported}.")
    return factory(**params)


def list_indicators() -> list[str]:
    """Return supported indicator names (sorted)."""
    return sorted(_INDICATOR_REGISTRY)
