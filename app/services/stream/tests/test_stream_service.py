"""
Unit tests for StreamService — the post-FE-CONTRACTS-4 owner of Schwab
subscriptions + the `stream_universe` CH table.

The CH repo methods (`_read_universe`, `_is_active`, `_write_row`) are
patched to drive off an in-memory fake; the provider is the same FakeDataProvider used in
the watchlist tests. This isolates the subscription state machine
from ClickHouse / Schwab.

Integration coverage (real CH, real provider) lives behind the
`integration` marker in test_stream_service_integration.py (TBD).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
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

class FakeAudit:
    """Stands in for `audit_run(frequent=True)`: records every cycle's
    (job_name, result, error) without touching ClickHouse."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, object, Optional[str]]] = []

    @contextlib.asynccontextmanager
    async def __call__(self, job_name: str):
        rec = SimpleNamespace(result=None)
        try:
            yield rec
        except Exception as exc:
            self.runs.append((job_name, rec.result, repr(exc)))
            raise
        else:
            self.runs.append((job_name, rec.result, None))


async def _wait_until(pred: Callable[[], bool], *, timeout: float = 3.0) -> None:
    """Poll `pred` until true or fail the test — the re-sync loop runs on a
    timer, so tests observe it rather than driving it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("condition not reached within timeout")
        await asyncio.sleep(0.01)


# ----- Fixture -----


@pytest.fixture
def make_svc(monkeypatch) -> Callable[..., StreamService]:
    """Factory: build a StreamService whose CH reads/writes hit an in-memory
    repo and whose provider is a FakeDataProvider. `universe_resync_seconds`
    defaults to 0 (loop off) so the CRUD tests stay timer-free; the re-sync
    tests pass a short interval + a FakeAudit."""
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
    # Futures universe is a separate CH table; these tests cover the
    # equities universe logic, so stub it to empty.
    monkeypatch.setattr(
        StreamService, "_read_futures_universe",
        lambda self, *, owner_id=None, strict=False: set(),
    )
    monkeypatch.setattr(
        StreamService, "_write_row",
        lambda self, sym, owner, is_active, *, asset_type="", added_by="", notes="":
            fake_repo.write(sym, is_active, asset_type=asset_type, added_by=added_by, notes=notes),
    )
    # Patch provider acquisition so the service never calls Schwab.
    monkeypatch.setattr(stream_module, "get_stream_provider", lambda: fake_prov)
    # The re-sync gate reads the configured provider name; pin it so the
    # tests pass identically whatever DATA_PROVIDER / STREAM_PROVIDER the
    # machine has in its environment.
    monkeypatch.setattr(stream_module.settings, "stream_provider", "fake")

    def _build(*, universe_resync_seconds: float = 0, audit=None) -> StreamService:
        s = StreamService(
            universe_resync_seconds=universe_resync_seconds,
            audit_factory=audit,
        )
        s._provider = fake_prov
        s._fake_repo = fake_repo  # type: ignore[attr-defined]
        s._fake_prov = fake_prov  # type: ignore[attr-defined]
        return s

    return _build


@pytest.fixture
def svc(make_svc) -> StreamService:
    return make_svc()


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


@pytest.mark.asyncio
async def test_start_propagates_universe_read_failure(
    svc: StreamService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*, owner_id=None):
        raise RuntimeError("ClickHouse unavailable")

    monkeypatch.setattr(svc, "_read_universe", _fail)

    with pytest.raises(RuntimeError, match="ClickHouse unavailable"):
        await svc.start()

    assert svc._started is False


def test_list_active_symbols_propagates_universe_read_failure(
    svc: StreamService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*, owner_id=None):
        raise RuntimeError("ClickHouse unavailable")

    monkeypatch.setattr(svc, "_read_universe", _fail)

    with pytest.raises(RuntimeError, match="ClickHouse unavailable"):
        svc.list_active_symbols()


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


# ----- periodic universe re-sync (deployment spec C2) -----
#
# The worker process must pick up rows written by ANOTHER process (the api
# container's watchlist add) without a restart. The loop is observed, not
# driven: a 20 ms interval + _wait_until.


@pytest.mark.asyncio
async def test_resync_subscribes_row_added_after_start(make_svc) -> None:
    audit = FakeAudit()
    s = make_svc(universe_resync_seconds=0.02, audit=audit)
    s._fake_repo.write("AAPL", 1)
    await s.start()
    assert s.universe_resync_active is True
    assert s._fake_prov.subscribed == {"AAPL"}

    # Another process writes the row; this process never called add().
    s._fake_repo.write("NVDA", 1)
    await _wait_until(lambda: "NVDA" in s._fake_prov.subscribed)

    assert s._subscribed == {"AAPL", "NVDA"}
    assert s.is_streaming("NVDA")
    # The cycle that did the work was audited with the +1 / -0 summary.
    assert ("stream_universe_resync", {"subscribed": 1, "unsubscribed": 0}, None) in audit.runs
    await s.stop()


@pytest.mark.asyncio
async def test_resync_unsubscribes_row_marked_inactive(make_svc) -> None:
    audit = FakeAudit()
    s = make_svc(universe_resync_seconds=0.02, audit=audit)
    s._fake_repo.write("AAPL", 1)
    s._fake_repo.write("TSLA", 1)
    await s.start()
    assert s._fake_prov.subscribed == {"AAPL", "TSLA"}

    s._fake_repo.write("TSLA", 0)  # operator removed it elsewhere
    await _wait_until(lambda: "TSLA" not in s._fake_prov.subscribed)

    assert s._subscribed == {"AAPL"}
    assert ["TSLA"] in s._fake_prov.unsubscribe_calls
    assert ("stream_universe_resync", {"subscribed": 0, "unsubscribed": 1}, None) in audit.runs
    await s.stop()


@pytest.mark.asyncio
async def test_resync_noop_cycle_reports_zero_zero(make_svc) -> None:
    """A cycle with nothing to do still returns a result (logged outcome,
    including zero) — the audit layer is what collapses it to a heartbeat."""
    s = make_svc(universe_resync_seconds=0)
    s._fake_repo.write("AAPL", 1)
    await s.start()
    assert await s.resync_universe() == {"subscribed": 0, "unsubscribed": 0}
    assert s._fake_prov.subscribe_calls == [["AAPL"]]  # start() only
    await s.stop()


@pytest.mark.asyncio
async def test_resync_loop_not_started_without_provider(make_svc, monkeypatch) -> None:
    """The api container (no stream provider) must not run the loop."""
    monkeypatch.setattr(stream_module.settings, "stream_provider", "")
    monkeypatch.setattr(stream_module.settings, "data_provider", "")
    assert stream_module.settings.effective_stream_provider == ""

    s = make_svc(universe_resync_seconds=0.02, audit=FakeAudit())
    await s.start()
    assert s._started is True
    assert s._resync_task is None
    assert s.universe_resync_active is False
    await s.stop()


@pytest.mark.asyncio
async def test_resync_loop_not_started_when_interval_zero(make_svc) -> None:
    s = make_svc(universe_resync_seconds=0)
    await s.start()
    assert s._resync_task is None
    assert s.universe_resync_active is False
    await s.stop()


@pytest.mark.asyncio
async def test_resync_read_failure_logs_and_next_cycle_runs(
    make_svc, monkeypatch, caplog,
) -> None:
    audit = FakeAudit()
    s = make_svc(universe_resync_seconds=0.02, audit=audit)
    s._fake_repo.write("AAPL", 1)
    await s.start()

    calls = {"n": 0}
    real_read = s._read_universe

    def _flaky_read(*, owner_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ClickHouse unavailable")
        return real_read(owner_id=owner_id)

    monkeypatch.setattr(s, "_read_universe", _flaky_read)
    s._fake_repo.write("NVDA", 1)

    with caplog.at_level(logging.ERROR, logger="app.services.stream.service"):
        await _wait_until(lambda: "NVDA" in s._fake_prov.subscribed)

    # Cycle 1 raised, was logged with the error, and left the set untouched.
    failed = [r for r in audit.runs if r[2] is not None]
    assert failed and "ClickHouse unavailable" in failed[0][2]
    assert any(
        "cycle 1 failed" in rec.getMessage() and "ClickHouse unavailable" in rec.getMessage()
        for rec in caplog.records
    )
    # A later cycle succeeded and did the subscribe.
    assert ("stream_universe_resync", {"subscribed": 1, "unsubscribed": 0}, None) in audit.runs
    assert s.universe_resync_active is True
    await s.stop()


@pytest.mark.asyncio
async def test_resync_futures_read_failure_raises_and_changes_nothing(
    make_svc, monkeypatch,
) -> None:
    """An unreadable futures_universe must NOT be applied as 'no futures':
    that would unsubscribe every live root. Strict read → cycle fails."""
    s = make_svc(universe_resync_seconds=0)
    s._fake_repo.write("AAPL", 1)
    monkeypatch.setattr(
        StreamService, "_read_futures_universe",
        lambda self, *, owner_id=None, strict=False: {"/ES"},
    )
    await s.start()
    assert s._subscribed == {"AAPL", "/ES"}

    def _futures_down(self, *, owner_id=None, strict=False):
        if strict:
            raise RuntimeError("futures_universe unreadable")
        return set()

    monkeypatch.setattr(StreamService, "_read_futures_universe", _futures_down)
    with pytest.raises(RuntimeError, match="futures_universe unreadable"):
        await s.resync_universe()

    assert s._subscribed == {"AAPL", "/ES"}
    assert s._fake_prov.unsubscribe_calls == []
    assert not s._lock.locked()  # released on the error path
    await s.stop()


@pytest.mark.asyncio
async def test_resync_skips_when_not_started(make_svc) -> None:
    s = make_svc()
    assert await s.resync_universe() == {"skipped": True, "reason": "not_started"}
    assert s._fake_prov.subscribe_calls == []


@pytest.mark.asyncio
async def test_resync_skips_when_provider_unavailable(make_svc, monkeypatch) -> None:
    s = make_svc()
    s._fake_repo.write("AAPL", 1)
    await s.start()
    s._fake_repo.write("NVDA", 1)

    s._provider = None
    monkeypatch.setattr(
        stream_module, "get_stream_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("no creds")),
    )
    assert await s.resync_universe() == {"skipped": True, "reason": "provider_unavailable"}
    assert s._subscribed == {"AAPL"}  # not silently marked as subscribed
    s._provider = s._fake_prov
    await s.stop()


@pytest.mark.asyncio
async def test_resync_skips_when_lock_busy(make_svc, monkeypatch) -> None:
    """A route thread mid-add() holds the lock; the cycle must give up, not
    block the event loop (that would deadlock the thread's provider call)."""
    s = make_svc()
    s._fake_repo.write("AAPL", 1)
    await s.start()
    monkeypatch.setattr(StreamService, "RESYNC_LOCK_WAIT_SECONDS", 0.1)

    s._fake_repo.write("NVDA", 1)
    assert s._lock.acquire(blocking=False)
    try:
        assert await s.resync_universe() == {"skipped": True, "reason": "lock_busy"}
    finally:
        s._lock.release()
    assert "NVDA" not in s._subscribed

    # Lock free again → the same cycle succeeds.
    assert await s.resync_universe() == {"subscribed": 1, "unsubscribed": 0}
    await s.stop()


@pytest.mark.asyncio
async def test_stop_cancels_resync_loop(make_svc) -> None:
    s = make_svc(universe_resync_seconds=0.02, audit=FakeAudit())
    await s.start()
    task = s._resync_task
    assert task is not None and not task.done()

    await s.stop()

    assert s._resync_task is None
    assert task.done()
    assert s.universe_resync_active is False
