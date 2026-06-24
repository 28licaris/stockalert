"""
MCP tools — backtest execution + run history (`tools/sim.py`).

`run_backtest` is exercised against the canary (sma_crossover) so
no API key is needed. The Backtester's `_fetch_bars` is stubbed
with synthetic bars so the test runs offline and deterministically.

`list_strategy_runs` is exercised with a stubbed registry-read
function.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.mcp.server import mcp, register_all_tools


register_all_tools()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _unwrap(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result: {result!r}")


class _SyntheticBar:
    def __init__(self, symbol, ts, open_, high, low, close, volume=1000.0):
        self.symbol = symbol
        self.timestamp = ts
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def _synthetic_bars(symbol: str, closes: list[float]):
    base = datetime(2024, 8, 1, tzinfo=timezone.utc)
    return [
        _SyntheticBar(
            symbol=symbol, ts=base + timedelta(days=i),
            open_=c, high=c * 1.005, low=c * 0.995, close=c, volume=10_000,
        )
        for i, c in enumerate(closes)
    ]


# ─────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────


def test_sim_tools_registered() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"run_backtest", "list_strategy_runs"} <= names


# ─────────────────────────────────────────────────────────────────────
# run_backtest
# ─────────────────────────────────────────────────────────────────────


def test_run_backtest_canary(monkeypatch) -> None:
    """End-to-end: MCP `run_backtest` against sma_crossover + synthetic bars."""
    from app.services.sim import backtester as bt_mod

    # 12 flat bars + a clean uptrend → guaranteed cross-up.
    bars = _synthetic_bars("TEST", [100.0] * 12 + [105.0, 110.0, 115.0, 120.0, 125.0])

    monkeypatch.setattr(
        bt_mod.Backtester, "_fetch_bars_multi",
        lambda self, c, intervals: {iv: {"TEST": bars} for iv in intervals},
    )
    monkeypatch.setattr(bt_mod.Backtester, "_capture_snapshot",
                        lambda self, c, exec_interval: "test-snap")

    # Don't write to CH in the test (it may not be reachable).
    with patch("app.services.sim.registry.write_run"):
        result = asyncio.run(mcp.call_tool("run_backtest", {
            "strategy_name": "sma_crossover",
            "strategy_params": {
                "fast_period": 3,
                "slow_period": 10,
                "position_size_pct": 0.95,
            },
            "config": {
                "symbols": ["TEST"],
                "start": bars[0].timestamp.isoformat(),
                "end": bars[-1].timestamp.isoformat(),
                "interval": "1d",
                "starting_cash": 10000.0,
                "history_window": 50,
                "fees_model": "zero",
                "slippage_model": "next_bar_open",
            },
            "write_to_registry": False,
        }))

    body = _unwrap(result)
    # RunMetrics returns as a dict; n_trades should be > 0 on the crossing series.
    assert body["n_trades"] >= 1
    assert body["final_equity"] > 0
    assert "total_return" in body
    assert "max_drawdown" in body


def test_run_backtest_unknown_strategy_raises(monkeypatch) -> None:
    """Unknown strategy name surfaces a clear MCP-side error."""
    from app.services.sim import backtester as bt_mod

    monkeypatch.setattr(
        bt_mod.Backtester, "_fetch_bars_multi",
        lambda self, c, intervals: {
            iv: {"TEST": _synthetic_bars("TEST", [100.0, 101.0])} for iv in intervals
        },
    )
    monkeypatch.setattr(
        bt_mod.Backtester, "_capture_snapshot",
        lambda self, c, exec_interval: None,
    )

    with pytest.raises(Exception):  # FastMCP wraps the ValueError as a ToolError
        asyncio.run(mcp.call_tool("run_backtest", {
            "strategy_name": "nope_unknown",
            "strategy_params": {},
            "config": {
                "symbols": ["TEST"],
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-02T00:00:00Z",
                "interval": "1d",
            },
        }))


# ─────────────────────────────────────────────────────────────────────
# list_strategy_runs
# ─────────────────────────────────────────────────────────────────────


def test_list_strategy_runs_returns_rows() -> None:
    """Stubbed registry returns 2 rows; tool slims them for the agent."""
    fake_rows = [
        {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "started_at": datetime(2024, 8, 1, 12, 0, tzinfo=timezone.utc),
            "strategy_name": "sma_crossover",
            "strategy_version": "0.1",
            "interval": "1d",
            "start_date": datetime(2024, 1, 1).date(),
            "end_date": datetime(2024, 7, 31).date(),
            "n_trades": 5,
            "total_return": 0.10,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.05,
            "final_equity": 11000.0,
        },
    ]
    with patch("app.mcp.tools.sim._list_runs", return_value=fake_rows):
        body = _unwrap(asyncio.run(mcp.call_tool("list_strategy_runs", {
            "strategy_name": "sma_crossover",
            "limit": 10,
        })))
    # list returns wrap as {"result": [...]} per FastMCP convention for non-dict types.
    payload = body.get("result", body)
    assert len(payload) == 1
    assert payload[0]["strategy_name"] == "sma_crossover"
    assert payload[0]["n_trades"] == 5
    # Timestamps are isoformat strings now (JSON-safe).
    assert isinstance(payload[0]["started_at"], str)
    assert "T" in payload[0]["started_at"]


def test_list_strategy_runs_clamps_limit() -> None:
    """limit > 200 gets clamped silently."""
    captured: dict = {}

    def _capture(strategy_name=None, limit=50):
        captured["limit"] = limit
        return []

    with patch("app.mcp.tools.sim._list_runs", side_effect=_capture):
        asyncio.run(mcp.call_tool("list_strategy_runs", {"limit": 9999}))
    assert captured["limit"] == 200
