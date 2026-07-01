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
        if config.benchmark:
            ctx.market = self._load_benchmark(config, exec_interval)
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

    def run_portfolio(self, strategy: Strategy, config: BacktestConfig) -> RunResult:
        """
        Multi-symbol PORTFOLIO backtest: all symbols share one cash pool, one
        equity curve, and a RiskManager that caps concurrent positions and total
        open risk (portfolio heat). Bars are time-synchronized across symbols, so
        positions are genuinely concurrent (unlike `run`, which walks each symbol's
        full timeline in isolation). Single execution interval (no multi-TF yet).

        The honest equity curve / drawdown this produces is what makes conviction
        sizing safe to evaluate — and what a customer-facing track record needs.
        """
        from app.services.sim.risk import RiskManager

        started_at = datetime.now(timezone.utc)
        exec_interval = config.interval
        if strategy.interval != exec_interval:
            raise ValueError(
                f"Strategy interval {strategy.interval!r} != config interval {exec_interval!r}."
            )
        fees = make_fees(config.fees_model, config.fees_params)
        slippage = make_slippage(config.slippage_model, config.slippage_params)
        snapshot_id = self._capture_snapshot(config, exec_interval)

        bars_by_symbol = self._fetch_bars_multi(config, [exec_interval])[exec_interval]
        portfolio = Portfolio(starting_cash=config.starting_cash)
        risk = RiskManager(
            max_concurrent=config.max_concurrent_positions,
            max_portfolio_heat=config.max_portfolio_heat,
        )

        # One Context per symbol (isolated history/indicators); shared benchmark.
        market = self._load_benchmark(config, exec_interval) if config.benchmark else None
        ctx_by_symbol: dict[str, Context] = {}
        for sym in config.symbols:
            ctx = Context(config=config, intervals=[exec_interval])
            ctx.market = market
            ctx_by_symbol[sym] = ctx
        # setup() runs once (strategy state is per-symbol-keyed internally).
        if config.symbols:
            strategy.setup(ctx_by_symbol[config.symbols[0]])

        # Merge all symbols' bars into one ascending timeline.
        timeline = sorted({b.timestamp for bars in bars_by_symbol.values() for b in bars})
        cursors = {sym: 0 for sym in config.symbols}
        last_close: dict[str, float] = {}
        # Dynamic-universe state: per-symbol close history for as-of momentum ranking.
        gate_long, gate_short = config.momentum_top_n, config.momentum_bottom_n
        mom_lb = config.momentum_lookback
        price_hist: dict[str, list[float]] = {}

        for t in timeline:
            # Pass 1: advance cursors, update close history, compute as-of momentum.
            present: list = []  # (sym, bar, next_bar)
            momentum: dict[str, float] = {}
            for sym in config.symbols:
                bars = bars_by_symbol.get(sym, [])
                cur = cursors[sym]
                if cur >= len(bars) or bars[cur].timestamp != t:
                    continue
                bar = bars[cur]
                next_bar = bars[cur + 1] if cur + 1 < len(bars) else None
                cursors[sym] = cur + 1
                last_close[sym] = bar.close
                ph = price_hist.setdefault(sym, [])
                ph.append(bar.close)
                if len(ph) > mom_lb and ph[-mom_lb - 1] > 0:
                    momentum[sym] = bar.close / ph[-mom_lb - 1] - 1.0
                present.append((sym, bar, next_bar))

            # Eligibility sets from the cross-sectional momentum ranking (as-of t).
            eligible_long = eligible_short = None
            if (gate_long or gate_short) and momentum:
                ranked = sorted(momentum, key=momentum.__getitem__, reverse=True)
                if gate_long:
                    eligible_long = set(ranked[:gate_long])
                if gate_short:
                    eligible_short = set(ranked[-gate_short:])

            # Pass 2: signals + execution, gated by direction-eligibility.
            for sym, bar, next_bar in present:
                ctx = ctx_by_symbol[sym]
                ctx.advance(bar, portfolio.snapshot())
                action = strategy.on_bar(ctx)
                if action.kind not in ("buy", "sell"):
                    continue

                pos = portfolio.positions.get(sym)
                has_position = pos is not None and pos.quantity != 0
                if has_position:
                    # Exit / cover — always allowed; free its risk budget.
                    portfolio.apply(action, bar, next_bar, fees, slippage)
                    if sym not in portfolio.positions:
                        risk.release(sym)
                    continue

                # Entry: dynamic-universe gate — long only in leaders, short only
                # in laggards (when the respective gate is configured).
                if eligible_long is not None and action.kind == "buy" and sym not in eligible_long:
                    continue
                if eligible_short is not None and action.kind == "sell" and sym not in eligible_short:
                    continue
                # Risk gate: portfolio heat + concurrent caps.
                stop = action.stop_price if action.stop_price is not None else bar.close
                risk_amount = action.size * abs(bar.close - stop)
                if risk.can_open(sym, risk_amount, portfolio.snapshot().equity):
                    trade = portfolio.apply(action, bar, next_bar, fees, slippage)
                    if trade is not None and sym in portfolio.positions:
                        risk.register(sym, risk_amount)

            portfolio.mark_portfolio(t, dict(last_close))

        strategy.teardown(ctx_by_symbol[config.symbols[0]]) if config.symbols else None
        metrics = self._evaluator.compute(portfolio, config)
        open_positions = [p for p in portfolio.positions.values() if p.quantity != 0]
        return RunResult(
            started_at=started_at, finished_at=datetime.now(timezone.utc),
            strategy_name=strategy.name, strategy_version=strategy.version,
            strategy_params=self._extract_params(strategy), config=config,
            snapshot_id=snapshot_id, git_sha=_git_sha(), metrics=metrics,
            equity_curve=list(portfolio.equity_curve), trades=list(portfolio.closed_trades),
            open_positions=open_positions,
        )

    def _load_benchmark(self, config, interval: str):
        """Load the benchmark once and wrap it in a (pure) MarketContext.

        Engine-side IO (allowed here; strategies/filters stay pure and just read
        `ctx.market`). Empty/missing data degrades to an empty MarketContext so a
        market filter fails closed rather than crashing the run.
        """
        import pandas as pd

        from app.services.readers.bar_reader import BarReader
        from app.services.sim.market_context import MarketContext

        try:
            if config.daily_table and interval == "1d":
                # Read the benchmark from the SAME table as the strategy bars so
                # timestamps (tz) + adjustment convention match — otherwise the
                # MarketContext as-of lookup compares mismatched tz and crashes.
                bench_cfg = config.model_copy(update={"symbols": [config.benchmark]})
                bars = self._fetch_bars_daily_table(bench_cfg, config.daily_table).get(
                    config.benchmark, [])
            else:
                bars = BarReader.from_settings().get_bars_in_range(
                    config.benchmark, config.start, config.end, interval=interval,
                )
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the backtest
            logger.warning("Backtester: benchmark %s load failed: %s", config.benchmark, exc)
            bars = []
        if not bars:
            logger.warning("Backtester: no benchmark bars for %s", config.benchmark)
            return MarketContext(config.benchmark, pd.Series(dtype=float))
        close = pd.Series(
            [b.close for b in bars],
            index=pd.DatetimeIndex([b.timestamp for b in bars]),
        )
        return MarketContext(config.benchmark, close)

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

    def _fetch_bars_daily_table(self, config: BacktestConfig, table: str) -> dict[str, list[Bar]]:
        """Read pre-adjusted daily bars directly from a CH table (the research
        universe, e.g. ohlcv_daily). Bar is a structural Protocol, so a tiny
        record with the OHLCV fields satisfies the rest of the engine."""
        import re
        from dataclasses import dataclass
        from app.db.client import get_client

        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise ValueError(f"unsafe daily_table name {table!r}")

        @dataclass
        class _DailyBar:
            symbol: str
            timestamp: datetime
            open: float
            high: float
            low: float
            close: float
            volume: float

        cli = get_client()
        a = config.start.strftime("%Y-%m-%d %H:%M:%S")
        b = config.end.strftime("%Y-%m-%d %H:%M:%S")
        out: dict[str, list[Bar]] = {sym: [] for sym in config.symbols}
        # ONE query for the whole universe (500 per-symbol FINAL queries were slow).
        rows = cli.query(
            f"SELECT symbol, timestamp, open, high, low, close, volume FROM {table} FINAL "
            "WHERE symbol IN {syms:Array(String)} "
            "AND timestamp >= {a:String} AND timestamp <= {b:String} "
            "ORDER BY symbol, timestamp",
            parameters={"syms": list(config.symbols), "a": a, "b": b},
        ).result_rows
        for r in rows:
            sym = r[0]
            if sym in out:
                out[sym].append(_DailyBar(sym, r[1], float(r[2]), float(r[3]),
                                          float(r[4]), float(r[5]), float(r[6])))
        return out

    def _fetch_bars_ch(
        self, config: BacktestConfig, interval: str,
    ) -> dict[str, list[Bar]]:
        """All non-1m intervals route through CH via BarReader (rollup of ohlcv_1m),
        unless a `daily_table` is configured for 1d (direct pre-adjusted read)."""
        if interval == "1d" and config.daily_table:
            return self._fetch_bars_daily_table(config, config.daily_table)
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
