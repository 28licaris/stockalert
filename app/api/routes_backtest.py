"""
Backtest HTTP API — powers the customer-facing strategy playground.

  POST /api/v1/backtest/run        — run a strategy config, return metrics +
                                      equity curve + trades; persists to the
                                      agent_runs registry.
  GET  /api/v1/backtest/runs        — recent stored runs (history list).
  GET  /api/v1/backtest/runs/{id}   — one stored run's metrics.
  GET  /api/v1/backtest/catalog     — strategies / signal sources / filters the
                                      builder UI offers.

A run executes synchronously (single config, ~seconds). Sweeps/param-search stay
CLI/MCP for now. Same engine as the CLI + MCP — identical numbers across surfaces.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.sim.schemas import BacktestConfig, RunMetrics

logger = logging.getLogger(__name__)
router = APIRouter()

# Cap payload sizes so a long backtest doesn't return a huge JSON blob.
_MAX_EQUITY_POINTS = 600
_MAX_TRADES = 1000


# ─────────────────────────────────────────────────────────────────────
# Request / response shapes
# ─────────────────────────────────────────────────────────────────────


class BacktestRunRequest(BaseModel):
    strategy: str = Field("alert_driven", description="Strategy name (see /backtest/catalog).")
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(..., min_length=1)
    start: datetime
    end: datetime
    interval: str = "1d"
    benchmark: Optional[str] = "SPY"
    portfolio: bool = Field(True, description="True = shared-capital portfolio with risk caps; False = per-symbol.")
    starting_cash: float = 100_000.0
    max_concurrent_positions: int = 6
    max_portfolio_heat: float = 0.10
    momentum_top_n: Optional[int] = Field(None, description="Dynamic universe: long only the top-N as-of momentum names.")
    momentum_bottom_n: Optional[int] = Field(None, description="Dynamic universe: short only the bottom-N as-of momentum names.")
    momentum_lookback: int = 60
    store: bool = Field(True, description="Persist the run to the registry for the history list.")


class EquityPoint(BaseModel):
    t: datetime
    equity: float


class TradeOut(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: datetime
    realized_pnl: float
    holding_days: float
    is_closing: bool
    note: str = ""


class BacktestRunResponse(BaseModel):
    run_id: str
    strategy: str
    symbols: list[str]
    start: datetime
    end: datetime
    interval: str
    portfolio: bool
    stored: bool
    metrics: RunMetrics
    equity_curve: list[EquityPoint]
    trades: list[TradeOut]


class RunSummary(BaseModel):
    run_id: str
    started_at: Optional[datetime] = None
    strategy_name: str
    interval: Optional[str] = None
    total_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    n_trades: Optional[int] = None


class CatalogResponse(BaseModel):
    strategies: list[str]
    signal_sources: list[str]
    filters: list[str]


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/backtest/catalog", response_model=CatalogResponse)
def backtest_catalog() -> CatalogResponse:
    """Strategies, pluggable signal sources, and A+ filters the builder offers."""
    from app.services.sim.filters import list_filters
    from app.services.sim.loader import STRATEGY_NAMES
    from app.services.sim.signal_source import list_signal_sources

    return CatalogResponse(
        strategies=list(STRATEGY_NAMES),
        signal_sources=list_signal_sources(),
        filters=list_filters(),
    )


@router.post("/backtest/run", response_model=BacktestRunResponse)
def backtest_run(body: BacktestRunRequest = Body(...)) -> BacktestRunResponse:
    """Run one strategy config and return metrics + equity curve + trades."""
    from app.services.sim.backtester import Backtester
    from app.services.sim.loader import build_strategy

    cfg = BacktestConfig(
        symbols=body.symbols, start=body.start, end=body.end, interval=body.interval,
        benchmark=body.benchmark, starting_cash=body.starting_cash,
        max_concurrent_positions=body.max_concurrent_positions,
        max_portfolio_heat=body.max_portfolio_heat,
        momentum_top_n=body.momentum_top_n,
        momentum_bottom_n=body.momentum_bottom_n,
        momentum_lookback=body.momentum_lookback,
    )
    try:
        strategy = build_strategy(body.strategy, body.strategy_params, interval=body.interval)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad strategy/params: {exc}") from exc

    bt = Backtester()
    try:
        result = bt.run_portfolio(strategy, cfg) if body.portfolio else bt.run(strategy, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("backtest run failed for %s", body.strategy)
        raise HTTPException(status_code=500, detail=f"backtest failed: {type(exc).__name__}: {exc}") from exc

    stored = False
    if body.store:
        try:
            from app.services.sim.registry import write_run
            write_run(result)
            stored = True
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning("backtest registry write failed: %s", exc)

    return BacktestRunResponse(
        run_id=str(result.run_id), strategy=result.strategy_name, symbols=body.symbols,
        start=body.start, end=body.end, interval=body.interval, portfolio=body.portfolio,
        stored=stored, metrics=result.metrics,
        equity_curve=_downsample_equity(result.equity_curve),
        trades=_serialize_trades(result.trades),
    )


@router.get("/backtest/runs", response_model=list[RunSummary])
def backtest_runs(
    limit: int = Query(25, ge=1, le=200),
    strategy: Optional[str] = Query(None, description="Filter to one strategy name."),
) -> list[RunSummary]:
    """Recent stored backtest runs (history list)."""
    from app.services.sim.registry import list_runs
    try:
        rows = list_runs(strategy_name=strategy, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("backtest runs list failed: %s", exc)
        return []
    return [_row_to_summary(r) for r in rows]


@router.get("/paper/status")
def paper_status(
    name: str = Query("momentum_top15", description="Paper run name."),
    start: Optional[datetime] = Query(None, description="Replay start date (default = locked go_live). Set earlier to replay forward from a past date."),
    capital: Optional[float] = Query(None, gt=0, description="Starting capital to rebase to (default = config). The curve/P&L scale to this."),
):
    """Forward paper-trading track record, rebased to `capital` as of `start`."""
    from app.services.sim.paper.service import build_status, load_state
    state = load_state(name)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no paper run named {name!r} (run scripts/paper_trade_run.py)")
    return build_status(state, start=start, capital=capital)


@router.get("/backtest/runs/{run_id}", response_model=RunSummary)
def backtest_run_detail(run_id: str) -> RunSummary:
    """One stored run's summary metrics."""
    from app.services.sim.registry import fetch_run
    try:
        row = fetch_run(run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"registry read failed: {exc}") from exc
    if not row:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _row_to_summary(row)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _downsample_equity(curve: list[tuple[datetime, float]]) -> list[EquityPoint]:
    if len(curve) <= _MAX_EQUITY_POINTS:
        pts = curve
    else:
        step = len(curve) // _MAX_EQUITY_POINTS + 1
        pts = curve[::step]
        if pts[-1] != curve[-1]:
            pts = [*pts, curve[-1]]  # always keep the final point
    return [EquityPoint(t=t, equity=float(e)) for t, e in pts]


def _serialize_trades(trades: list) -> list[TradeOut]:
    out = [
        TradeOut(
            symbol=t.symbol, side=t.side, quantity=t.quantity, price=t.price,
            timestamp=t.timestamp, realized_pnl=t.realized_pnl,
            holding_days=t.holding_days, is_closing=t.is_closing, note=t.note,
        )
        for t in trades
    ]
    return out[-_MAX_TRADES:]


def _row_to_summary(row: dict) -> RunSummary:
    return RunSummary(
        run_id=str(row.get("run_id", "")),
        started_at=row.get("started_at"),
        strategy_name=row.get("strategy_name", ""),
        interval=row.get("interval"),
        total_return=row.get("total_return"),
        sharpe_ratio=row.get("sharpe_ratio"),
        max_drawdown=row.get("max_drawdown"),
        win_rate=row.get("win_rate"),
        n_trades=row.get("n_trades"),
    )
