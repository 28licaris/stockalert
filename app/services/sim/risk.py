"""
RiskManager — portfolio-level gating for the multi-symbol backtest.

Conviction sizing (up to 5% per trade) across many concurrent long+short
positions can compound into an unacceptable drawdown. The RiskManager caps:
  - **max concurrent positions** — don't be in everything at once.
  - **portfolio heat** — total open risk (sum of each position's entry→stop $
    risk) ≤ `max_portfolio_heat` × equity. This is the real lever: it bounds
    how much of the book is at risk simultaneously, regardless of per-trade size.

Stateful: the backtester registers a position's risk when an entry is allowed
and releases it on exit. One position per symbol (the strategy enforces that),
so per-name capping is implicit.
"""
from __future__ import annotations


class RiskManager:
    def __init__(self, *, max_concurrent: int = 10, max_portfolio_heat: float = 0.10) -> None:
        self.max_concurrent = max_concurrent
        self.max_portfolio_heat = max_portfolio_heat
        self._open_risk: dict[str, float] = {}   # symbol → committed $ risk

    @property
    def open_count(self) -> int:
        return len(self._open_risk)

    @property
    def open_risk_total(self) -> float:
        return sum(self._open_risk.values())

    def can_open(self, symbol: str, risk_amount: float, equity: float,
                 heat_scale: float = 1.0) -> bool:
        """Allow a new position iff it fits the concurrent-count and heat caps.

        heat_scale < 1 shrinks the heat budget for this admission (drawdown
        governor); it never affects already-open risk or exits.
        """
        if symbol in self._open_risk:
            return True  # already counted (shouldn't happen — strategy is 1/symbol)
        if self.open_count >= self.max_concurrent:
            return False
        if equity <= 0:
            return False
        if (self.open_risk_total + risk_amount) > self.max_portfolio_heat * heat_scale * equity:
            return False
        return True

    def register(self, symbol: str, risk_amount: float) -> None:
        self._open_risk[symbol] = risk_amount

    def release(self, symbol: str) -> None:
        self._open_risk.pop(symbol, None)
