"""
AlertStrategy — the signal→backtest bridge (Milestone 1).

Trades any `SignalSource` uniformly: on each bar, if flat, ask the source for a
setup and (if it passes the gates) enter it sized off its stop; if in a
position, exit when the bar touches the stop or target. This is what finally
makes our alerts/setups *backtestable* — point it at `ma_cross`, `breakout`,
or (later) an Elliott-Wave source and get reproducible win-rate / P&L / drawdown
from the existing evaluator + agent_runs registry.

Design choices (see docs/strategy_rnd_platform_design.md):
  - **Long-only** for now (the engine reserves quantity<0 for a later short tier).
    Short signals are skipped.
  - **Risk-based sizing:** size so entry→stop loss ≈ `risk_pct` of equity, capped
    by available cash. This makes win-rate and avg-R comparable across signals.
  - **Exit fills** are market orders emitted on the bar that touches stop/target,
    filled at the next bar's open per the harness slippage model. This slightly
    flatters/penalizes vs a true stop fill at the stop price; it's the honest,
    no-look-ahead baseline. A limit/stop fill model is a later refinement.
  - **Gates:** minimum reward:risk and confidence — the seed of the Milestone-2
    A+ scoring layer.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.filters import FilteredSignalSource, build_filter
from app.services.sim.schemas import Action, hold
from app.services.sim.signal_source import Signal, build_signal_source
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


def build_filtered_source(
    source: str,
    source_params: Optional[dict] = None,
    filters: Optional[list[dict]] = None,
    *,
    filter_mode: str = "all",
    min_score: Optional[float] = None,
):
    """Build a SignalSource, wrapping it in the composable A+ filter layer when
    filters are declared. Shared by AlertStrategy and RegimeSwitchStrategy so the
    {source, source_params, filters, filter_mode, min_score} contract is built one
    way everywhere."""
    base = build_signal_source(source, **(source_params or {}))
    if filters:
        built = [build_filter(f["name"], **(f.get("params") or {})) for f in filters]
        return FilteredSignalSource(base, built, mode=filter_mode, min_score=min_score)
    return base


class AlertStrategyParams(BaseModel):
    source: str = Field("breakout", description="Registered SignalSource name (ma_cross, breakout).")
    source_params: dict = Field(default_factory=dict, description="kwargs for the SignalSource.")
    filters: list[dict] = Field(
        default_factory=list,
        description=(
            "Composable A+ filters applied to each signal, e.g. "
            "[{name: trend, params: {period: 50}}, {name: reward_risk, params: {min_rr: 2}}]. "
            "Empty = raw source signals."
        ),
    )
    filter_mode: str = Field(
        "all", description="'all' (every filter must pass) or 'score' (weighted score ≥ min_score).",
    )
    min_score: Optional[float] = Field(
        None, description="For filter_mode='score': minimum summed filter weight to pass.",
    )
    risk_pct: float = Field(
        0.01, gt=0.0, le=0.25,
        description="Base fraction of equity risked per trade (entry→stop). 0.01 = 1%.",
    )
    max_risk_pct: Optional[float] = Field(
        None, gt=0.0, le=0.25,
        description=(
            "Ceiling risk for FULL-confluence (confidence=1.0) setups; risk scales "
            "linearly from risk_pct (confidence 0) to max_risk_pct (confidence 1). "
            "e.g. risk_pct=0.01, max_risk_pct=0.05 → 1% on weak setups, up to 5% when "
            "every confirmation agrees. None = flat risk_pct (no scaling)."
        ),
    )
    max_cash_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Cap on cash deployed per position (headroom for fees/slippage).",
    )
    min_reward_risk: float = Field(
        1.5, ge=0.0, description="Skip signals whose target:stop reward:risk is below this.",
    )
    min_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Skip signals below this source confidence.",
    )
    max_holding_days: Optional[float] = Field(
        None,
        description=(
            "Time stop: exit at market once a position has been held this many "
            "calendar days, even if neither stop nor target hit. None = no time "
            "stop (hold until stop/target). Caps capital tied up per trade."
        ),
    )
    entry_policy: str = Field(
        "next_open",
        description=(
            "How a signal becomes a fill. 'next_open' (legacy): market entry at "
            "the next bar's open. 'retest_limit': working buy-limit at the "
            "signal's stop level (the broken structure) for entry_expiry_days — "
            "better entries, misses runners that never retest. 'hourly_pullback': "
            "wait for a pullback below the signal close, enter on the first "
            "hourly turn-up while the level holds, stop = the pullback low "
            "(hourly structure). Both working policies need ctx.intraday "
            "(BacktestConfig.hourly_table); reward:risk multiple re-anchors at "
            "the actual entry."
        ),
    )
    entry_expiry_days: int = Field(
        5, ge=1,
        description="Working-order lifetime in trading bars for retest_limit / hourly_pullback.",
    )
    stop_trigger: str = Field(
        "touch",
        description=(
            "'touch' (default): a resting stop order — exits the moment the level "
            "trades (fills at the level with ctx.intraday, else legacy). 'close': "
            "EOD-confirmed stop — exits only when the bar CLOSES beyond the stop, "
            "filled at the next open (a wick through the level doesn't take you "
            "out; the cost is that a hard close-through loses more than 1R). "
            "Targets are unaffected (resting limit at the level)."
        ),
    )


class AlertStrategy(BaseStrategy):
    """Execute a pluggable SignalSource as long trades with stop/target exits."""

    name: str = "alert_driven"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[AlertStrategyParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or AlertStrategyParams()
        self.interval = interval
        self.source = build_filtered_source(
            self.params.source, self.params.source_params, self.params.filters,
            filter_mode=self.params.filter_mode, min_score=self.params.min_score,
        )
        # Active trade plan per symbol (stop/target tracking for open positions).
        self._plans: dict[str, Signal] = {}
        # Working entry orders per symbol (retest_limit / hourly_pullback):
        # [signal, bars_left, pullback_low_or_None, pulled_back_flag].
        self._pending: dict[str, list] = {}

    def setup(self, ctx: Context) -> None:
        self._plans = {}
        self._pending = {}
        self.source.setup(ctx)

    def on_bar(self, ctx: Context) -> Action:
        symbol = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(symbol)
        if pos is not None and pos.quantity != 0:
            self._pending.pop(symbol, None)  # a fill supersedes any working order
            return self._manage_exit(ctx, symbol, pos)

        if symbol in self._pending:
            return self._work_pending(ctx, symbol)
        return self._maybe_enter(ctx, symbol)

    # ── exits ──────────────────────────────────────────────────────────
    def _manage_exit(self, ctx: Context, symbol: str, pos) -> Action:
        plan = self._plans.get(symbol)
        if plan is None:
            return hold()  # position without a tracked plan — leave it be
        bar = ctx.bar
        is_long = pos.quantity > 0
        qty = abs(pos.quantity)
        # Exit leg: sell to close a long, buy to cover a short.
        exit_kind = "sell" if is_long else "buy"

        # Path-aware exits: when the engine provides the day's hourly path
        # (ctx.intraday / BacktestConfig.hourly_table), order stop-vs-target by
        # FIRST intraday touch and fill AT the level on this bar. A day that
        # GAPS through the level fills at the (worse) open. Falls back to the
        # legacy whole-bar worst-case checks when no path exists for this day.
        path = getattr(ctx, "intraday", None)
        hours = path.bars_for(symbol, bar.timestamp.date()) if path is not None else []
        eod_stop = self.params.stop_trigger == "close"
        if hours:
            for i, hb in enumerate(hours):
                stop_hit = (not eod_stop) and (
                    hb.low <= plan.stop if is_long else hb.high >= plan.stop)
                target_hit = hb.high >= plan.target_1 if is_long else hb.low <= plan.target_1
                if stop_hit:  # worst case within a single hour that spans both
                    level = plan.stop
                    if i == 0 and ((is_long and hb.open <= level)
                                   or (not is_long and hb.open >= level)):
                        level = hb.open  # gapped through the stop → open fill
                    self._plans.pop(symbol, None)
                    ctx.log(event="exit_stop", stop=plan.stop, dir=plan.direction,
                            fill="intraday")
                    return Action(kind=exit_kind, symbol=symbol, size=qty,
                                  fill_at_level=level,
                                  note=f"stop @ {plan.stop:.4f} ({plan.kind})")
                if target_hit:
                    level = plan.target_1
                    if i == 0 and ((is_long and hb.open >= level)
                                   or (not is_long and hb.open <= level)):
                        level = hb.open  # gapped through the target → open fill
                    self._plans.pop(symbol, None)
                    ctx.log(event="exit_target", target=plan.target_1,
                            dir=plan.direction, fill="intraday")
                    return Action(kind=exit_kind, symbol=symbol, size=qty,
                                  fill_at_level=level,
                                  note=f"target @ {plan.target_1:.4f} ({plan.kind})")
            # EOD-confirmed stop: the day's wicks didn't matter; the CLOSE did.
            if eod_stop:
                closed_through = (bar.close <= plan.stop if is_long
                                  else bar.close >= plan.stop)
                if closed_through:
                    self._plans.pop(symbol, None)
                    ctx.log(event="exit_stop", stop=plan.stop, dir=plan.direction,
                            fill="eod_close")
                    return Action(kind=exit_kind, symbol=symbol, size=qty,
                                  note=f"stop(close) @ {plan.stop:.4f} ({plan.kind})")
        else:
            # Stop: long stops below (low ≤ stop); short stops above (high ≥ stop).
            # Stop checked before target (worst case when a bar spans both).
            stop_hit = ((bar.close if eod_stop else bar.low) <= plan.stop if is_long
                        else (bar.close if eod_stop else bar.high) >= plan.stop)
            if stop_hit:
                self._plans.pop(symbol, None)
                ctx.log(event="exit_stop", stop=plan.stop, dir=plan.direction)
                return Action(kind=exit_kind, symbol=symbol, size=qty,
                              note=f"stop @ {plan.stop:.4f} ({plan.kind})")
            target_hit = bar.high >= plan.target_1 if is_long else bar.low <= plan.target_1
            if target_hit:
                self._plans.pop(symbol, None)
                ctx.log(event="exit_target", target=plan.target_1, dir=plan.direction)
                return Action(kind=exit_kind, symbol=symbol, size=qty,
                              note=f"target @ {plan.target_1:.4f} ({plan.kind})")
        # Time stop — cap how long capital stays tied up.
        if self.params.max_holding_days is not None:
            held = (bar.timestamp - pos.entry_time).total_seconds() / 86_400.0
            if held >= self.params.max_holding_days:
                self._plans.pop(symbol, None)
                ctx.log(event="exit_time", held_days=round(held, 1))
                return Action(kind=exit_kind, symbol=symbol, size=qty,
                              note=f"time stop @ {held:.0f}d ({plan.kind})")
        return hold()

    # ── entries ────────────────────────────────────────────────────────
    def _maybe_enter(self, ctx: Context, symbol: str) -> Action:
        sig = self.source.on_bar(ctx)
        if sig is None or sig.direction not in ("long", "short"):
            return hold()
        if sig.risk_per_share <= 0:
            return hold()
        if sig.reward_risk < self.params.min_reward_risk:
            return hold()
        if sig.confidence < self.params.min_confidence:
            return hold()

        # Working-order policies (long only): arm at signal close, fill later
        # via the hourly path. Shorts and path-less runs use the legacy fill.
        if (self.params.entry_policy != "next_open" and sig.direction == "long"
                and getattr(ctx, "intraday", None) is not None):
            self._pending[symbol] = [sig, self.params.entry_expiry_days, None, False, None]
            ctx.log(event="entry_armed", policy=self.params.entry_policy,
                    kind=sig.kind, level=sig.stop, expiry=self.params.entry_expiry_days)
            return hold()

        return self._emit_entry(ctx, symbol, sig, fill_level=None)

    def _emit_entry(self, ctx: Context, symbol: str, sig: Signal,
                    fill_level: Optional[float]) -> Action:
        """Size and emit an entry for `sig`; fill_level → path-aware level fill."""
        qty = self._size(ctx, sig)
        if qty <= 0:
            return hold()
        self._plans[symbol] = sig
        ctx.log(
            event="entry", kind=sig.kind, direction=sig.direction, entry=sig.entry,
            stop=sig.stop, target=sig.target_1, qty=qty, rr=round(sig.reward_risk, 2),
        )
        # Long → buy to open; short → sell to open (the engine opens a short).
        # Carry the stop so the portfolio risk manager can size portfolio heat.
        entry_kind = "buy" if sig.direction == "long" else "sell"
        return Action(kind=entry_kind, symbol=symbol, size=float(qty),
                      stop_price=sig.stop, target_price=sig.target_1,
                      fill_at_level=fill_level,
                      confidence=min(1.0, max(0.0, sig.confidence)),
                      note=f"{sig.kind} {sig.direction} rr={sig.reward_risk:.2f}: {sig.rationale}")

    # ── working entry orders (retest_limit / hourly_pullback) ─────────────
    def _work_pending(self, ctx: Context, symbol: str) -> Action:
        from dataclasses import replace

        sig, bars_left, pull_low, pulled, prev_high = self._pending[symbol]
        bar = ctx.bar
        level = sig.stop            # the broken structure level (source convention)
        risk0 = sig.risk_per_share  # signal's risk distance, re-anchored at the fill
        rr = max(sig.reward_risk, 1.0)
        path = getattr(ctx, "intraday", None)
        hours = path.bars_for(symbol, bar.timestamp.date()) if path is not None else []

        if self.params.entry_policy == "retest_limit":
            for i, hb in enumerate(hours):
                if hb.low <= level:
                    # Limit buy at the level; a gap BELOW the limit fills at the
                    # (better) open of the first hour.
                    fill = min(hb.open, level) if i == 0 else level
                    new_sig = replace(sig, entry=fill, stop=fill - risk0,
                                      target_1=fill + rr * risk0)
                    self._pending.pop(symbol, None)
                    ctx.log(event="entry_fill", policy="retest_limit", fill=fill)
                    return self._emit_entry(ctx, symbol, new_sig, fill_level=fill)
        else:  # hourly_pullback
            for hb in hours:
                if not pulled and hb.low < sig.entry:
                    pulled = True            # price gave back some of the signal bar
                if pulled:
                    pull_low = hb.low if pull_low is None else min(pull_low, hb.low)
                    if (prev_high is not None and hb.close > prev_high
                            and hb.close > level and hb.close > pull_low):
                        entry = hb.close     # first hourly turn-up with structure intact
                        risk = entry - pull_low
                        if risk > 0:
                            new_sig = replace(sig, entry=entry, stop=pull_low,
                                              target_1=entry + rr * risk)
                            self._pending.pop(symbol, None)
                            ctx.log(event="entry_fill", policy="hourly_pullback",
                                    fill=entry, stop=pull_low)
                            return self._emit_entry(ctx, symbol, new_sig, fill_level=entry)
                prev_high = hb.high

        # No fill today: cancel on structure failure (daily close back under the
        # level) or expiry; otherwise keep working.
        if bar.close < level:
            self._pending.pop(symbol, None)
            ctx.log(event="entry_cancelled", reason="close_below_level", level=level)
            return hold()
        bars_left -= 1
        if bars_left <= 0:
            self._pending.pop(symbol, None)
            ctx.log(event="entry_expired", policy=self.params.entry_policy)
        else:
            self._pending[symbol] = [sig, bars_left, pull_low, pulled, prev_high]
        return hold()

    def _size(self, ctx: Context, sig: Signal) -> int:
        """Conviction-scaled risk size: risk scales risk_pct→max_risk_pct by the
        signal's confluence confidence, then converts to shares off the stop,
        capped by cash. Higher-probability (more-confirmed) setups bet more."""
        p = self.params
        ceil = p.max_risk_pct if p.max_risk_pct is not None else p.risk_pct
        conf = min(1.0, max(0.0, sig.confidence))
        risk_pct = p.risk_pct + (ceil - p.risk_pct) * conf
        risk_amount = ctx.portfolio.equity * risk_pct
        qty_by_risk = math.floor(risk_amount / sig.risk_per_share)
        cash_cap = ctx.portfolio.cash * p.max_cash_pct
        qty_by_cash = math.floor(cash_cap / sig.entry) if sig.entry > 0 else 0
        return max(0, min(qty_by_risk, qty_by_cash))
