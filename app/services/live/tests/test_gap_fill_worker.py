"""Tests for the read-path provider gap-fill fallback in gap_fill_worker.

Drives `_run_fill` directly (synchronous) rather than via the daemon
thread, so the two-tier behaviour is deterministic:

  1. Lake → CH fill runs first (ground truth).
  2. Only when the lake fill comes up EMPTY does the Schwab provider
     tip-fill fire — and only for equities, when `symbol_gapfill_enabled`.

Spec: docs/symbol_onboarding_read_design.md §3.3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.live import gap_fill_worker as gfw

UTC = timezone.utc
_START = datetime(2024, 6, 1, tzinfo=UTC)
_END = datetime(2024, 6, 30, tzinfo=UTC)


@pytest.fixture
def tip_calls(monkeypatch):
    """Capture provider tip-fill invocations + make lake-fill yield configurable."""
    calls: list[str] = []

    class _FakeTip:
        @classmethod
        def from_settings(cls):
            return cls()

        async def tip_fill(self, symbol):
            calls.append(symbol)
            return SimpleNamespace(
                bars_fetched=10, bars_written_bronze=10, bars_written_ch=10,
            )

    monkeypatch.setattr(
        "app.services.ingest.schwab_tip_fill.SchwabTipFill", _FakeTip,
    )

    def _set_lake_yield(rows: int):
        # bars_gateway._lake_fill_fn(symbol) -> fill_fn(symbol, start, end) -> inserted
        monkeypatch.setattr(
            "app.services.readers.bars_gateway._lake_fill_fn",
            lambda symbol: (lambda s, a, b: rows),
        )

    return calls, _set_lake_yield


def test_provider_fill_fires_when_lake_empty(tip_calls, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "symbol_gapfill_enabled", True)
    calls, set_lake_yield = tip_calls
    set_lake_yield(0)  # lake had nothing → detected gap

    gfw._run_fill("AAPL", _START, _END)

    assert calls == ["AAPL"]


def test_provider_fill_skipped_when_lake_covered(tip_calls, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "symbol_gapfill_enabled", True)
    calls, set_lake_yield = tip_calls
    set_lake_yield(500)  # lake served the window → no provider needed

    gfw._run_fill("AAPL", _START, _END)

    assert calls == []


def test_provider_fill_skipped_when_disabled(tip_calls, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "symbol_gapfill_enabled", False)
    calls, set_lake_yield = tip_calls
    set_lake_yield(0)

    gfw._run_fill("AAPL", _START, _END)

    assert calls == []


def test_provider_fill_skipped_for_futures(tip_calls, monkeypatch):
    """Futures provider gap-fill is a follow-up — never fire it in v1."""
    from app.config import settings
    monkeypatch.setattr(settings, "symbol_gapfill_enabled", True)
    calls, set_lake_yield = tip_calls
    set_lake_yield(0)

    gfw._run_fill("/ES", _START, _END)

    assert calls == []
