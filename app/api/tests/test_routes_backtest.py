"""Backtest API tests (TestClient; engine mocked so no ClickHouse needed)."""
from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.services.sim.schemas import BacktestConfig, RunMetrics, RunResult, Trade

UTC = dt.timezone.utc
T0 = dt.datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture
def client():
    from app.main_api import app

    @asynccontextmanager
    async def _noop(_app):
        yield

    app.router.lifespan_context = _noop
    with TestClient(app) as c:
        yield c


def _fake_result() -> RunResult:
    cfg = BacktestConfig(symbols=["AAPL"], start=T0, end=T0 + dt.timedelta(days=30), interval="1d")
    metrics = RunMetrics(
        total_return=0.12, max_drawdown=-0.05, longest_drawdown_days=8,
        win_rate=0.55, n_trades=4, final_equity=112_000.0, avg_holding_days=20.0,
        sharpe_ratio=1.1,
    )
    eq = [(T0 + dt.timedelta(days=i), 100_000.0 + i * 100) for i in range(30)]
    trades = [Trade(symbol="AAPL", side="buy", quantity=10, price=100.0, timestamp=T0),
              Trade(symbol="AAPL", side="sell", quantity=10, price=110.0, timestamp=T0 + dt.timedelta(days=5),
                    realized_pnl=100.0, holding_days=5.0, is_closing=True)]
    return RunResult(
        run_id=uuid.uuid4(), started_at=T0, finished_at=T0, strategy_name="alert_driven",
        strategy_version="0.1", strategy_params={}, config=cfg, snapshot_id=None,
        git_sha="abc123", metrics=metrics, equity_curve=eq, trades=trades,
    )


def test_catalog(client) -> None:
    r = client.get("/api/v1/backtest/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "alert_driven" in body["strategies"]
    assert "divergence" in body["signal_sources"]
    assert "regime" in body["filters"]


def test_run_returns_metrics_equity_trades(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sim.backtester.Backtester.run_portfolio",
        lambda self, strat, cfg: _fake_result(),
    )
    monkeypatch.setattr("app.services.sim.registry.write_run", lambda run: None)
    r = client.post("/api/v1/backtest/run", json={
        "strategy": "alert_driven",
        "strategy_params": {"source": "divergence", "source_params": {"side": "both"}},
        "symbols": ["AAPL", "MSFT"],
        "start": "2024-01-01T00:00:00Z", "end": "2024-12-31T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy"] == "alert_driven"
    assert body["metrics"]["total_return"] == 0.12
    assert body["metrics"]["avg_holding_days"] == 20.0
    assert len(body["equity_curve"]) == 30 and body["equity_curve"][0]["equity"] == 100_000.0
    assert any(t["is_closing"] for t in body["trades"])
    assert body["stored"] is True


def test_run_bad_strategy_400(client) -> None:
    r = client.post("/api/v1/backtest/run", json={
        "strategy": "does_not_exist", "symbols": ["AAPL"],
        "start": "2024-01-01T00:00:00Z", "end": "2024-12-31T00:00:00Z",
    })
    assert r.status_code == 400
