"""EW-7 live path: IntradayWaveScanner wired into MonitorManager.

Tests cover:
  - start_wave_scanner adds a task to monitors dict
  - stop_wave_scanner cancels the task and removes the key
  - list_wave_scanners returns only wave: keys
  - duplicate start returns already_running
  - invalid interval returns error without creating a task
  - on_bar runs compute_labeling in a thread (not blocking the event loop)
  - on_bar skips unknown symbols
  - on_bar debounces repeated (wave, direction) pairs
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.indicators.pivots import Pivot
from app.services.alerts.intraday import IntradayWaveScanner, INTRADAY_INTERVALS
from app.services.live.monitor_manager import MonitorManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk(i, price, kind):
    return Pivot(index=i, timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                 price=price, kind=kind, k=10, degree=1, confirmed_at_index=i + 10)


def _mock_bar(symbol="AAPL", close=150.0):
    bar = MagicMock()
    bar.symbol = symbol
    bar.ticker = symbol
    bar.close = close
    return bar


def _mock_provider():
    p = MagicMock()
    p.subscribe_bars = MagicMock()
    p.unsubscribe_bars = MagicMock()
    return p


# ---------------------------------------------------------------------------
# MonitorManager — wave scanner lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_wave_scanner_adds_task():
    """start_wave_scanner creates a running asyncio.Task in monitors."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()

    result = mgr.start_wave_scanner(["AAPL"], interval="5m")

    assert result["status"] == "started"
    key = result["key"]
    assert key.startswith("wave:")
    assert key in mgr.monitors
    task = mgr.monitors[key]["task"]
    assert isinstance(task, asyncio.Task)
    assert not task.done()
    # Cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_stop_wave_scanner_cancels_task():
    """stop_wave_scanner cancels the task and removes the key."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()

    mgr.start_wave_scanner(["TSLA"], interval="5m")
    key = mgr._wave_key(["TSLA"], "5m")
    task = mgr.monitors[key]["task"]

    result = mgr.stop_wave_scanner(["TSLA"], interval="5m")

    assert result["status"] == "stopped"
    assert key not in mgr.monitors
    # Give the event loop one tick to process the cancellation request
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_list_wave_scanners_only_wave_keys():
    """list_wave_scanners returns only entries prefixed 'wave:'."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()
    # Inject a fake divergence monitor (non-wave key)
    fake_task = asyncio.create_task(asyncio.sleep(1000))
    mgr.monitors["AAPL:rsi:hidden_bullish"] = {
        "task": fake_task, "tickers": ["AAPL"],
        "indicator": "rsi", "signal_type": "hidden_bullish",
    }

    mgr.start_wave_scanner(["AAPL"], interval="5m")
    wave_list = mgr.list_wave_scanners()

    assert all(k.startswith("wave:") for k in wave_list)
    assert "wave:5m:AAPL" in wave_list
    # Cleanup
    fake_task.cancel()
    wave_key = "wave:5m:AAPL"
    if wave_key in mgr.monitors:
        mgr.monitors[wave_key]["task"].cancel()
    try:
        await fake_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_start_wave_scanner_duplicate_returns_already_running():
    """Starting the same scanner twice returns already_running."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()

    mgr.start_wave_scanner(["AAPL"], interval="5m")
    result = mgr.start_wave_scanner(["AAPL"], interval="5m")

    assert result["status"] == "already_running"
    # Cleanup
    key = mgr._wave_key(["AAPL"], "5m")
    if key in mgr.monitors:
        mgr.monitors[key]["task"].cancel()
        try:
            await mgr.monitors[key]["task"]
        except (asyncio.CancelledError, Exception):
            pass


def test_start_wave_scanner_invalid_interval():
    """start_wave_scanner rejects intervals not in INTRADAY_INTERVALS."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()

    result = mgr.start_wave_scanner(["AAPL"], interval="3d")

    assert result["status"] == "error"
    assert "interval" in result["message"].lower()


def test_stop_wave_scanner_not_found():
    """stop_wave_scanner for non-existent key returns not_found."""
    mgr = MonitorManager()
    mgr.provider = _mock_provider()

    result = mgr.stop_wave_scanner(["ZZZZ"], interval="5m")
    assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# IntradayWaveScanner — on_bar callback behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_bar_skips_unknown_symbol():
    """on_bar is a no-op for symbols not in the scanner's list."""
    scanner = IntradayWaveScanner(["AAPL"], interval="5m")

    with patch("app.services.alerts.intraday.compute_labeling") as mock_cl:
        await scanner.on_bar(_mock_bar(symbol="TSLA"))
        mock_cl.assert_not_called()


@pytest.mark.asyncio
async def test_on_bar_runs_compute_labeling_in_thread():
    """on_bar calls compute_labeling via asyncio.to_thread (non-blocking)."""
    scanner = IntradayWaveScanner(["AAPL"], interval="5m")

    with patch("app.services.alerts.intraday.asyncio.to_thread",
               new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = None  # lab is None → early exit
        await scanner.on_bar(_mock_bar(symbol="AAPL"))
        mock_thread.assert_called_once()
        # First arg to to_thread should be compute_labeling
        assert mock_thread.call_args[0][0].__name__ == "compute_labeling"


@pytest.mark.asyncio
async def test_on_bar_debounce_skips_same_wave_direction():
    """on_bar does not fire broadcast twice for the same (wave, direction)."""
    from unittest.mock import AsyncMock as AM
    fired = []

    async def cb(alert):
        fired.append(alert)

    scanner = IntradayWaveScanner(["AAPL"], interval="5m", broadcast_cb=cb,
                                   min_probability=0.0, min_risk_reward=0.0)

    mock_alert = MagicMock()
    mock_alert.current_wave = "3"
    mock_alert.direction = "long"
    mock_alert.probability = 0.8
    mock_alert.risk_reward = 3.0

    mock_lab = MagicMock()
    mock_lab.primary = MagicMock()

    with (
        patch("app.services.alerts.intraday.asyncio.to_thread",
              new_callable=AsyncMock, return_value=mock_lab),
        patch("app.services.alerts.intraday._labeling_to_state", return_value=MagicMock()),
        patch("app.services.alerts.intraday.build_alert", return_value=mock_alert),
    ):
        await scanner.on_bar(_mock_bar(symbol="AAPL"))
        await scanner.on_bar(_mock_bar(symbol="AAPL"))  # same wave/direction

    assert len(fired) == 1, f"expected 1 broadcast, got {len(fired)}"


@pytest.mark.asyncio
async def test_on_bar_fires_again_when_wave_changes():
    """on_bar fires again when the active wave changes."""
    fired = []

    async def cb(alert):
        fired.append(alert)

    scanner = IntradayWaveScanner(["AAPL"], interval="5m", broadcast_cb=cb,
                                   min_probability=0.0, min_risk_reward=0.0)

    mock_lab = MagicMock()
    mock_lab.primary = MagicMock()

    def _make_alert(wave):
        a = MagicMock()
        a.current_wave = wave
        a.direction = "long"
        a.probability = 0.8
        a.risk_reward = 3.0
        return a

    with (
        patch("app.services.alerts.intraday.asyncio.to_thread",
              new_callable=AsyncMock, return_value=mock_lab),
        patch("app.services.alerts.intraday._labeling_to_state", return_value=MagicMock()),
        patch("app.services.alerts.intraday.build_alert",
              side_effect=[_make_alert("3"), _make_alert("5")]),
    ):
        await scanner.on_bar(_mock_bar(symbol="AAPL"))  # wave 3 fires
        await scanner.on_bar(_mock_bar(symbol="AAPL"))  # wave 5 fires (different)

    assert len(fired) == 2, f"expected 2 broadcasts, got {len(fired)}"
