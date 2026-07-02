"""
Fee + slippage models.

Both are Protocols with multiple default implementations. The
backtester accepts them per-run so different cost assumptions can be
swapped (e.g. zero fees for theoretical-edge measurement, realistic
fees for production-ready evaluation) without recompiling strategies.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.services.sim.schemas import Action, Bar


# ─────────────────────────────────────────────────────────────────────
# Protocols
# ─────────────────────────────────────────────────────────────────────


class FeeModel(Protocol):
    """Computes the dollar fee for a single fill."""

    def fee_for(self, action: Action, fill_price: float) -> float: ...


class SlippageModel(Protocol):
    """Computes the actual fill price given an intended action + a bar."""

    def fill_price(self, action: Action, next_bar: Bar | None) -> float: ...


# ─────────────────────────────────────────────────────────────────────
# Fee implementations
# ─────────────────────────────────────────────────────────────────────


class ZeroFees:
    """No fees — useful for theoretical-edge measurement."""

    def fee_for(self, action: Action, fill_price: float) -> float:
        return 0.0


class PerShareFees:
    """
    Flat per-share commission with a minimum + maximum. Matches the
    Interactive Brokers tiered/pro structure and is a reasonable
    Schwab approximation.
    """

    def __init__(
        self,
        per_share: float = 0.005,
        min_commission: float = 1.00,
        max_commission_pct: float = 0.01,
    ) -> None:
        self.per_share = per_share
        self.min_commission = min_commission
        self.max_commission_pct = max_commission_pct

    def fee_for(self, action: Action, fill_price: float) -> float:
        if action.kind in ("hold", "set_position"):
            # set_position is decomposed into a buy/sell by the
            # portfolio before fees apply, so we shouldn't see one here.
            return 0.0
        qty = abs(action.size)
        if qty <= 0:
            return 0.0
        raw = qty * self.per_share
        max_cap = qty * fill_price * self.max_commission_pct
        return float(min(max(raw, self.min_commission), max_cap))


class PercentFees:
    """
    Percentage-of-notional commission. Useful as a quick-and-dirty
    model when bid/ask + commission together are best approximated
    as one round-trip cost.
    """

    def __init__(self, pct: float = 0.001) -> None:
        if pct < 0:
            raise ValueError("PercentFees pct must be >= 0")
        self.pct = pct

    def fee_for(self, action: Action, fill_price: float) -> float:
        if action.kind in ("hold", "set_position"):
            return 0.0
        return float(abs(action.size) * fill_price * self.pct)


# ─────────────────────────────────────────────────────────────────────
# Slippage implementations
# ─────────────────────────────────────────────────────────────────────


class NextBarOpenFill:
    """
    Fill on the next bar's open. No slippage adjustment.

    This is the **default and recommended** model for backtesting —
    it prevents look-ahead bias (the strategy decides on bar N's
    close; the fill happens at N+1's open, which the strategy hasn't
    seen yet).

    If `next_bar is None` (end of data), returns the current bar's
    close as a fallback — the trade effectively didn't execute. The
    backtester drops these to keep accounting clean.
    """

    def fill_price(self, action: Action, next_bar: Bar | None) -> float:
        if next_bar is None:
            return float("nan")
        return float(next_bar.open)

    def fill_at_level(self, action: Action, level: float) -> float:
        """Path-aware fill at a verified intra-bar level. No adjustment."""
        return float(level)


class PercentSlippage:
    """
    Fill at next bar's open ± a fixed percentage that hurts the
    trader (worse fill on entry AND exit). Approximates bid/ask cross
    + temporary impact.
    """

    def __init__(self, pct: float = 0.0005) -> None:
        if pct < 0:
            raise ValueError("PercentSlippage pct must be >= 0")
        self.pct = pct

    def fill_price(self, action: Action, next_bar: Bar | None) -> float:
        if next_bar is None:
            return float("nan")
        base = float(next_bar.open)
        if action.kind == "buy":
            return base * (1.0 + self.pct)
        if action.kind == "sell":
            return base * (1.0 - self.pct)
        return base

    def fill_at_level(self, action: Action, level: float) -> float:
        """Path-aware fill at a verified intra-bar level, pct against the trader."""
        base = float(level)
        if action.kind == "buy":
            return base * (1.0 + self.pct)
        if action.kind == "sell":
            return base * (1.0 - self.pct)
        return base


# ─────────────────────────────────────────────────────────────────────
# Factory by name (used by BacktestConfig)
# ─────────────────────────────────────────────────────────────────────


_FEE_REGISTRY: dict[str, Any] = {
    "zero": ZeroFees,
    "per_share": PerShareFees,
    "percent": PercentFees,
}

_SLIPPAGE_REGISTRY: dict[str, Any] = {
    "next_bar_open": NextBarOpenFill,
    "percent": PercentSlippage,
}


def make_fees(name: str, params: dict[str, Any] | None = None) -> FeeModel:
    cls = _FEE_REGISTRY.get(name)
    if cls is None:
        supported = ", ".join(sorted(_FEE_REGISTRY))
        raise ValueError(f"Unknown fees model {name!r}. Supported: {supported}.")
    return cls(**(params or {}))


def make_slippage(name: str, params: dict[str, Any] | None = None) -> SlippageModel:
    cls = _SLIPPAGE_REGISTRY.get(name)
    if cls is None:
        supported = ", ".join(sorted(_SLIPPAGE_REGISTRY))
        raise ValueError(f"Unknown slippage model {name!r}. Supported: {supported}.")
    return cls(**(params or {}))
