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


def build_status(state: PaperState) -> PaperStatus:
    """Compute the forward (post-go-live) track record from persisted state."""
    cfg = state.config
    go = cfg.go_live
    curve = state.equity_curve
    # Baseline equity = last point at/before go_live (or first point if go_live precedes data).
    baseline = cfg.starting_cash
    for t, e in curve:
        if t <= go:
            baseline = e
        else:
            break
    current = curve[-1][1] if curve else cfg.starting_cash
    fwd_trades = [t for t in state.trades if _ts(t["timestamp"]) >= go]
    fwd_closed = [t for t in fwd_trades if t.get("is_closing")]
    wins = [t for t in fwd_closed if t.get("realized_pnl", 0.0) > 0]
    win_rate = (len(wins) / len(fwd_closed)) if fwd_closed else None
    days_live = max(0, ((state.computed_through or go) - go).days)
    return PaperStatus(
        name=cfg.name, go_live=go, last_run_at=state.last_run_at,
        computed_through=state.computed_through, days_live=days_live,
        equity_at_go_live=baseline, current_equity=current,
        forward_return=(current / baseline - 1.0) if baseline else 0.0,
        forward_n_trades=len(fwd_closed),
        forward_win_rate=win_rate,
        n_open_positions=len(state.open_positions),
        open_positions=[PaperPositionView(**p) for p in state.open_positions],
        forward_trades=[PaperTradeView(**t) for t in fwd_trades[-50:]],
        equity_curve=[PaperEquityPoint(t=t, equity=e) for t, e in curve],
    )


def _ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
