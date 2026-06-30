"""
Portfolio + Position accounting for backtests.

Owns the **only** mutable state the harness has during a run:
cash, open positions, the equity curve, and the trade log. Strategies
get a read-only `PortfolioSnapshot` via the Context; the backtester
applies actions to mutate this object.

Accounting rules:
  - LONG-only for Phase TA-1 (short selling lands in TA-5+).
  - Fills happen on the NEXT bar's open by default (see fees.NextBarOpenFill).
  - Mark-to-market on every bar's close. Equity curve is the
    timestamped portfolio value at each bar.
  - Insufficient cash on a buy → action is downsized to what cash
    allows. Negative cash never occurs.
  - Sell larger than position → action is clamped to position size.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Optional

from app.services.sim.fees import FeeModel, SlippageModel
from app.services.sim.schemas import (
    Action,
    Bar,
    PortfolioSnapshot,
    Position,
    Trade,
)

logger = logging.getLogger(__name__)


class Portfolio:
    """
    Backtest portfolio state. One instance per run.

    Lifecycle:
      - `__init__(starting_cash)` — once.
      - `apply(action, current_bar, next_bar, fees, slippage)` —
        once per bar from the backtester, AFTER `strategy.on_bar`
        produced the action. Translates intent → fill.
      - `mark_to_market(current_bar)` — once per bar, after `apply`.
        Updates `unrealized_pnl` on each Position and appends to the
        equity curve.
      - `snapshot()` — anytime. Returns the read-only view passed
        to the next strategy call.
    """

    def __init__(self, starting_cash: float) -> None:
        if starting_cash <= 0:
            raise ValueError(f"starting_cash must be > 0, got {starting_cash}")
        self.starting_cash: float = float(starting_cash)
        self.cash: float = float(starting_cash)
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[Trade] = []
        self.equity_curve: list[tuple[datetime, float]] = []

    # ─────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> PortfolioSnapshot:
        """Read-only view for the strategy."""
        return PortfolioSnapshot(
            cash=self.cash,
            equity=self._current_equity(),
            positions={s: p.model_copy() for s, p in self.positions.items()},
            n_trades=len(self.closed_trades),
        )

    def apply(
        self,
        action: Action,
        current_bar: Bar,
        next_bar: Optional[Bar],
        fees: FeeModel,
        slippage: SlippageModel,
    ) -> Optional[Trade]:
        """
        Translate an Action into a Trade (or no-op).

        Returns the executed Trade or None for hold/no-fill. The
        return is for logging / breadcrumbs — the trade is also
        recorded in `closed_trades` and reflected in `cash` /
        `positions`.
        """
        if action.kind == "hold":
            return None

        # set_position is sugar over buy/sell. Compute delta and recurse.
        if action.kind == "set_position":
            current_qty = self._position_qty(action.symbol)
            target = action.size
            delta = target - current_qty
            if abs(delta) < 1e-9:
                return None
            decomposed = Action(
                kind="buy" if delta > 0 else "sell",
                symbol=action.symbol,
                size=abs(delta),
                limit_price=action.limit_price,
                stop_price=action.stop_price,
                note=f"set_position target={target} (from {current_qty})",
            )
            return self.apply(decomposed, current_bar, next_bar, fees, slippage)

        fill_price = slippage.fill_price(action, next_bar)
        if math.isnan(fill_price):
            # End of data — fill couldn't happen.
            return None

        if action.kind == "buy":
            return self._execute_buy(action, fill_price, current_bar, next_bar, fees)
        if action.kind == "sell":
            return self._execute_sell(action, fill_price, current_bar, next_bar, fees)

        logger.warning("Portfolio.apply: unknown action kind %r", action.kind)
        return None

    def mark_to_market(self, bar: Bar) -> None:
        """
        Recompute unrealized_pnl on each position using `bar.close`
        and append `(bar.timestamp, current_equity)` to the equity
        curve. Called once per bar after `apply()`.

        Only updates positions whose `symbol == bar.symbol` — the
        multi-symbol case requires the backtester to call this
        per-symbol-per-bar.
        """
        pos = self.positions.get(bar.symbol)
        if pos is not None:
            pos.unrealized_pnl = (bar.close - pos.avg_entry_price) * pos.quantity
        self.equity_curve.append((bar.timestamp, self._current_equity()))

    def mark_portfolio(self, timestamp: datetime, prices: dict[str, float]) -> None:
        """Multi-symbol mark: revalue every open position at its latest price and
        append ONE shared equity point. Used by the portfolio backtest, where
        many symbols share one equity curve (vs `mark_to_market`'s single symbol)."""
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px is not None:
                pos.unrealized_pnl = (px - pos.avg_entry_price) * pos.quantity
        self.equity_curve.append((timestamp, self._current_equity()))

    # ─────────────────────────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────────────────────────

    def _execute_buy(
        self,
        action: Action,
        fill_price: float,
        current_bar: Bar,
        next_bar: Optional[Bar],
        fees: FeeModel,
    ) -> Optional[Trade]:
        if fill_price <= 0:
            return None
        # Buy-to-cover: if we're short, a buy closes (realizes P&L on) the short.
        pos = self.positions.get(action.symbol)
        if pos is not None and pos.quantity < 0:
            qty = min(action.size, -pos.quantity)
            if qty <= 0:
                return None
            fee = fees.fee_for(Action(kind="buy", symbol=action.symbol, size=qty), fill_price)
            realized = (pos.avg_entry_price - fill_price) * qty - fee  # short: profit when fill < entry
            fill_ts = next_bar.timestamp if next_bar else current_bar.timestamp
            holding_days = max(0.0, (fill_ts - pos.entry_time).total_seconds() / 86_400.0)
            self.cash -= qty * fill_price + fee  # covering costs cash
            trade = Trade(
                symbol=action.symbol, side="buy", quantity=qty, price=fill_price,
                timestamp=fill_ts, fees=fee, realized_pnl=realized,
                holding_days=holding_days, is_closing=True, note=action.note,
            )
            pos.quantity += qty
            if pos.quantity >= -1e-9:
                del self.positions[action.symbol]
            self.closed_trades.append(trade)
            return trade

        # Otherwise: open/add a LONG. Clamp to what cash allows (no margin).
        requested_qty = action.size
        max_qty_by_cash = self.cash / fill_price  # ignores fees; refine below
        qty = min(requested_qty, max_qty_by_cash)
        if qty <= 0:
            return None

        # Compute fee at this qty; if total exceeds cash, downsize.
        provisional_fee = fees.fee_for(
            Action(kind="buy", symbol=action.symbol, size=qty),
            fill_price,
        )
        total_cost = qty * fill_price + provisional_fee
        if total_cost > self.cash:
            # Iterate one step: solve qty such that qty*p + fee(qty) ≤ cash.
            # Approximate by removing the fee from cash and re-deriving qty.
            qty = max(0.0, (self.cash - provisional_fee) / fill_price)
            if qty <= 0:
                return None
            provisional_fee = fees.fee_for(
                Action(kind="buy", symbol=action.symbol, size=qty),
                fill_price,
            )

        fill_ts = next_bar.timestamp if next_bar else current_bar.timestamp
        trade = Trade(
            symbol=action.symbol,
            side="buy",
            quantity=qty,
            price=fill_price,
            timestamp=fill_ts,
            fees=provisional_fee,
            note=action.note,
        )
        self.cash -= qty * fill_price + provisional_fee
        # Floor at exactly 0 — float math can leave a sub-cent residual that
        # makes cash technically negative even when the iteration above
        # guaranteed enough headroom. We treat -$1e-9 as 0; anything larger
        # is a real bug worth investigating.
        if -1e-6 < self.cash < 0.0:
            self.cash = 0.0
        self._add_to_position(action.symbol, qty, fill_price, fill_ts)
        self.closed_trades.append(trade)
        return trade

    def _execute_sell(
        self,
        action: Action,
        fill_price: float,
        current_bar: Bar,
        next_bar: Optional[Bar],
        fees: FeeModel,
    ) -> Optional[Trade]:
        if fill_price <= 0:
            return None
        pos = self.positions.get(action.symbol)
        fill_ts = next_bar.timestamp if next_bar else current_bar.timestamp

        if pos is not None and pos.quantity > 0:
            # Sell-to-close a LONG (realizes P&L).
            qty = min(action.size, pos.quantity)
            if qty <= 0:
                return None
            fee = fees.fee_for(Action(kind="sell", symbol=action.symbol, size=qty), fill_price)
            realized = (fill_price - pos.avg_entry_price) * qty - fee
            holding_days = max(0.0, (fill_ts - pos.entry_time).total_seconds() / 86_400.0)
            trade = Trade(
                symbol=action.symbol, side="sell", quantity=qty, price=fill_price,
                timestamp=fill_ts, fees=fee, realized_pnl=realized,
                holding_days=holding_days, is_closing=True, note=action.note,
            )
            self.cash += qty * fill_price - fee
            self._reduce_position(action.symbol, qty)
            self.closed_trades.append(trade)
            return trade

        # Sell-to-open a SHORT (flat or already short). Proceeds credited to cash;
        # equity reflects the liability via the negative position (see mark_to_market).
        qty = action.size
        if qty <= 0:
            return None
        fee = fees.fee_for(Action(kind="sell", symbol=action.symbol, size=qty), fill_price)
        self.cash += qty * fill_price - fee
        self._add_to_short(action.symbol, qty, fill_price, fill_ts)
        trade = Trade(
            symbol=action.symbol, side="sell", quantity=qty, price=fill_price,
            timestamp=fill_ts, fees=fee, realized_pnl=0.0, is_closing=False, note=action.note,
        )
        self.closed_trades.append(trade)
        return trade

    def _add_to_position(
        self, symbol: str, qty: float, price: float, ts: datetime,
    ) -> None:
        """Weighted-average entry price."""
        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = Position(
                symbol=symbol, quantity=qty,
                avg_entry_price=price, entry_time=ts,
            )
            return
        new_qty = existing.quantity + qty
        new_avg = (
            existing.avg_entry_price * existing.quantity + price * qty
        ) / new_qty
        existing.quantity = new_qty
        existing.avg_entry_price = new_avg

    def _add_to_short(self, symbol: str, qty: float, price: float, ts: datetime) -> None:
        """Open or add to a SHORT (quantity < 0), weighted-average entry price."""
        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = Position(
                symbol=symbol, quantity=-qty, avg_entry_price=price, entry_time=ts,
            )
            return
        prior_short = -existing.quantity            # positive size
        new_short = prior_short + qty
        existing.avg_entry_price = (
            existing.avg_entry_price * prior_short + price * qty
        ) / new_short
        existing.quantity = -new_short

    def _reduce_position(self, symbol: str, qty: float) -> None:
        existing = self.positions.get(symbol)
        if existing is None:
            return
        existing.quantity -= qty
        if existing.quantity <= 1e-9:
            del self.positions[symbol]

    def _position_qty(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0

    def _current_equity(self) -> float:
        """
        Cash plus mark-to-market value of all positions.

        position_value = quantity * avg_entry_price + unrealized_pnl
            (unrealized_pnl is (mark - avg) * quantity by definition)
        """
        positions_value = sum(
            p.quantity * p.avg_entry_price + p.unrealized_pnl
            for p in self.positions.values()
        )
        return self.cash + positions_value
