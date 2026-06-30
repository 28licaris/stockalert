"""
Paper-trading service (M3).

Design (honest + minimal): re-run the LOCKED config via the same
`Backtester.run_portfolio` against live-updating ClickHouse data, then report the
slice AFTER `go_live` as the forward track record. This guarantees the paper
strategy is byte-for-byte the validated one (identical engine), and the forward
portion uses only bars that postdate the commitment — a real, no-look-ahead record.

State persists as JSON (one file per run). Production may move this to CH/Postgres;
JSON is a clean, inspectable MVP for a once-daily job.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.sim.backtester import Backtester
from app.services.sim.loader import build_strategy
from app.services.sim.paper.schemas import (
    PaperEquityPoint, PaperPositionView, PaperRunConfig, PaperState, PaperStatus, PaperTradeView,
)
from app.services.sim.schemas import BacktestConfig

logger = logging.getLogger(__name__)


def _paper_dir() -> Path:
    d = Path(os.environ.get("STOCKALERT_PAPER_DIR", Path.cwd() / "data" / "paper"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return _paper_dir() / f"{safe}.json"


def load_state(name: str) -> Optional[PaperState]:
    p = _state_path(name)
    if not p.exists():
        return None
    return PaperState.model_validate_json(p.read_text())


def save_state(state: PaperState) -> Path:
    p = _state_path(state.config.name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2))
    tmp.replace(p)  # atomic
    return p


def run_paper(cfg: PaperRunConfig, now: Optional[datetime] = None) -> PaperState:
    """Run the locked config from history_start..now and persist the result."""
    now = now or datetime.now(timezone.utc)
    bt_cfg = BacktestConfig(
        symbols=cfg.symbols, start=cfg.history_start, end=now, interval=cfg.interval,
        benchmark=cfg.benchmark, starting_cash=cfg.starting_cash,
        history_window=cfg.history_window,
        max_concurrent_positions=cfg.max_concurrent_positions,
        max_portfolio_heat=cfg.max_portfolio_heat,
        momentum_top_n=cfg.momentum_top_n, momentum_bottom_n=cfg.momentum_bottom_n,
        momentum_lookback=cfg.momentum_lookback,
    )
    strat = build_strategy(cfg.strategy, cfg.strategy_params, interval=cfg.interval)
    result = Backtester().run_portfolio(strat, bt_cfg)
    computed_through = result.equity_curve[-1][0] if result.equity_curve else None
    state = PaperState(
        config=cfg, last_run_at=now, computed_through=computed_through,
        equity_curve=list(result.equity_curve),
        trades=[t.model_dump() for t in result.trades],
        open_positions=[p.model_dump() for p in result.open_positions],
    )
    save_state(state)
    logger.info(
        "paper.run %s: through=%s equity=%.0f open=%d trades=%d",
        cfg.name, computed_through, result.metrics.final_equity,
        len(result.open_positions), len(result.trades),
    )
    return state


def build_status(
    state: PaperState,
    start: Optional[datetime] = None,
    capital: Optional[float] = None,
    trade_limit: Optional[int] = 100,
) -> PaperStatus:
    """Forward track record, REBASED to `capital` as of `start` (defaults to the
    locked go_live / configured cash). Pure slice + scale of the persisted run —
    lets the UI replay the strategy forward from any past date at any starting
    capital instantly (no re-run). $ amounts (equity, P&L, position size) scale by
    the rebase factor; prices and dates are untouched."""
    from datetime import timedelta

    cfg = state.config
    start_date = start or cfg.go_live
    if start_date.tzinfo is None:  # date-only query params arrive naive; curve ts are tz-aware
        start_date = start_date.replace(tzinfo=timezone.utc)
    starting_capital = capital if capital is not None else cfg.starting_cash
    curve = state.equity_curve

    # Baseline equity at start_date (last point at/before it; else first point).
    baseline = None
    for t, e in curve:
        if t <= start_date:
            baseline = e
        else:
            break
    if baseline is None:
        baseline = curve[0][1] if curve else cfg.starting_cash
    rebase = (starting_capital / baseline) if baseline else 1.0

    fwd_curve = [(t, e * rebase) for t, e in curve if t >= start_date]
    if not fwd_curve and curve:
        fwd_curve = [(curve[-1][0], curve[-1][1] * rebase)]
    current = fwd_curve[-1][1] if fwd_curve else starting_capital

    # Pair each closing leg with its opening leg's fill price. AlertStrategy holds
    # one position per symbol at a time, so entry/exit legs alternate per symbol —
    # a simple "last open price per symbol" pairing is exact.
    entry_px: dict[int, float] = {}
    _open_px: dict[str, float] = {}
    for tr in state.trades:
        if tr.get("is_closing"):
            entry_px[id(tr)] = _open_px.get(tr["symbol"], float("nan"))
        else:
            _open_px[tr["symbol"]] = tr["price"]
            entry_px[id(tr)] = tr["price"]

    def _mk_trade(t: dict) -> PaperTradeView:
        ts = _ts(t["timestamp"])
        hold = float(t.get("holding_days", 0.0) or 0.0)
        closing = bool(t.get("is_closing"))
        ep = entry_px.get(id(t))
        entry_price = ep if (ep is not None and ep == ep) else None  # drop NaN
        return PaperTradeView(
            symbol=t["symbol"], side=t["side"], quantity=t["quantity"] * rebase,
            price=t["price"], timestamp=ts,
            realized_pnl=float(t.get("realized_pnl", 0.0)) * rebase,
            holding_days=hold, is_closing=closing,
            exit_date=ts if closing else None,
            entry_date=(ts - timedelta(days=hold)) if closing else ts,
            entry_price=entry_price,
            exit_price=t["price"] if closing else None,
        )

    def _mk_pos(p: dict) -> PaperPositionView:
        qty = p["quantity"]
        avg = p["avg_entry_price"]
        upnl = p.get("unrealized_pnl", 0.0)
        # current mark from the position: unrealized = qty*(mark - avg) → mark = avg + upnl/qty.
        # qty & upnl share the rebase factor, so the price is rebase-invariant.
        current_price = (avg + upnl / qty) if qty else avg
        return PaperPositionView(
            symbol=p["symbol"], quantity=qty * rebase,
            avg_entry_price=avg, current_price=current_price, entry_time=_ts(p["entry_time"]),
            unrealized_pnl=upnl * rebase,
        )

    fwd_trades = [t for t in state.trades if _ts(t["timestamp"]) >= start_date]
    fwd_closed = [t for t in fwd_trades if t.get("is_closing")]
    wins = [t for t in fwd_closed if t.get("realized_pnl", 0.0) > 0]
    win_rate = (len(wins) / len(fwd_closed)) if fwd_closed else None
    days_live = max(0, ((state.computed_through or start_date) - start_date).days)

    # "Today" = the latest computed bar date — the alertable activity for this run.
    through = state.computed_through
    today_entries, today_exits = [], []
    if through is not None:
        d = through.date()
        today_entries = [_mk_pos(p) for p in state.open_positions
                         if _ts(p["entry_time"]).date() == d]
        today_exits = [_mk_trade(t) for t in state.trades
                       if t.get("is_closing") and _ts(t["timestamp"]).date() == d]

    return PaperStatus(
        name=cfg.name, go_live=cfg.go_live, start_date=start_date,
        last_run_at=state.last_run_at, computed_through=state.computed_through,
        days_live=days_live, starting_capital=starting_capital, current_balance=current,
        forward_return=(current / starting_capital - 1.0) if starting_capital else 0.0,
        forward_n_trades=len(fwd_closed), forward_win_rate=win_rate,
        n_open_positions=len(state.open_positions),
        open_positions=[_mk_pos(p) for p in state.open_positions],
        forward_trades=[_mk_trade(t) for t in (fwd_trades if trade_limit is None else fwd_trades[-trade_limit:])],
        equity_curve=[PaperEquityPoint(t=t, equity=e) for t, e in fwd_curve],
        today_entries=today_entries, today_exits=today_exits,
    )


def export_csv(
    state: PaperState,
    start: Optional[datetime] = None,
    capital: Optional[float] = None,
) -> str:
    """A complete, human-readable CSV log: summary (start/end balance, return) +
    every closed trade (entry/exit dates, P&L) + current open positions. Rebased
    to the requested start/capital, like the dashboard."""
    import csv
    import io

    s = build_status(state, start, capital, trade_limit=None)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Paper trading log", s.name])
    w.writerow(["# Start date", s.start_date.date().isoformat()])
    w.writerow(["# Computed through", s.computed_through.date().isoformat() if s.computed_through else ""])
    w.writerow(["# Starting balance", round(s.starting_capital, 2)])
    w.writerow(["# Ending balance", round(s.current_balance, 2)])
    w.writerow(["# Return $", round(s.current_balance - s.starting_capital, 2)])
    w.writerow(["# Return %", round(s.forward_return * 100, 2)])
    w.writerow(["# Days", s.days_live])
    w.writerow(["# Closed trades", s.forward_n_trades])
    w.writerow(["# Win rate %", round(s.forward_win_rate * 100, 1) if s.forward_win_rate is not None else ""])
    w.writerow([])
    w.writerow(["CLOSED TRADES"])
    w.writerow(["symbol", "side", "entry_date", "exit_date", "held_days", "quantity",
                "entry_price", "exit_price", "realized_pnl"])
    for t in s.forward_trades:
        if not t.is_closing:
            continue
        w.writerow([
            t.symbol, t.side,
            t.entry_date.date().isoformat() if t.entry_date else "",
            t.exit_date.date().isoformat() if t.exit_date else "",
            round(t.holding_days, 1), round(t.quantity, 2),
            round(t.entry_price, 2) if t.entry_price is not None else "",
            round(t.exit_price, 2) if t.exit_price is not None else "",
            round(t.realized_pnl, 2),
        ])
    w.writerow([])
    w.writerow(["OPEN POSITIONS"])
    w.writerow(["symbol", "side", "entry_date", "quantity", "avg_entry_price", "unrealized_pnl"])
    for p in s.open_positions:
        w.writerow([
            p.symbol, "long" if p.quantity >= 0 else "short", p.entry_time.date().isoformat(),
            round(p.quantity, 2), round(p.avg_entry_price, 2), round(p.unrealized_pnl, 2),
        ])
    return buf.getvalue()


def append_alerts(status: PaperStatus) -> int:
    """Append today's entries/exits to <name>_alerts.jsonl (idempotent per date).
    Returns the count of alert rows written (0 if nothing new or already logged)."""
    import json

    if not status.today_entries and not status.today_exits:
        return 0
    path = _paper_dir() / f"{status.name}_alerts.jsonl"
    date_str = (status.computed_through or status.go_live).date().isoformat()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                if json.loads(line).get("date") == date_str:
                    return 0  # already logged this date — idempotent
            except (ValueError, KeyError):
                continue
    row = {
        "date": date_str,
        "entries": [{"symbol": p.symbol, "side": "long" if p.quantity >= 0 else "short",
                     "entry": p.avg_entry_price} for p in status.today_entries],
        "exits": [{"symbol": t.symbol, "pnl": t.realized_pnl} for t in status.today_exits],
    }
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return len(row["entries"]) + len(row["exits"])


def _ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
