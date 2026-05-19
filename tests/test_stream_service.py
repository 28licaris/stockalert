"""
Unit tests for StreamService — the post-FE-CONTRACTS-4 owner of Schwab
subscriptions + the `stream_universe` CH table.

The CH repo methods (`_read_universe`, `_is_active`, `_write_row`,
`bootstrap_if_empty`, `is_empty`) are patched to drive off an
in-memory fake; the provider is the same FakeDataProvider used in
the watchlist tests. This isolates the subscription state machine
from ClickHouse / Schwab.

Integration coverage (real CH, real provider) lives behind the
`integration` marker in test_stream_service_integration.py (TBD).
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

import pandas as pd
import pytest

from app.providers.base import DataProvider
from app.services.stream import service as stream_module
from app.services.stream.service import StreamService


# ----- Fakes -----


class FakeDataProvider(DataProvider):
    """In-process provider that records subscribe/unsubscribe calls."""

    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self.subscribe_calls: list[list[str]] = []
        self.unsubscribe_calls: list[list[str]] = []
        self.stopped = False
        self._callback: Optional[Callable] = None

    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        self.stopped = True

    def subscribe_bars(self, callback, tickers: list[str]) -> None:
        self._callback = callback
        for t in tickers:
            self.subscribed.add(t)
        self.subscribe_calls.append(list(tickers))

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        for t in tickers:
            self.subscribed.discard(t)
        self.unsubscribe_calls.append(list(tickers))

    async def historical_df(self, symbol, start, end, timeframe="1Min"):
        return pd.DataFrame()


class FakeUniverseRepo:
    """In-memory shim for the stream_universe table CRUD."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}  # symbol -> {asset_type, added_at, added_by, notes, is_active}

    def list_active(self) -> list[dict]:
        return [
            {
                "symbol": sym,
                "asset_type": r["asset_type"],
                "added_at": r["added_at"],
                "added_by": r["added_by"],
                "notes": r["notes"],
            }
            for sym, r in self.rows.items()
            if r["is_active"]
        ]

    def is_active(self, symbol: str) -> bool:
        r = self.rows.get(symbol)
        return bool(r and r["is_active"])

    def write(self, symbol: str, is_active: int, *, asset_type: str = "", added_by: str = "", notes: str = "") -> None:
        self.rows[symbol] = {
            "asset_type": asset_type,
            "added_at": "2026-05-19T00:00:00Z",
            "added_by": added_by,
            "notes": notes,
            "is_active": bool(is_active),
        }

    def is_empty(self) -> bool:
        return not any(r["is_active"] for r in self.rows.values())


# ----- Fixture -----


@pytest.fixture
def svc(monkeypatch) -> StreamService:
    fake_repo = FakeUniverseRepo()
    fake_prov = FakeDataProvider()

    # Patch CH-touching methods so the StreamService never tries to
    # reach a real ClickHouse during these unit tests.
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
    monkeypatch.setattr(
        StreamService, "is_empty",
        lambda self, *, owner_id=None: fake_repo.is_empty(),
    )
    monkeypatch.setattr(
        StreamService, "bootstrap_if_empty",
        lambda self, *, owner_id=None: (False, 0),
    )

    # Patch provider acquisition so the service never calls Schwab.
    monkeypatch.setattr(stream_module, "get_stream_provider", lambda: fake_prov)

    s = StreamService()
    s._provider = fake_prov
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    s._fake_prov = fake_prov  # type: ignore[attr-defined]
    return s


# ----- start subscribes everything in the universe -----


@pytest.mark.asyncio
async def test_start_subscribes_to_active_universe(svc: StreamService) -> None:
    svc._fake_repo.write("AAPL", 1)
    svc._fake_repo.write("MSFT", 1)
    svc._fake_repo.write("GOOGL", 0)  # inactive — should not be subscribed

    await svc.start()

    assert svc._fake_prov.subscribed == {"AAPL", "MSFT"}
    assert svc._subscribed == {"AAPL", "MSFT"}
    assert svc._started is True


@pytest.mark.asyncio
async def test_start_is_idempotent(svc: StreamService) -> None:
    svc._fake_repo.write("AAPL", 1)
    await svc.start()
    await svc.start()  # second call must not double-subscribe
    assert svc._fake_prov.subscribe_calls == [["AAPL"]]


# ----- add subscribes immediately -----


@pytest.mark.asyncio
async def test_add_subscribes_new_symbol(svc: StreamService) -> None:
    await svc.start()
    result = svc.add("NVDA")
    assert result["changed"] == ["NVDA"]
    assert "NVDA" in svc._fake_prov.subscribed
    assert "NVDA" in svc._subscribed


@pytest.mark.asyncio
async def test_add_already_active_is_noop(svc: StreamService) -> None:
    svc._fake_repo.write("NVDA", 1)
    await svc.start()
    assert "NVDA" in svc._fake_prov.subscribed

    initial_subs = list(svc._fake_prov.subscribe_calls)
    result = svc.add("NVDA")
    assert result["changed"] == []  # idempotent
    # No new subscribe call.
    assert svc._fake_prov.subscribe_calls == initial_subs


def test_add_normalizes_symbol(svc: StreamService) -> None:
    result = svc.add("  aapl  ")
    assert "AAPL" in svc._fake_repo.rows
    assert result["changed"] == ["AAPL"]


def test_add_rejects_empty_after_normalization(svc: StreamService) -> None:
    with pytest.raises(ValueError):
        svc.add("   ")


# ----- remove unsubscribes immediately -----


@pytest.mark.asyncio
async def test_remove_unsubscribes_symbol(svc: StreamService) -> None:
    svc._fake_repo.write("TSLA", 1)
    await svc.start()
    assert "TSLA" in svc._fake_prov.subscribed

    result = svc.remove("TSLA")
    assert result["changed"] == ["TSLA"]
    assert "TSLA" not in svc._fake_prov.subscribed
    assert "TSLA" not in svc._subscribed


@pytest.mark.asyncio
async def test_remove_inactive_is_noop(svc: StreamService) -> None:
    await svc.start()
    result = svc.remove("AAPL")
    assert result["changed"] == []
    assert svc._fake_prov.unsubscribe_calls == []


# ----- ensure_streaming auto-extend (watchlist hook) -----


@pytest.mark.asyncio
async def test_ensure_streaming_adds_missing_symbols(svc: StreamService) -> None:
    svc._fake_repo.write("AAPL", 1)
    await svc.start()

    added = svc.ensure_streaming(["AAPL", "MSFT"], source="watchlist:test")
    assert added == ["MSFT"]  # AAPL was already active
    assert "MSFT" in svc._fake_prov.subscribed
    # AAPL's notes are unchanged (no second add call).
    assert svc._fake_repo.rows["AAPL"]["notes"] == ""
    # MSFT carries the source tag.
    assert "watchlist:test" in svc._fake_repo.rows["MSFT"]["notes"]


# ----- is_streaming reflects the live subscription set -----


@pytest.mark.asyncio
async def test_is_streaming_after_add_and_remove(svc: StreamService) -> None:
    await svc.start()
    assert svc.is_streaming("XYZ") is False
    svc.add("XYZ")
    assert svc.is_streaming("XYZ") is True
    svc.remove("XYZ")
    assert svc.is_streaming("XYZ") is False


# ----- stop unsubscribes everything -----


@pytest.mark.asyncio
async def test_stop_unsubscribes_everything(svc: StreamService) -> None:
    svc._fake_repo.write("A", 1)
    svc._fake_repo.write("B", 1)
    await svc.start()
    assert svc._fake_prov.subscribed == {"A", "B"}

    await svc.stop()
    assert svc._fake_prov.subscribed == set()
    assert svc._fake_prov.stopped is True


# ----- public provider handle (regression for FE-CONTRACTS-4 autocomplete bug) -----


@pytest.mark.asyncio
async def test_get_provider_returns_none_before_start(svc: StreamService) -> None:
    """Routes (e.g. /api/v1/instruments/search) must be able to ask for
    the provider handle and get None safely when StreamService hasn't
    been started yet. Falling back to None is the contract; if this
    breaks the autocomplete dropdown silently returns empty results."""
    fresh = StreamService()
    # Don't start. Don't touch _provider directly.
    assert fresh.get_provider() is None


@pytest.mark.asyncio
async def test_get_provider_returns_initialized_provider_after_start(
    svc: StreamService,
) -> None:
    """After start(), get_provider() must return the same handle that
    StreamService used to subscribe Schwab — so other modules (routes_
    instruments) reuse the authenticated session instead of constructing
    their own."""
    svc._fake_repo.write("AAPL", 1)
    await svc.start()
    provider = svc.get_provider()
    assert provider is not None
    assert provider is svc._provider  # same handle
    assert provider is svc._fake_prov  # the fixture's fake


# ----- worker-thread loop routing (regression for the "add via API does
#       not actually subscribe Schwab" bug) -----


@pytest.mark.asyncio
async def test_add_from_worker_thread_routes_subscribe_through_main_loop(
    svc: StreamService,
) -> None:
    """REGRESSION: API routes call `stream_service.add` via
    `asyncio.to_thread`, leaving the worker thread without a running
    event loop. The provider's `subscribe_bars` needs the loop to
    register the WS callback, and the warmup `create_task` likewise
    needs one. Before the fix, both silently failed and brand-new
    symbols never reached Schwab.

    The contract: when called from a worker thread, the service must
    route subscribe + warmup back to the captured main loop via
    `run_coroutine_threadsafe`. The `add` call itself returns success;
    Schwab actually receives the subscribe call.
    """
    # Start the service in this asyncio context — captures _main_loop.
    await svc.start()
    assert svc._main_loop is not None

    # Now invoke `add` from a real worker thread (matches the API path:
    # `await asyncio.to_thread(stream_service.add, ...)`).
    result = await asyncio.to_thread(svc.add, "BRAND_NEW")

    assert result["changed"] == ["BRAND_NEW"]
    # The fake provider records subscribe calls. The bug was that this
    # set stayed empty because the call silently bailed in the thread.
    assert "BRAND_NEW" in svc._fake_prov.subscribed
    assert ["BRAND_NEW"] in svc._fake_prov.subscribe_calls


@pytest.mark.asyncio
async def test_remove_from_worker_thread_routes_unsubscribe_through_main_loop(
    svc: StreamService,
) -> None:
    """Symmetric to the add case — `remove` called from a worker
    thread must reach the provider's `unsubscribe_bars`."""
    svc._fake_repo.write("TARGET", 1)
    await svc.start()
    assert "TARGET" in svc._fake_prov.subscribed

    result = await asyncio.to_thread(svc.remove, "TARGET")

    assert result["changed"] == ["TARGET"]
    assert "TARGET" not in svc._fake_prov.subscribed
    assert ["TARGET"] in svc._fake_prov.unsubscribe_calls


@pytest.mark.asyncio
async def test_add_from_worker_thread_without_start_skips_cleanly(
    svc: StreamService,
) -> None:
    """If `add` is called from a worker thread BEFORE start() captured
    the main loop, the subscribe is logged-and-skipped rather than
    raising — the CH row still gets written so a later run can recover."""
    # Don't call start. _main_loop stays None.
    assert svc._main_loop is None

    # Call from a worker thread (no running loop, no captured main).
    result = await asyncio.to_thread(svc.add, "EARLY")
    # The CH row was still written.
    assert result["changed"] == ["EARLY"]
    # But Schwab was NOT subscribed (no loop to route through).
    assert "EARLY" not in svc._fake_prov.subscribed


# ----- status() shape -----


@pytest.mark.asyncio
async def test_status_returns_expected_keys(svc: StreamService) -> None:
    svc._fake_repo.write("A", 1)
    await svc.start()

    st = svc.status()
    assert st["started"] is True
    assert "provider" in st
    assert st["streaming_count"] == 1
    assert st["streaming_symbols"] == ["A"]
    assert st["universe_count"] == 1
    assert st["provider_ready"] is True
