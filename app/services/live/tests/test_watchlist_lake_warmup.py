"""
Tests for the silver-derived warmup path (TA-5.3.3) — now owned by
StreamService.

The TA-5.3 unified add-symbol flow per docs/streaming_universe_model.md
replaces the legacy 3-call _enqueue_backfill path with:
  1. LakeToChBackfill.backfill_symbol(days=730) — silver.ohlcv_1m → CH
  2. SchwabTipFill.tip_fill(symbol) — silver-watermark → live gap (≤48d)

Post-FE-CONTRACTS-4-finalisation this lives on `StreamService`
(see docs/frontend_api_contracts.md §10.1 locked sticky-universe
model). WatchlistService's `add_members` auto-extends the stream
universe; if the flag is ON, StreamService.add fires the silver-
derived warmup. If the flag is OFF, WatchlistService falls back to
the legacy quick/intraday/daily backfill for the newly-added
symbols.

These tests verify:
  - Flag OFF: WatchlistService fires the legacy backfill path.
  - Flag ON: StreamService fires the silver-derived chain on add.
  - Per-symbol chain runs silver→CH THEN tip-fill in order.
  - Step-1 failure does not block step 2 (silver-missing path).
  - Step-2 failure is logged but does not raise.
  - The no-running-loop guard returns cleanly from sync calls.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

import pandas as pd
import pytest

from app.providers.base import DataProvider
from app.services.ingest.lake_to_ch_backfill import LakeToChBackfillResult
from app.services.ingest.schwab_tip_fill import TipFillResult
from app.services.live import watchlist_service as wls_module
from app.services.live.watchlist_service import WatchlistService
from app.services.stream import service as stream_module
from app.services.stream.service import StreamService


# ─────────────────────────────────────────────────────────────────────
# Fakes
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


class _FakeUniverseRepo:
    """Minimal in-memory shim for the stream_universe CH calls."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def list_active(self) -> list[dict]:
        return [
            {"symbol": sym, "asset_type": "", "added_at": "", "added_by": "", "notes": ""}
            for sym, r in self.rows.items()
            if r["is_active"]
        ]

    def is_active(self, sym: str) -> bool:
        r = self.rows.get(sym)
        return bool(r and r["is_active"])

    def write(self, sym: str, is_active: int, **kw) -> None:
        self.rows[sym] = {"is_active": bool(is_active), **kw}


# ─────────────────────────────────────────────────────────────────────
# Stream service fixture (subscription mechanics + warmup live here)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def stream_svc(monkeypatch) -> StreamService:
    fake_repo = _FakeUniverseRepo()
    fake_prov = _FakeDataProvider()

    monkeypatch.setattr(
        StreamService, "_read_universe",
        lambda self, *, owner_id=None: fake_repo.list_active(),
    )
    monkeypatch.setattr(
        StreamService, "_is_active",
        lambda self, sym, *, owner_id=None: fake_repo.is_active(sym),
    )
    monkeypatch.setattr(
        StreamService, "_write_row",
        lambda self, sym, owner, is_active, *, asset_type="", added_by="", notes="":
            fake_repo.write(sym, is_active, asset_type=asset_type, added_by=added_by, notes=notes),
    )
    monkeypatch.setattr(stream_module, "get_stream_provider", lambda: fake_prov)

    s = StreamService()
    s._provider = fake_prov
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    return s


# ─────────────────────────────────────────────────────────────────────
# Watchlist service fixture (flag-off legacy path runs here)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def wl_svc(monkeypatch) -> WatchlistService:
    fake_repo = _FakeRepo()
    monkeypatch.setattr(wls_module, "watchlist_repo", fake_repo)

    # Replace the lazy-imported stream_service inside add_members with
    # a no-op fake so the auto-extend hook doesn't try to reach CH.
    class _NoopStream:
        def ensure_streaming(self, symbols, **kw):
            return []

        def is_streaming(self, symbol):
            return False

        def status(self):
            return {}

    import app.services.stream as stream_pkg
    monkeypatch.setattr(stream_pkg, "stream_service", _NoopStream())

    s = WatchlistService(backfill=None)
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    return s


# ─────────────────────────────────────────────────────────────────────
# Flag dispatch: watchlist legacy backfill fires only when flag is OFF
# ─────────────────────────────────────────────────────────────────────


class TestAddMembersFlagDispatch:
    """The watchlist auto-extend path delegates streaming + silver
    warmup to StreamService. The watchlist itself only fires the
    legacy quick/intraday/daily backfill, and only when the flag is OFF.
    """

    def test_flag_off_fires_legacy_backfill(
        self, wl_svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(settings, "lake_warmup_enabled", False)

        legacy_calls: list[list[str]] = []
        monkeypatch.setattr(
            wl_svc, "_enqueue_warmup_legacy",
            lambda symbols: legacy_calls.append(list(symbols)),
        )

        wl_svc.add_members("a", ["NVDA", "AAPL"])
        # Legacy path got called once with both symbols.
        assert legacy_calls == [["NVDA", "AAPL"]]

    def test_flag_on_skips_legacy_backfill(
        self, wl_svc: WatchlistService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(settings, "lake_warmup_enabled", True)

        legacy_calls: list[list[str]] = []
        monkeypatch.setattr(
            wl_svc, "_enqueue_warmup_legacy",
            lambda symbols: legacy_calls.append(list(symbols)),
        )

        wl_svc.add_members("a", ["NVDA"])
        # Stream service owns warmup when flag is on — watchlist legacy NOT called.
        assert legacy_calls == []


# ─────────────────────────────────────────────────────────────────────
# Per-symbol warmup chain (now on StreamService)
# ─────────────────────────────────────────────────────────────────────


class TestWarmupChain:
    """The _lake_warmup_one async method runs lake_to_ch
    and tip_fill in PARALLEL (CV12). Pre-CV12 v1 ran silver_build
    || tip_fill then silver_to_ch sequentially; the v2 simplification
    drops the silver_build step (polygon_adjusted is whole-market
    pre-built by Spark) and parallelizes the two remaining writers."""

    @pytest.mark.asyncio
    async def test_runs_lake_to_ch_and_tip_fill_in_parallel(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called: set[str] = set()

        class _FakeS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                called.add(f"lake_to_ch:{symbol}:days={days}")
                return LakeToChBackfillResult(
                    symbol=symbol, bars_read=100, bars_written=100,
                )

        class _FakeTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                called.add(f"tip_fill:{symbol}")
                return TipFillResult(symbol=symbol, bars_fetched=10)

        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.LakeToChBackfill",
            _FakeS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        await stream_svc._lake_warmup_one("NVDA")

        # Both ran. Order is non-deterministic (asyncio.gather) so we
        # only assert the set, not the sequence.
        assert called == {
            "lake_to_ch:NVDA:days=730",  # DEFAULT_BACKFILL_DAYS
            "tip_fill:NVDA",
        }

    @pytest.mark.asyncio
    async def test_s2c_failure_does_not_block_tip_fill(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
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
            "app.services.ingest.lake_to_ch_backfill.LakeToChBackfill",
            _FailingS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        await stream_svc._lake_warmup_one("NVDA")
        assert tip_called["n"] == 1

    @pytest.mark.asyncio
    async def test_tip_fill_failure_is_logged_not_raised(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _OkS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                return LakeToChBackfillResult(symbol=symbol, bars_written=10)

        class _FailingTip:
            @classmethod
            def from_settings(cls):
                return cls()

            async def tip_fill(self, symbol):
                raise RuntimeError("schwab 503")

        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.LakeToChBackfill",
            _OkS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FailingTip,
        )

        # Fire-and-forget — must not raise.
        await stream_svc._lake_warmup_one("NVDA")

    @pytest.mark.asyncio
    async def test_s2c_returns_error_result_continues_to_tip_fill(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tip_called = {"n": 0}

        class _ErrorResultS2C:
            @classmethod
            def from_settings(cls):
                return cls()

            def backfill_symbol(self, symbol, *, days):
                return LakeToChBackfillResult(
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
            "app.services.ingest.lake_to_ch_backfill.LakeToChBackfill",
            _ErrorResultS2C,
        )
        monkeypatch.setattr(
            "app.services.ingest.schwab_tip_fill.SchwabTipFill",
            _FakeTip,
        )

        await stream_svc._lake_warmup_one("NVDA")
        assert tip_called["n"] == 1


# ─────────────────────────────────────────────────────────────────────
# Fire-and-forget dispatch semantics
# ─────────────────────────────────────────────────────────────────────


class TestEnqueueSemantics:
    def test_no_running_loop_no_raise(self) -> None:
        """When called from sync context with no event loop running,
        the method silently skips rather than raising."""
        s = StreamService()
        # Sync call, no event loop: must not raise.
        s._enqueue_warmup(["NVDA"])

    @pytest.mark.asyncio
    async def test_empty_symbols_is_noop(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"n": 0}

        async def _spy(sym):
            called["n"] += 1

        monkeypatch.setattr(stream_svc, "_lake_warmup_one", _spy)
        from app.config import settings
        monkeypatch.setattr(settings, "lake_warmup_enabled", True)
        stream_svc._enqueue_warmup([])
        await asyncio.sleep(0)
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_one_task_per_symbol(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[str] = []

        async def _spy(sym):
            seen.append(sym)

        monkeypatch.setattr(stream_svc, "_lake_warmup_one", _spy)
        from app.config import settings
        monkeypatch.setattr(settings, "lake_warmup_enabled", True)
        stream_svc._enqueue_warmup(["NVDA", "AAPL", "MSFT"])
        for _ in range(5):
            await asyncio.sleep(0)
        assert sorted(seen) == ["AAPL", "MSFT", "NVDA"]

    @pytest.mark.asyncio
    async def test_flag_off_is_noop(
        self, stream_svc: StreamService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When SILVER_DERIVED_ADD_MEMBERS_ENABLED is off, _enqueue_warmup
        skips scheduling entirely (the watchlist's legacy path is what
        handles backfill in that branch)."""
        seen: list[str] = []

        async def _spy(sym):
            seen.append(sym)

        monkeypatch.setattr(stream_svc, "_lake_warmup_one", _spy)
        from app.config import settings
        monkeypatch.setattr(settings, "lake_warmup_enabled", False)
        stream_svc._enqueue_warmup(["NVDA"])
        await asyncio.sleep(0)
        assert seen == []
