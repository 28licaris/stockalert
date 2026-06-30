"""
Strategy loader — the single place to instantiate a strategy by name.

Shared by the CLI (`scripts/run_backtest.py`), the MCP tools, and the backtest
HTTP API so a new strategy is registered ONCE. Lazy imports keep this module
dep-light (and past the strategies purity gate — it's not under strategies/).
"""
from __future__ import annotations

from typing import Any

# Names exposed to the catalog/UI. `alert_driven` is the configurable one
# (pluggable signal source + composable A+ filters); the rest are canaries.
STRATEGY_NAMES: list[str] = [
    "alert_driven", "sma_crossover", "ema_crossover",
    "rsi_reversion", "bollinger_mean_revert", "mtf_ema_trend_filtered", "llm_agent",
]


def build_strategy(name: str, params: dict[str, Any], interval: str) -> Any:
    if name == "alert_driven":
        from app.services.sim.strategies.alert_strategy import AlertStrategy, AlertStrategyParams
        return AlertStrategy(AlertStrategyParams(**params), interval=interval)
    if name == "sma_crossover":
        from app.services.sim.strategies.sma_crossover import SmaCrossoverParams, SmaCrossoverStrategy
        return SmaCrossoverStrategy(params=SmaCrossoverParams(**params), interval=interval)
    if name == "ema_crossover":
        from app.services.sim.strategies.ema_crossover import EmaCrossoverParams, EmaCrossoverStrategy
        return EmaCrossoverStrategy(params=EmaCrossoverParams(**params), interval=interval)
    if name == "rsi_reversion":
        from app.services.sim.strategies.rsi_reversion import RsiReversionParams, RsiReversionStrategy
        return RsiReversionStrategy(params=RsiReversionParams(**params), interval=interval)
    if name == "bollinger_mean_revert":
        from app.services.sim.strategies.bollinger_mean_revert import (
            BollingerMeanRevertParams, BollingerMeanRevertStrategy,
        )
        return BollingerMeanRevertStrategy(params=BollingerMeanRevertParams(**params), interval=interval)
    if name == "mtf_ema_trend_filtered":
        from app.services.sim.strategies.mtf_ema_trend_filtered import (
            MtfEmaTrendFilteredParams, MtfEmaTrendFilteredStrategy,
        )
        return MtfEmaTrendFilteredStrategy(params=MtfEmaTrendFilteredParams(**params))
    if name == "llm_agent":
        from app.services.sim.strategies.llm_agent import LLMAgentParams, LLMAgentStrategy
        return LLMAgentStrategy(params=LLMAgentParams(**params), interval=interval)
    raise ValueError(
        f"Unknown strategy {name!r}. One of: {', '.join(STRATEGY_NAMES)}."
    )
