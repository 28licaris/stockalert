#!/usr/bin/env python3
"""
Run a backtest from a YAML config.

Usage:
    poetry run python scripts/run_backtest.py --config configs/canary.yaml
    poetry run python scripts/run_backtest.py --config configs/canary.yaml --no-write
    poetry run python scripts/run_backtest.py --config configs/canary.yaml --quiet

Output: a one-page metrics table to stdout. By default also writes
one row to ClickHouse `agent_runs` for the run registry (skip with
`--no-write`).

The CLI is intentionally thin — all logic is in the harness. New
strategies plug in by registering with the strategy loader (see
`_load_strategy`) below; new metrics, fees, slippage land in the
respective service modules.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

# Make `app` importable when run as a top-level script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from app.services.sim.backtester import Backtester  # noqa: E402
from app.services.sim.schemas import BacktestConfig, RunResult  # noqa: E402


# Strategy loader. Adding a new strategy = one line here.
# Future agent-loader will replace this with module discovery, but for
# TA-1 the explicit map is clearer and easier to grep.
def _load_strategy(name: str, params: dict[str, Any], interval: str) -> Any:
    if name == "sma_crossover":
        from app.services.sim.strategies.sma_crossover import (
            SmaCrossoverParams,
            SmaCrossoverStrategy,
        )
        return SmaCrossoverStrategy(
            params=SmaCrossoverParams(**params),
            interval=interval,
        )
    if name == "llm_agent":
        from app.services.sim.strategies.llm_agent import (
            LLMAgentParams,
            LLMAgentStrategy,
        )
        return LLMAgentStrategy(
            params=LLMAgentParams(**params),
            interval=interval,
        )
    if name == "rsi_reversion":
        from app.services.sim.strategies.rsi_reversion import (
            RsiReversionParams,
            RsiReversionStrategy,
        )
        return RsiReversionStrategy(
            params=RsiReversionParams(**params),
            interval=interval,
        )
    raise ValueError(
        f"Unknown strategy {name!r}. Register it in scripts/run_backtest.py::_load_strategy."
    )


def _print_metrics(run: RunResult) -> None:
    """Pretty-print the RunResult for human consumption."""
    m = run.metrics
    cfg = run.config
    lines = [
        "",
        "=" * 70,
        f"  Run: {run.run_id}",
        f"  Strategy: {run.strategy_name} v{run.strategy_version}",
        f"  Params:   {run.strategy_params}",
        f"  Window:   {cfg.start.date()} .. {cfg.end.date()}  ({cfg.interval}, {cfg.symbols})",
        f"  Snapshot: {run.snapshot_id or '(none — CH path)'}",
        f"  Git SHA:  {run.git_sha[:12] if run.git_sha else '(unknown)'}",
        "-" * 70,
        f"  Starting capital  ${cfg.starting_cash:>14,.2f}",
        f"  Final equity      ${m.final_equity:>14,.2f}",
        f"  Total return      {_pct(m.total_return):>15s}",
        f"  Annualized return {_pct(m.annualized_return):>15s}",
        f"  Sharpe ratio      {_opt(m.sharpe_ratio, '.3f'):>15s}",
        f"  Sortino ratio     {_opt(m.sortino_ratio, '.3f'):>15s}",
        f"  Max drawdown      {_pct(m.max_drawdown):>15s}",
        f"  Longest DD (days) {m.longest_drawdown_days:>15d}",
        f"  N trades          {m.n_trades:>15d}",
        f"  Win rate          {_pct(m.win_rate):>15s}",
        f"  Profit factor     {_opt(m.profit_factor, '.3f'):>15s}",
        f"  Avg trade PnL     {_opt(m.avg_trade_pnl, ',.2f'):>15s}",
        "=" * 70,
        "",
    ]
    print("\n".join(lines))


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _opt(x: float | None, fmt: str) -> str:
    return "n/a" if x is None else format(x, fmt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run a backtest from a YAML config.")
    p.add_argument("--config", required=True, help="Path to YAML config file.")
    p.add_argument(
        "--no-write",
        action="store_true",
        help="Skip the agent_runs CH insert. Useful for dry-runs.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the metrics-table output (still inserts to CH).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"error: config file not found: {cfg_path}", file=sys.stderr)
        return 2
    with cfg_path.open() as fh:
        raw = yaml.safe_load(fh)

    strategy_name = raw["strategy"]
    strategy_params = raw.get("strategy_params") or {}
    config = BacktestConfig.model_validate(raw["config"])
    strategy = _load_strategy(strategy_name, strategy_params, interval=config.interval)

    backtester = Backtester()
    run = backtester.run(strategy, config)

    if not args.quiet:
        _print_metrics(run)

    if not args.no_write:
        from app.services.sim.registry import write_run
        write_run(run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
