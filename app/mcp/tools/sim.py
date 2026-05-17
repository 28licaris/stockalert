"""
MCP tools — backtest execution + run-history access.

These tools close the loop for AGENT-DRIVEN STRATEGY ITERATION: an
LLM agent can run a backtest, inspect the result, then propose a
different strategy config and run again. Same Pydantic shapes as
the CLI; the tools are thin adapters over `Backtester.run` and the
`agent_runs` registry.

Design notes:

  - `run_backtest` is a heavy tool — a single 1-day intraday call
    might pull thousands of bars from bronze and run the harness
    end-to-end. Defaults assume the agent has thought about scope;
    we cap with explicit max symbol/bar windows in docstrings, not
    hard limits in code. (Real cost ceiling is set at the model
    layer — the agent's own context-window budget.)

  - `list_strategy_runs` returns the slim metadata view from
    `agent_runs`, NOT the full equity-curve / trade-log payload.
    Those go to S3 in a future slice if they outgrow CH row size;
    today they're in `metrics_full` JSON inside the row and the
    agent can fetch a specific run by ID if it really needs them.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.sim.backtester import Backtester
from app.services.sim.registry import list_runs as _list_runs
from app.services.sim.schemas import BacktestConfig, RunMetrics

logger = logging.getLogger(__name__)


@mcp.tool()
def run_backtest(
    strategy_name: Literal[
        "sma_crossover", "ema_crossover", "llm_agent",
        "rsi_reversion", "bollinger_mean_revert",
    ],
    strategy_params: dict[str, Any],
    config: dict[str, Any],
    write_to_registry: bool = True,
) -> RunMetrics:
    """Run a backtest end-to-end and return the canonical metrics.

    USE WHEN: an agent wants to evaluate a strategy configuration
    against historical data — "how would SMA(20)/SMA(60) crossover
    have performed on AAPL last year", "let me try this LLM prompt
    variant and see what Sharpe it produces."

    Args:
        strategy_name: Registered strategy. 'sma_crossover' is the
            canary; 'llm_agent' wraps Claude (requires
            ANTHROPIC_API_KEY in the server's environment).
        strategy_params: Strategy-specific Pydantic params as a dict.
            For sma_crossover: fast_period / slow_period /
            position_size_pct. For llm_agent: model / context_bars /
            indicators / system_prompt / temperature / max_tokens /
            position_size_pct / cache_path.
        config: `BacktestConfig` as a dict. Required keys: symbols
            (list), start (ISO 8601), end (ISO 8601), interval
            ('1d' or '1m'), starting_cash, fees_model, slippage_model.
            See `BacktestConfig` Pydantic for the full schema.
        write_to_registry: If True (default), persist the result to
            `agent_runs`. Set False for dry-runs an agent is
            iterating on rapidly.

    Returns:
        `RunMetrics` — total_return, sharpe, max_drawdown, win_rate,
        n_trades, etc. To inspect trades + equity curve in detail,
        look up the run in `list_strategy_runs` and pull
        `metrics_full` JSON from the row (or run the CLI directly).

    Cost shape: depends on strategy. SMA crossover ≈ free (pure CH
    or bronze reads). LLM agent ≈ ~$0.001-0.003 per actionable bar
    on first run; subsequent runs with the same config = $0 (cache
    hit).
    """
    from app.services.sim.registry import write_run

    with tool_call("run_backtest", strategy=strategy_name):
        cfg = BacktestConfig.model_validate(config)
        strategy = _instantiate(strategy_name, strategy_params, interval=cfg.interval)
        run = Backtester().run(strategy, cfg)
        if write_to_registry:
            write_run(run)
        return run.metrics


@mcp.tool()
def list_strategy_runs(
    strategy_name: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent backtest runs from the `agent_runs` registry.

    USE WHEN: an agent is iterating — "how have my last 20 attempts
    performed", "is the new prompt better than the old one?"
    Newest first.

    Args:
        strategy_name: Filter to one strategy ('sma_crossover',
            'llm_agent'). Omit to see all.
        limit: Max rows. Default 20; max 200.

    Returns:
        List of dicts with the slim metadata view: run_id,
        started_at, strategy_name, strategy_version, interval,
        start_date, end_date, n_trades, total_return,
        sharpe_ratio, max_drawdown, final_equity. For the full
        result, pull `metrics_full` directly from CH.
    """
    if limit > 200:
        limit = 200
    with tool_call("list_strategy_runs", strategy=strategy_name, limit=limit):
        rows = _list_runs(strategy_name=strategy_name, limit=limit)
        # Coerce datetime / UUID for JSON serializability.
        out: list[dict[str, Any]] = []
        for r in rows:
            slim = dict(r)
            for k, v in slim.items():
                if hasattr(v, "isoformat"):
                    slim[k] = v.isoformat()
                else:
                    slim[k] = str(v) if k == "run_id" else v
            out.append(slim)
        return out


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _instantiate(name: str, params: dict[str, Any], *, interval: str):
    """
    Build a strategy instance by name. Mirrors the CLI's loader.
    Adding a new strategy = add a branch here + a branch in
    `scripts/run_backtest.py::_load_strategy`. Keeping both maps
    explicit (vs auto-discovery) makes the supported surface
    grep-able in one place.
    """
    if name == "sma_crossover":
        from app.services.sim.strategies.sma_crossover import (
            SmaCrossoverParams,
            SmaCrossoverStrategy,
        )
        return SmaCrossoverStrategy(
            params=SmaCrossoverParams(**params), interval=interval,
        )
    if name == "llm_agent":
        from app.services.sim.strategies.llm_agent import (
            LLMAgentParams,
            LLMAgentStrategy,
        )
        return LLMAgentStrategy(
            params=LLMAgentParams(**params), interval=interval,
        )
    if name == "rsi_reversion":
        from app.services.sim.strategies.rsi_reversion import (
            RsiReversionParams,
            RsiReversionStrategy,
        )
        return RsiReversionStrategy(
            params=RsiReversionParams(**params), interval=interval,
        )
    if name == "bollinger_mean_revert":
        from app.services.sim.strategies.bollinger_mean_revert import (
            BollingerMeanRevertParams,
            BollingerMeanRevertStrategy,
        )
        return BollingerMeanRevertStrategy(
            params=BollingerMeanRevertParams(**params), interval=interval,
        )
    if name == "ema_crossover":
        from app.services.sim.strategies.ema_crossover import (
            EmaCrossoverParams,
            EmaCrossoverStrategy,
        )
        return EmaCrossoverStrategy(
            params=EmaCrossoverParams(**params), interval=interval,
        )
    raise ValueError(
        f"Unknown strategy {name!r}. Supported: 'sma_crossover', "
        "'ema_crossover', 'llm_agent', 'rsi_reversion', 'bollinger_mean_revert'."
    )
