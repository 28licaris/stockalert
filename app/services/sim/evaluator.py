"""
Evaluator — produces `RunMetrics` from a Portfolio's final state.

Default `StandardEvaluator` computes the canonical metric set:
total return, annualized return, Sharpe, Sortino, max drawdown,
win rate, profit factor, per-trade aggregates. Each metric guards
against the degenerate-input case (zero trades, zero stddev, etc.)
so degenerate strategies don't crash the evaluator.

Custom evaluators implement the `Evaluator` Protocol and pass to
the Backtester per run.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Protocol

import numpy as np

from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import BacktestConfig, RunMetrics, Trade


class Evaluator(Protocol):
    """Strategy-agnostic post-run metrics."""

    def compute(self, portfolio: Portfolio, config: BacktestConfig) -> RunMetrics: ...


# Trading-day approximations for annualizing returns + Sharpe.
# Conservative — US markets trade ~252 days/year.
_TRADING_DAYS_PER_YEAR = 252


class StandardEvaluator:
    """
    Canonical performance metrics. Bar-frequency aware: Sharpe is
    annualized using the appropriate bars-per-year factor for the
    config's interval.
    """

    def compute(self, portfolio: Portfolio, config: BacktestConfig) -> RunMetrics:
        trades = portfolio.closed_trades
        equity_curve = portfolio.equity_curve

        starting = portfolio.starting_cash
        final_equity = equity_curve[-1][1] if equity_curve else starting
        total_return = (final_equity / starting) - 1.0 if starting else 0.0

        annualized = self._annualized_return(equity_curve, config.interval)
        sharpe = self._sharpe(equity_curve, config.interval)
        sortino = self._sortino(equity_curve, config.interval)
        max_dd, longest_dd_days = self._max_drawdown(equity_curve)
        win_rate, profit_factor, avg_t, avg_w, avg_l = self._trade_stats(trades)

        return RunMetrics(
            total_return=float(total_return),
            annualized_return=annualized,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=float(max_dd),
            longest_drawdown_days=int(longest_dd_days),
            win_rate=win_rate,
            profit_factor=profit_factor,
            n_trades=len(trades),
            avg_trade_pnl=avg_t,
            avg_winner_pnl=avg_w,
            avg_loser_pnl=avg_l,
            final_equity=float(final_equity),
        )

    # ─────────────────────────────────────────────────────────────────
    # Metric computations
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _bars_per_year(interval: str) -> float:
        """Approximate bars per year — used to annualize bar-level returns."""
        # ~6.5h regular session × ~252 trading days = ~1,638h
        return {
            "1d": float(_TRADING_DAYS_PER_YEAR),
            "1h": float(_TRADING_DAYS_PER_YEAR * 6.5),
            "30m": float(_TRADING_DAYS_PER_YEAR * 13),
            "15m": float(_TRADING_DAYS_PER_YEAR * 26),
            "5m":  float(_TRADING_DAYS_PER_YEAR * 78),
            "1m":  float(_TRADING_DAYS_PER_YEAR * 390),
        }.get(interval, float(_TRADING_DAYS_PER_YEAR))

    @classmethod
    def _bar_returns(cls, equity_curve: list[tuple[datetime, float]]) -> np.ndarray:
        if len(equity_curve) < 2:
            return np.array([], dtype=float)
        values = np.array([e for _, e in equity_curve], dtype=float)
        # Per-bar simple returns. Guards against zero entries.
        prev = values[:-1]
        curr = values[1:]
        with np.errstate(divide="ignore", invalid="ignore"):
            rets = np.where(prev > 0, (curr - prev) / prev, 0.0)
        return rets

    @classmethod
    def _annualized_return(
        cls, equity_curve: list[tuple[datetime, float]], interval: str,
    ) -> float | None:
        if len(equity_curve) < 2:
            return None
        starting = equity_curve[0][1]
        final = equity_curve[-1][1]
        if starting <= 0:
            return None
        n_bars = len(equity_curve) - 1
        bars_per_year = cls._bars_per_year(interval)
        years = n_bars / bars_per_year
        if years <= 0:
            return None
        return float((final / starting) ** (1 / years) - 1)

    @classmethod
    def _sharpe(
        cls, equity_curve: list[tuple[datetime, float]], interval: str,
    ) -> float | None:
        rets = cls._bar_returns(equity_curve)
        if rets.size < 2:
            return None
        mean = rets.mean()
        std = rets.std(ddof=1)
        if std == 0 or not math.isfinite(std):
            return None
        bars_per_year = cls._bars_per_year(interval)
        return float((mean / std) * math.sqrt(bars_per_year))

    @classmethod
    def _sortino(
        cls, equity_curve: list[tuple[datetime, float]], interval: str,
    ) -> float | None:
        rets = cls._bar_returns(equity_curve)
        if rets.size < 2:
            return None
        downside = rets[rets < 0]
        if downside.size < 2:
            return None
        mean = rets.mean()
        downside_std = downside.std(ddof=1)
        if downside_std == 0 or not math.isfinite(downside_std):
            return None
        bars_per_year = cls._bars_per_year(interval)
        return float((mean / downside_std) * math.sqrt(bars_per_year))

    @staticmethod
    def _max_drawdown(
        equity_curve: list[tuple[datetime, float]],
    ) -> tuple[float, int]:
        """
        Peak-to-trough max drawdown (negative number) + longest
        drawdown duration in days.
        """
        if len(equity_curve) < 2:
            return (0.0, 0)
        values = np.array([e for _, e in equity_curve], dtype=float)
        running_peak = np.maximum.accumulate(values)
        dd = (values - running_peak) / running_peak
        max_dd = float(dd.min()) if dd.size else 0.0

        # Longest drawdown: longest contiguous run where values < running_peak
        peak_times = [equity_curve[0][0]]
        longest = 0
        cur_peak_ts = equity_curve[0][0]
        last_peak_value = values[0]
        for (ts, v), peak in zip(equity_curve, running_peak):
            if v >= peak:
                cur_peak_ts = ts
                last_peak_value = v
            else:
                span = (ts - cur_peak_ts).days
                if span > longest:
                    longest = span
        return (max_dd, longest)

    @staticmethod
    def _trade_stats(trades: list[Trade]) -> tuple[float | None, float | None, float | None, float | None, float | None]:
        """Returns (win_rate, profit_factor, avg_trade, avg_winner, avg_loser)."""
        # Only count closing sells in win/loss aggregates (entries have realized_pnl=0).
        closing_pnls = [t.realized_pnl for t in trades if t.side == "sell"]
        if not closing_pnls:
            return (None, None, None, None, None)
        wins = [p for p in closing_pnls if p > 0]
        losses = [p for p in closing_pnls if p < 0]
        avg_trade = float(np.mean(closing_pnls))
        avg_winner = float(np.mean(wins)) if wins else None
        avg_loser = float(np.mean(losses)) if losses else None
        win_rate = float(len(wins) / len(closing_pnls))
        if losses:
            profit_factor = float(sum(wins) / abs(sum(losses)))
        else:
            profit_factor = None  # division by zero — guard with None
        return (win_rate, profit_factor, avg_trade, avg_winner, avg_loser)
