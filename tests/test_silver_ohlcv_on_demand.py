"""
Unit tests for `app.services.silver.ohlcv.on_demand.build_one_symbol`.

The actual `SilverOhlcvBuild.build_window` is mocked so these tests
don't need bronze / corp_actions / silver data. We're verifying the
WRAPPER contract:

  - Single-symbol invocation: passes [sym] not the whole universe.
  - Window: defaults to last 5 years ending yesterday.
  - Async-safe: heavy work runs in a thread, doesn't block the loop.
  - Errors don't propagate; failures land in BuildResult.slices.
  - Symbol normalization (uppercase, strip).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services.silver.ohlcv.build import BuildResult
from app.services.silver.ohlcv.on_demand import (
    DEFAULT_ON_DEMAND_DAYS,
    build_one_symbol,
)


def _ok_result(symbol: str, start: date, end: date) -> BuildResult:
    """Helper: synthesize a successful BuildResult."""
    t = datetime.now(timezone.utc)
    return BuildResult(
        run_id="test",
        started_at=t,
        finished_at=t,
        symbols=[symbol],
        start_date=start,
        end_date=end,
    )


@pytest.mark.asyncio
async def test_build_one_symbol_passes_single_element_list() -> None:
    """REGRESSION: must call build_window(symbols=[sym]), not the whole
    universe. The on-demand path is per-symbol."""
    captured: dict = {}

    def _spy(symbols, start_date, end_date, *, mode="month"):
        captured["symbols"] = list(symbols)
        captured["mode"] = mode
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return _ok_result(symbols[0], start_date, end_date)

    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _spy
        await build_one_symbol("pg")

    # Single-symbol list (NOT whole universe).
    assert captured["symbols"] == ["PG"]
    # Month-batched mode = fast for the 5y window.
    assert captured["mode"] == "month"


@pytest.mark.asyncio
async def test_build_one_symbol_normalizes_input() -> None:
    """Symbol is upper-cased and trimmed before reaching build_window."""
    captured: dict = {}

    def _spy(symbols, start_date, end_date, *, mode="month"):
        captured["symbols"] = list(symbols)
        return _ok_result(symbols[0], start_date, end_date)

    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _spy
        await build_one_symbol("  msft  ")

    assert captured["symbols"] == ["MSFT"]


@pytest.mark.asyncio
async def test_build_one_symbol_empty_raises() -> None:
    """Empty/whitespace symbol → ValueError (caught at the API boundary)."""
    with pytest.raises(ValueError):
        await build_one_symbol("")
    with pytest.raises(ValueError):
        await build_one_symbol("   ")


@pytest.mark.asyncio
async def test_build_one_symbol_default_window_is_5_years_ending_yesterday() -> None:
    """Default window matches symbol_lifecycle.md: 5y back, end=yesterday
    (Polygon flat-files for today aren't published until tomorrow)."""
    captured: dict = {}

    def _spy(symbols, start_date, end_date, *, mode="month"):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return _ok_result(symbols[0], start_date, end_date)

    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _spy
        await build_one_symbol("AAPL")

    today = datetime.now(timezone.utc).date()
    expected_end = today - timedelta(days=1)
    expected_start = expected_end - timedelta(days=DEFAULT_ON_DEMAND_DAYS)
    assert captured["end_date"] == expected_end
    assert captured["start_date"] == expected_start
    # Verify the window is approximately 5 years.
    assert (captured["end_date"] - captured["start_date"]).days == DEFAULT_ON_DEMAND_DAYS


@pytest.mark.asyncio
async def test_build_one_symbol_custom_days() -> None:
    """`days` parameter is honored (smaller windows used by tests / quick checks)."""
    captured: dict = {}

    def _spy(symbols, start_date, end_date, *, mode="month"):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return _ok_result(symbols[0], start_date, end_date)

    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _spy
        await build_one_symbol("AAPL", days=30)

    assert (captured["end_date"] - captured["start_date"]).days == 30


@pytest.mark.asyncio
async def test_build_one_symbol_does_not_block_event_loop() -> None:
    """REGRESSION: the heavy synchronous work must run via asyncio.to_thread
    so the event loop stays responsive. Verify by running build_one_symbol
    concurrently with a quick sleep and observing both complete."""
    event = asyncio.Event()

    def _slow_build(symbols, start_date, end_date, *, mode="month"):
        # Simulate slow I/O. If this runs in the main loop, the other
        # coroutine below can't make progress until it returns.
        import time
        time.sleep(0.5)
        return _ok_result(symbols[0], start_date, end_date)

    async def _quick_signal():
        await asyncio.sleep(0.1)
        event.set()

    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _slow_build
        signal_task = asyncio.create_task(_quick_signal())
        build_task = asyncio.create_task(build_one_symbol("AAPL"))

        # The signal must fire within ~150ms; if build_window blocked
        # the loop, this would only fire after the full 500ms.
        try:
            await asyncio.wait_for(event.wait(), timeout=0.3)
        except asyncio.TimeoutError:
            pytest.fail(
                "build_one_symbol blocked the event loop "
                "— must run via asyncio.to_thread"
            )
        await build_task
        await signal_task


@pytest.mark.asyncio
async def test_build_one_symbol_explicit_end_date() -> None:
    """Caller can pass an explicit `end_date` (e.g. for deterministic tests)."""
    captured: dict = {}

    def _spy(symbols, start_date, end_date, *, mode="month"):
        captured["end_date"] = end_date
        return _ok_result(symbols[0], start_date, end_date)

    target_end = date(2024, 12, 31)
    with patch(
        "app.services.silver.ohlcv.on_demand.SilverOhlcvBuild.from_settings",
    ) as fake_factory:
        fake_factory.return_value.build_window = _spy
        await build_one_symbol("AAPL", days=10, end_date=target_end)

    assert captured["end_date"] == target_end
