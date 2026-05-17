"""
Backtester — the orchestrator for one trading-strategy run.

Owns the iteration loop:

    for each execution bar:
        # 1. Advance any newly-ready coarser-interval bars (multi-TF).
        for interval in coarser_intervals:
            while next_coarser_bar.ready_time <= execution_bar.timestamp:
                ctx.advance_coarser(interval, next_coarser_bar)
        # 2. Advance execution interval.
        ctx.advance(execution_bar, portfolio.snapshot())
        # 3. Strategy decides.
        action = strategy.on_bar(ctx)
        # 4. Harness executes the action.
        portfolio.apply(action, execution_bar, next_execution_bar, fees, slippage)
        portfolio.mark_to_market(execution_bar)

Bar source resolution by interval (TA-1 + TA-4):
  - "1m"  -> `BronzeReader.get_bars(...)` (Iceberg, CH-independent;
             snapshot_id pinned for reproducibility)
  - "1d" / "5m" / "15m" / "30m" / "1h" / "4h"
          -> `BarReader.get_bars_in_range(..., interval=...)`
             (ClickHouse — `ohlcv_daily` or resampled from
             `ohlcv_1m`/`ohlcv_5m`. snapshot_id is None on the CH
             path; daily bronze table is a Phase 1 deferred item.)

Multi-symbol is TA-3+. For TA-1/TA-4 the Backtester runs ONE symbol
per call (it loops over `config.symbols`, but each iteration is
independent — no portfolio sharing across symbols).
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.services.sim.context import Context
from app.services.sim.evaluator import Evaluator, StandardEvaluator
from app.services.sim.fees import (
    FeeModel,
    SlippageModel,
    make_fees,
    make_slippage,
)
from app.services.sim.intervals import (
    execution_interval as _execution_interval,
    interval_duration,
)
from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import (
    Bar,
    BacktestConfig,
    RunMetrics,
    RunResult,
)
from app.services.sim.strategy import Strategy, required_intervals

logger = logging.getLogger(__name__)


def _git_sha() -> str:
    """Capture the current git SHA for the RunResult. Empty on failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 — best-effort
        return ""


class Backtester:
    """
    Orchestrates one backtest run. Stateless across runs.

    Custom fees, slippage, and evaluator are accepted via constructor
    injection. Multi-timeframe support is engaged whenever a strategy
    declares `intervals` (a list) — single-TF strategies that only
    declare `interval` still work unchanged.
    """

    def __init__(
        self,
        *,
        evaluator: Optional[Evaluator] = None,
    ) -> None:
        self._evaluator = evaluator or StandardEvaluator()

    def run(self, strategy: Strategy, config: BacktestConfig) -> RunResult:
        """
        Run `strategy` against `config`. Returns a fully-populated
        RunResult including the equity curve, trades, and metrics.
        """
        started_at = datetime.now(timezone.utc)

        # Resolve the interval set. Config can override what the
        # strategy declared (rare but useful for sensitivity sweeps).
        if config.intervals:
            run_intervals = list(config.intervals)
        else:
            run_intervals = required_intervals(strategy)
        exec_interval = _execution_interval(run_intervals)

        # Strategy / config consistency checks — never silently
        # mismatch on interval, that's how reproducibility dies.
        if strategy.interval != exec_interval:
            raise ValueError(
                f"Strategy {strategy.name!r} declares interval={strategy.interval!r} "
                f"but execution interval resolves to {exec_interval!r} "
                f"(from intervals={run_intervals}). Strategy.interval must "
                "match the FINEST interval declared."
            )
        if config.interval != exec_interval:
            raise ValueError(
                f"Config.interval={config.interval!r} doesn't match execution "
                f"interval {exec_interval!r}. Update config.interval to "
                f"{exec_interval!r} or change the strategy's intervals."
            )

        fees = make_fees(config.fees_model, config.fees_params)
        slippage = make_slippage(config.slippage_model, config.slippage_params)

        # Snapshot pin from bronze ('1m' interval). Only the execution
        # interval matters for the agent_runs row today; future
        # work can pin per-interval if it becomes valuable.
        snapshot_id = self._capture_snapshot(config, exec_interval)

        # Fetch bars at every required interval per symbol.
        bars_by_symbol_by_interval = self._fetch_bars_multi(config, run_intervals)
        if not any(
            bars_by_symbol_by_interval.get(exec_interval, {}).values()
        ):
            logger.warning(
                "Backtester.run: no execution-interval bars for %s in [%s..%s]",
                config.symbols, config.start, config.end,
            )

        portfolio = Portfolio(starting_cash=config.starting_cash)
        ctx = Context(config=config, intervals=run_intervals)
        strategy.setup(ctx)

        for symbol in config.symbols:
            self._run_one_symbol(
                strategy=strategy, ctx=ctx, portfolio=portfolio,
                exec_bars=bars_by_symbol_by_interval[exec_interval].get(symbol, []),
                coarser_bars={
                    iv: bars_by_symbol_by_interval[iv].get(symbol, [])
                    for iv in run_intervals if iv != exec_interval
                },
                fees=fees, slippage=slippage,
            )

        strategy.teardown(ctx)

        metrics = self._evaluator.compute(portfolio, config)
        finished_at = datetime.now(timezone.utc)

        return RunResult(
            started_at=started_at,
            finished_at=finished_at,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            strategy_params=self._extract_params(strategy),
            config=config,
            snapshot_id=snapshot_id,
            git_sha=_git_sha(),
            metrics=metrics,
            equity_curve=list(portfolio.equity_curve),
            trades=list(portfolio.closed_trades),
        )

    # ─────────────────────────────────────────────────────────────────
    # Iteration
    # ─────────────────────────────────────────────────────────────────

    def _run_one_symbol(
        self,
        *,
        strategy: Strategy,
        ctx: Context,
        portfolio: Portfolio,
        exec_bars: Sequence[Bar],
        coarser_bars: dict[str, Sequence[Bar]],
        fees: FeeModel,
        slippage: SlippageModel,
    ) -> None:
        """
        Inner iteration loop for one symbol's bars. Multi-TF safe:
        if `coarser_bars` is non-empty, those bars are released to the
        Context only when their ready_time has passed.
        """
        if not exec_bars:
            return

        # Pre-compute ready_time per coarser bar so the per-step
        # exposure check is O(1). For each interval we walk forward
        # through its bar list (sorted by timestamp); we maintain a
        # "next index to release" cursor.
        coarser_cursors: dict[str, int] = {iv: 0 for iv in coarser_bars}
        coarser_durations = {iv: interval_duration(iv) for iv in coarser_bars}

        for i, bar in enumerate(exec_bars):
            next_bar: Optional[Bar] = exec_bars[i + 1] if i + 1 < len(exec_bars) else None

            # 1. Release any coarser bars whose ready_time has passed.
            for iv, iv_bars in coarser_bars.items():
                cursor = coarser_cursors[iv]
                duration = coarser_durations[iv]
                while cursor < len(iv_bars):
                    coarser_bar = iv_bars[cursor]
                    ready_time = coarser_bar.timestamp + duration
                    if ready_time > bar.timestamp:
                        break
                    ctx.advance_coarser(iv, coarser_bar)
                    cursor += 1
                coarser_cursors[iv] = cursor

            # 2. Advance execution.
            ctx.advance(bar, portfolio.snapshot())

            # 3. Strategy decides.
            action = strategy.on_bar(ctx)
            if action.kind != "hold":
                portfolio.apply(action, bar, next_bar, fees, slippage)
            portfolio.mark_to_market(bar)

    # ─────────────────────────────────────────────────────────────────
    # Bar fetching (multi-interval)
    # ─────────────────────────────────────────────────────────────────

    def _fetch_bars_multi(
        self,
        config: BacktestConfig,
        intervals: list[str],
    ) -> dict[str, dict[str, list[Bar]]]:
        """
        Returns `{interval: {symbol: [bars...]}}`.

        Each interval is resolved to its native bar source:
          - '1m' -> BronzeReader
          - others -> BarReader (ohlcv_daily for 1d, resampled for the rest)
        """
        out: dict[str, dict[str, list[Bar]]] = {}
        for interval in intervals:
            if interval == "1m":
                out[interval] = self._fetch_bars_bronze(config)
            else:
                out[interval] = self._fetch_bars_ch(config, interval)
        return out

    def _fetch_bars_bronze(self, config: BacktestConfig) -> dict[str, list[Bar]]:
        from app.services.readers.bronze_reader import BronzeReader

        reader = BronzeReader.from_settings()
        out: dict[str, list[Bar]] = {}
        for symbol in config.symbols:
            bars = reader.get_bars(
                symbol, config.start, config.end, provider=config.provider,
            )
            out[symbol] = list(bars)
            logger.info(
                "Backtester: fetched %d bronze 1m bars for %s [%s..%s]",
                len(bars), symbol, config.start, config.end,
            )
        return out

    def _fetch_bars_ch(
        self, config: BacktestConfig, interval: str,
    ) -> dict[str, list[Bar]]:
        """All non-1m intervals route through CH via BarReader."""
        from app.services.readers.bar_reader import BarReader

        reader = BarReader.from_settings()
        out: dict[str, list[Bar]] = {}
        for symbol in config.symbols:
            bars = reader.get_bars_in_range(
                symbol, config.start, config.end, interval=interval,
                limit=200_000,
            )
            out[symbol] = list(bars)
            logger.info(
                "Backtester: fetched %d CH %s bars for %s [%s..%s]",
                len(bars), interval, symbol, config.start, config.end,
            )
        return out

    def _capture_snapshot(
        self, config: BacktestConfig, exec_interval: str,
    ) -> Optional[str]:
        """Pin the Iceberg snapshot_id for reproducibility. None on CH-only paths."""
        if exec_interval != "1m":
            return None
        try:
            from app.config import settings
            from app.services.iceberg_catalog import get_catalog

            table_id = f"{settings.iceberg_glue_database}.{config.provider}_minute"
            t = get_catalog().load_table(table_id)
            snap = t.current_snapshot()
            return str(snap.snapshot_id) if snap else None
        except Exception as exc:  # noqa: BLE001 — non-fatal
            logger.warning("Backtester: snapshot capture failed: %s", exc)
            return None

    @staticmethod
    def _extract_params(strategy: Strategy) -> dict:
        """Strategy params for the agent_runs row."""
        getter = getattr(strategy, "params_dict", None)
        if callable(getter):
            return getter()
        return {}
