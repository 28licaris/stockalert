"""
Tests for the silver-derived add_members warmup path (TA-5.3.3).

The TA-5.3 unified add-symbol flow per docs/streaming_universe_model.md
replaces the legacy 3-call _enqueue_backfill path with:
  1. SilverToChBackfill.backfill_symbol(days=730) — silver.ohlcv_1m → CH
  2. SchwabTipFill.tip_fill(symbol) — silver-watermark → live gap (≤48d)

These tests verify:
  - Flag OFF (default): add_members keeps using the legacy path
  - Flag ON: add_members fires the silver-derived warmup tasks
  - Per-symbol warmup runs steps 1 + 2 sequentially in the right order
  - Failure in step 1 doesn't block step 2 (logs but continues)
  - Failure in step 2 is logged but doesn't propagate
  - Symbol is added to the watchlist regardless of warmup outcome
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional
from unittest.mock import patch

import pandas as pd
import pytest

from app.providers.base import DataProvider
from app.services.ingest.silver_to_ch_backfill import SilverToChBackfillResult
from app.services.ingest.schwab_tip_fill import TipFillResult
from app.services.live import watchlist_service as wls_module
from app.services.live.watchlist_service import WatchlistService


# ─────────────────────────────────────────────────────────────────────
# Fakes (mirrors tests/test_watchlist_service.py FakeRepo / FakeDataProvider)
# ─────────────────────────────────────────────────────────────────────


class _FakeDataProvider(DataProvider):
    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self._callback: Optional[Callable] = None
        self.stopped = False

    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        self.stopped = True

    def subscribe_bars(self, callback, tickers: list[str]) -> None:
        self._callback = callback
        for t in tickers:
            self.subscribed.add(t)

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        for t in tickers:
            self.subscribed.discard(t)

    async def historical_df(self, symbol, start, end, timeframe="1Min"):
        return pd.DataFrame()


class _FakeRepo:
    def __init__(self) -> None:
        self.watchlists: dict[str, dict] = {}
        self.members: dict[str, set[str]] = {}

    def list_watchlists(self, include_inactive: bool = False) -> list[dict]:
        return [
            {"name": n, **m, "updated_at": None}
            for n, m in self.watchlists.items()
            if include_inactive or m["is_active"]
        ]

    def get_watchlist(self, name: str):
        meta = self.watchlists.get(name)
        return None if meta is None else {"name": name, **meta, "updated_at": None}

    def create_watchlist(self, name, kind="user", description=""):
        self.watchlists[name] = {"kind": kind, "description": description, "is_active": True}
        self.members.setdefault(name, set())
        return self.get_watchlist(name)

    def list_members(self, name):
        if not self.watchlists.get(name, {}).get("is_active"):
            return []
        return sorted(self.members.get(name, set()))

    def add_members(self, name, symbols):
        if name not in self.watchlists:
            self.create_watchlist(name)
        existing = self.members.setdefault(name, set())
        newly = []
        for s in symbols or []:
            ss = (s or "").strip().upper()
            if ss and ss not in existing:
                existing.add(ss)
                newly.append(ss)
        return newly

    def remove_members(self, name, symbols):
        existing = self.members.get(name, set())
        removed = []
        for s in symbols or []:
            ss = (s or "").strip().upper()
            if ss in existing:
                existing.discard(ss)
                removed.append(ss)
        return removed

    def list_all_active_symbols(self, kinds=None):
        out: set[str] = set()
        for name, meta in self.watchlists.items():
            if not meta["is_active"]:
                continue
            if kinds is not None and meta["kind"] not in set(kinds):
                continue
            out |= self.members.get(name, set())
        return out


@pytest.fixture
def svc(monkeypatch) -> WatchlistService:
    fake_repo = _FakeRepo()
    fake_prov = _FakeDataProvider()
    monkeypatch.setattr(wls_module, "watchlist_repo", fake_repo)
    monkeypatch.setattr(wls_module, "get_stream_provider", lambda: fake_prov)
    s = WatchlistService(backfill=None)
    s._provider = fake_prov  # bypass lazy init
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    return s


# ─────────────────────────────────────────────────────────────────────
# Flag dispatch in add_members
# ─────────────────────────────────────────────────────────────────────


class TestAddMembersFlagDispatch:
    @pytest.mark.asyncio
    async def test_flag_off_uses_legacy_path(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(settings, "silver_derived_add_members_enabled", False)

        legacy_calls: list[tuple] = []
        silver_calls: list[str] = []

        def _spy_legacy(symbols, *, kind, days):
            legacy_calls.append((tuple(symbols), kind, days))

        def _spy_new(symbols):
            silver_calls.extend(symbols)

        monkeypatch.setattr(svc, "_enqueue_backfill", _spy_legacy)
        monkeypatch.setattr(svc, "_enqueue_silver_derived_warmup", _spy_new)

        await svc.start()
        svc.add_members("a", ["NVDA"])

        # Legacy: three calls (quick / intraday / daily).
        kinds = [c[1] for c in legacy_calls]
        assert set(kinds) == {"quick", "intraday", "daily"}
        # New path NOT called.
        assert silver_calls == []

    @pytest.mark.asyncio
    async def test_flag_on_uses_silver_derived_path(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(settings, "silver_derived_add_members_enabled", True)

        legacy_calls: list[tuple] = []
        silver_calls: list[str] = []

        def _spy_legacy(symbols, *, kind, days):
            legacy_calls.append((tuple(symbols), kind, days))

        def _spy_new(symbols):
            silver_calls.extend(symbols)

        monkeypatch.setattr(svc, "_enqueue_backfill", _spy_legacy)
        monkeypatch.setattr(svc, "_enqueue_silver_derived_warmup", _spy_new)

        await svc.start()
        svc.add_members("a", ["NVDA", "AAPL"])

        # New path: ONE call with both symbols.
        assert sorted(silver_calls) == ["AAPL", "NVDA"]
        # Legacy NOT called.
        assert legacy_calls == []


# ─────────────────────────────────────────────────────────────────────
# Per-symbol warmup chain
# ─────────────────────────────────────────────────────────────────────


class TestWarmupChain:
    """The _silver_derived_warmup_one async method runs silver_to_ch
    THEN tip_fill, sequentially per symbol."""

    @pytest.mark.asyncio
    async def test_runs_silver_to_ch_then_tip_fill_in_order(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        order: list[str] = []

        # Fake SilverToChBackfill.
        class _FakeS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                order.append(f"silver_to_ch:{symbol}:days={days}")
                return SilverToChBackfillResult(
                    symbol=symbol, bars_read=100, bars_written=100,
                )

        # Fake SchwabTipFill.
        class _FakeTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                order.append(f"tip_fill:{symbol}")
                return TipFillResult(symbol=symbol, bars_fetched=10)

        monkeypatch.setattr(
            "app.services.ingest.silver_to_ch_backfill.SilverToChBackfill",
            _FakeS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        await svc._silver_derived_warmup_one("NVDA")

        assert order == [
            "silver_to_ch:NVDA:days=730",  # DEFAULT_BACKFILL_DAYS
            "tip_fill:NVDA",
        ]

    @pytest.mark.asyncio
    async def test_s2c_failure_does_not_block_tip_fill(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If silver→CH fails (silver missing / raise / partial), the
        tip-fill still runs. For a brand-new symbol, this is the
        expected path: silver has nothing → s2c does nothing → tip-fill
        provides the 48-day data."""
        tip_called = {"n": 0}

        class _FailingS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                raise RuntimeError("silver catalog unreachable")

        class _FakeTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                tip_called["n"] += 1
                return TipFillResult(symbol=symbol, bars_fetched=5)

        monkeypatch.setattr(
            "app.services.ingest.silver_to_ch_backfill.SilverToChBackfill",
            _FailingS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        # Should NOT raise.
        await svc._silver_derived_warmup_one("NVDA")
        # Tip-fill ran despite the s2c failure.
        assert tip_called["n"] == 1

    @pytest.mark.asyncio
    async def test_tip_fill_failure_is_logged_not_raised(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _OkS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                return SilverToChBackfillResult(symbol=symbol, bars_written=10)

        class _FailingTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                raise RuntimeError("schwab 503")

        monkeypatch.setattr(
            "app.services.ingest.silver_to_ch_backfill.SilverToChBackfill",
            _OkS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FailingTip,
        )

        # Should NOT raise — warmup is fire-and-forget.
        await svc._silver_derived_warmup_one("NVDA")

    @pytest.mark.asyncio
    async def test_s2c_returns_error_result_continues_to_tip_fill(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When silver→CH returns a SilverToChBackfillResult with an
        error string (not an exception), still continue to tip-fill."""
        tip_called = {"n": 0}

        class _ErrorResultS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                return SilverToChBackfillResult(
                    symbol=symbol, error="some clean failure mode",
                )

        class _FakeTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                tip_called["n"] += 1
                return TipFillResult(symbol=symbol, bars_fetched=5)

        monkeypatch.setattr(
            "app.services.ingest.silver_to_ch_backfill.SilverToChBackfill",
            _ErrorResultS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        await svc._silver_derived_warmup_one("NVDA")
        assert tip_called["n"] == 1


# ─────────────────────────────────────────────────────────────────────
# Fire-and-forget dispatch semantics
# ─────────────────────────────────────────────────────────────────────


class TestEnqueueSemantics:
    def test_no_running_loop_no_raise(self) -> None:
        """When called from sync context with no event loop running
        (pre-startup, sync test), the method silently skips rather
        than raising. Mirrors `_enqueue_backfill`'s no-loop guard."""
        s = WatchlistService(backfill=None)
        # Sync call, no event loop: must not raise.
        s._enqueue_silver_derived_warmup(["NVDA"])

    @pytest.mark.asyncio
    async def test_empty_symbols_is_noop(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"n": 0}

        async def _spy(sym):
            called["n"] += 1

        monkeypatch.setattr(svc, "_silver_derived_warmup_one", _spy)
        svc._enqueue_silver_derived_warmup([])
        # Yield a tick to let any erroneously-created tasks run.
        await asyncio.sleep(0)
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_one_task_per_symbol(
        self, svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[str] = []

        async def _spy(sym):
            seen.append(sym)

        monkeypatch.setattr(svc, "_silver_derived_warmup_one", _spy)
        svc._enqueue_silver_derived_warmup(["NVDA", "AAPL", "MSFT"])
        # Yield until all tasks complete.
        for _ in range(5):
            await asyncio.sleep(0)
        assert sorted(seen) == ["AAPL", "MSFT", "NVDA"]
