"""
Backtester — the orchestrator for one trading-strategy run.

Owns the iteration loop:

    for each bar:
        ctx.advance(bar, portfolio.snapshot())
        action = strategy.on_bar(ctx)
        portfolio.apply(action, current_bar, next_bar, fees, slippage)
        portfolio.mark_to_market(current_bar)

Bar source resolution by interval (TA-1):
  - "1m"  -> `BronzeReader.get_bars(...)` (Iceberg, CH-independent;
             snapshot_id pinned for reproducibility)
  - "1d"  -> `BarReader.get_bars_in_range(..., interval="1d")`
             (ClickHouse `ohlcv_daily`; snapshot_id is None because
             CH has no snapshots — reproducibility is weaker on this
             path. Suitable for research; production training should
             prefer bronze + a daily resampler when that lands.)
  - Other intervals: NotImplementedError. TA-3+ adds a resampling
    layer for 5m/15m/30m/1h/4h from minute bronze.

Multi-symbol is TA-3+. For TA-1 the Backtester runs ONE symbol per
call (it loops over `config.symbols`, but each iteration is
independent — no portfolio sharing across symbols). The canary
(SMA crossover) only uses one symbol, so this matches the gate test.
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
from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import (
    Bar,
    BacktestConfig,
    RunMetrics,
    RunResult,
)
from app.services.sim.strategy import Strategy

logger = logging.getLogger(__name__)


def _git_sha() -> str:
    """Capture the current git SHA for the RunResult. Empty on failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 — best-effort, never blocks a run
        return ""


class Backtester:
    """
    Orchestrates one backtest run. Stateless across runs — each
    `run()` call gets its own Portfolio + Context + Bar source.

    Custom bar sources, fees, slippage, and evaluator are accepted
    via constructor injection — the Backtester doesn't care which
    concrete implementations are passed in.
    """

    def __init__(
        self,
        *,
        evaluator: Optional[Evaluator] = None,
    ) -> None:
        self._evaluator = evaluator or StandardEvaluator()

    def run(
        self,
        strategy: Strategy,
        config: BacktestConfig,
    ) -> RunResult:
        """
        Run `strategy` against `config`. Returns a fully-populated
        RunResult including the equity curve, trades, and metrics.

        Multi-symbol semantics: bars from each symbol are concatenated
        in symbol-then-time order. The portfolio is **shared across
        symbols** within one run — strategies that should NOT mix
        symbols' capital must request `len(config.symbols) == 1`.
        """
        started_at = datetime.now(timezone.utc)

        # Validate strategy / config interval compatibility.
        if strategy.interval != config.interval:
            raise ValueError(
                f"Strategy {strategy.name!r} requires interval={strategy.interval!r} "
                f"but config requested {config.interval!r}. Update the strategy or "
                "the config — never silently downsample."
            )

        # Fees + slippage from config — pluggable, name-resolved.
        fees = make_fees(config.fees_model, config.fees_params)
        slippage = make_slippage(config.slippage_model, config.slippage_params)

        # Snapshot pinning for reproducibility (bronze path).
        snapshot_id = self._capture_snapshot(config)

        # Fetch bars for all symbols.
        bars_by_symbol = self._fetch_bars(config)
        if not any(bars_by_symbol.values()):
            logger.warning(
                "Backtester.run: no bars returned for %s in [%s..%s]",
                config.symbols, config.start, config.end,
            )

        portfolio = Portfolio(starting_cash=config.starting_cash)
        ctx = Context(config=config)
        strategy.setup(ctx)

        for symbol, bars in bars_by_symbol.items():
            self._run_one_symbol(
                strategy=strategy, ctx=ctx, portfolio=portfolio,
                bars=bars, fees=fees, slippage=slippage,
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
    # Internals
    # ─────────────────────────────────────────────────────────────────

    def _run_one_symbol(
        self,
        *,
        strategy: Strategy,
        ctx: Context,
        portfolio: Portfolio,
        bars: Sequence[Bar],
        fees: FeeModel,
        slippage: SlippageModel,
    ) -> None:
        """Inner iteration loop for one symbol's bars."""
        if not bars:
            return
        # Iterate with lookahead-of-one for fills on next bar's open.
        for i, bar in enumerate(bars):
            next_bar: Optional[Bar] = bars[i + 1] if i + 1 < len(bars) else None
            ctx.advance(bar, portfolio.snapshot())
            action = strategy.on_bar(ctx)
            if action.kind != "hold":
                portfolio.apply(action, bar, next_bar, fees, slippage)
            portfolio.mark_to_market(bar)

    def _fetch_bars(self, config: BacktestConfig) -> dict[str, list[Bar]]:
        """Resolve bar source by interval and pull bars for each symbol."""
        if config.interval == "1m":
            return self._fetch_bars_bronze(config)
        if config.interval == "1d":
            return self._fetch_bars_ch_daily(config)
        raise NotImplementedError(
            f"Backtester does not support interval={config.interval!r} yet. "
            "Supported in TA-1: '1m' (bronze) and '1d' (CH ohlcv_daily). "
            "TA-3+ adds 5m/15m/30m/1h/4h via a resampling layer."
        )

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

    def _fetch_bars_ch_daily(self, config: BacktestConfig) -> dict[str, list[Bar]]:
        from app.services.readers.bar_reader import BarReader

        reader = BarReader.from_settings()
        out: dict[str, list[Bar]] = {}
        for symbol in config.symbols:
            bars = reader.get_bars_in_range(
                symbol, config.start, config.end, interval="1d",
                limit=100_000,
            )
            out[symbol] = list(bars)
            logger.info(
                "Backtester: fetched %d CH daily bars for %s [%s..%s]",
                len(bars), symbol, config.start, config.end,
            )
        return out

    def _capture_snapshot(self, config: BacktestConfig) -> Optional[str]:
        """Pin the Iceberg snapshot_id for reproducibility. None for CH paths."""
        if config.interval != "1m":
            return None  # CH has no snapshots
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
