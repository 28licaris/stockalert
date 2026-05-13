"""
Unit tests for app.services.backfill_service.BackfillService.

We exercise the service against:
  - a `FakeLoader` that records every `load_bars` call and returns a fixed
    number of dummy rows (so we can assert "how many fetches", "what window",
    etc.),
  - an injected `coverage_fn` so we can simulate "DB already has X bars"
    without touching ClickHouse,
  - an injected `now_fn` so windows are deterministic.

No ClickHouse, no provider; pure logic tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import pytest

from app.services.backfill_service import BackfillService


# ---------- Fakes ----------


class FakeLoader:
    """
    Replaces the parts of HistoricalDataLoader that BackfillService actually
    touches: `_fetch_from_provider` (where the provider call would happen)
    and `_persist` is monkey-patched away on the service itself.
    """

    def __init__(self, bars_per_call: int = 1000) -> None:
        self.bars_per_call = bars_per_call
        self.calls: list[dict] = []

    async def _fetch_from_provider(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        self.calls.append({"symbol": symbol, "start": start, "end": end})
        # `_persist` walks `df.iterrows()` and reads OHLCV columns. Return a
        # DataFrame with a DatetimeIndex so that `ts.tzinfo` works as expected
        # in the real `_persist` (we patch around it in tests though).
        idx = pd.date_range(start=start, periods=self.bars_per_call, freq="1min", tz=timezone.utc)
        return pd.DataFrame(
            {
                "open": [1.0] * self.bars_per_call,
                "high": [1.0] * self.bars_per_call,
                "low": [1.0] * self.bars_per_call,
                "close": [1.0] * self.bars_per_call,
                "volume": [1.0] * self.bars_per_call,
            },
            index=idx,
        )


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------- Fixtures ----------


@pytest.fixture
def fixed_now():
    return _ts(2026, 5, 12, 17)


@pytest.fixture
def empty_coverage_fn():
    async def _fn(symbol, start, end):
        return {
            "symbol": symbol.upper(),
            "start": start,
            "end": end,
            "earliest": None,
            "latest": None,
            "bar_count": 0,
        }
    return _fn


def make_coverage_fn(bar_count: int, earliest: Optional[datetime], latest: Optional[datetime]):
    async def _fn(symbol, start, end):
        return {
            "symbol": symbol.upper(),
            "start": start,
            "end": end,
            "earliest": earliest,
            "latest": latest,
            "bar_count": bar_count,
        }
    return _fn


async def _noop_persist(self, symbol, df):  # bound replacement for BackfillService._persist
    return None


@pytest.fixture(autouse=True)
def _patch_persist(monkeypatch):
    """All tests: skip ClickHouse writes inside BackfillService."""
    monkeypatch.setattr(BackfillService, "_persist", _noop_persist)


@pytest.fixture(autouse=True)
def _disable_flatfiles_dispatch_by_default(monkeypatch):
    """
    The deep dispatch reads live ``settings`` to decide whether to route
    through Polygon Flat Files. Local dev configs may have flat-files
    enabled, which would silently divert the REST-path tests below to
    flat-files and break them. Force the dispatch off for every test by
    default; the flat-files tests at the bottom of this file opt back in
    explicitly via their own fixture.
    """
    import app.services.backfill_service as bs_mod
    monkeypatch.setattr(
        bs_mod.settings, "polygon_flatfiles_enabled", False, raising=False,
    )


# ---------- Quick path ----------


@pytest.mark.asyncio
async def test_quick_short_circuits_when_coverage_is_high(fixed_now, empty_coverage_fn) -> None:
    # 30 days * 5/7 trading days * 390 min ~= 8357 expected; supply 9000 (>90%).
    cov_fn = make_coverage_fn(bar_count=9000, earliest=fixed_now - timedelta(days=30), latest=fixed_now)
    loader = FakeLoader(bars_per_call=500)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    await asyncio.sleep(0)  # let the task tick
    await asyncio.sleep(0)
    # Drain any pending background tasks for this symbol.
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]
    assert st["quick"]["state"] == "skipped"
    assert "already" in (st["quick"]["reason"] or "")
    # Loader should not have been called - we short-circuited.
    assert loader.calls == []


@pytest.mark.asyncio
async def test_quick_fetches_when_coverage_is_low(fixed_now) -> None:
    cov_fn = make_coverage_fn(bar_count=10, earliest=fixed_now - timedelta(hours=2), latest=fixed_now)
    loader = FakeLoader(bars_per_call=8000)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for _ in range(3):
        await asyncio.sleep(0)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]
    assert st["quick"]["state"] == "done"
    assert st["quick"]["bars"] == 8000
    assert len(loader.calls) == 1
    # 30-day window relative to fixed_now
    assert loader.calls[0]["end"] == fixed_now
    assert loader.calls[0]["start"] == fixed_now - timedelta(days=30)


@pytest.mark.asyncio
async def test_enqueue_quick_is_idempotent(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader(bars_per_call=1)
    # Add a tiny artificial delay so the first task is observably still running.
    original = loader._fetch_from_provider

    async def slow_load(*a, **kw):
        await asyncio.sleep(0.05)
        return await original(*a, **kw)

    loader._fetch_from_provider = slow_load  # type: ignore[assignment]

    svc = BackfillService(loader=loader, coverage_fn=empty_coverage_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    first = svc.enqueue_quick("AAPL", days=30)
    # Yield to let the task progress past `state=queued` into the slow load.
    await asyncio.sleep(0)
    second = svc.enqueue_quick("AAPL", days=30)  # should NOT spawn a second task
    assert first["state"] == "queued"
    assert second["state"] in ("queued", "running")
    assert second.get("reason") == "already running" or first == second

    for task in list(svc._tasks.values()):
        await task

    # Only one underlying loader call despite two enqueues.
    assert len(loader.calls) == 1


# ---------- Deep path ----------


@pytest.mark.asyncio
async def test_deep_chunks_full_window_when_db_is_empty(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader(bars_per_call=2000)
    svc = BackfillService(
        loader=loader,  # type: ignore[arg-type]
        coverage_fn=empty_coverage_fn,
        now_fn=lambda: fixed_now,
        chunk_days=9,
    )

    svc.enqueue_deep("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "done"
    # 30 days / 9-day chunks -> 4 chunks (9, 9, 9, 3)
    assert st["chunks_total"] == 4
    assert st["chunks_done"] == 4
    assert len(loader.calls) == 4
    # Walk chunks: each load_bars call has both `start` and `end` set (range mode).
    for call in loader.calls:
        assert call["start"] is not None
        assert call["end"] is not None
        span_days = (call["end"] - call["start"]).total_seconds() / 86400.0
        assert span_days <= 9.0 + 1e-6
    # Chunks should tile [fixed_now-30d, fixed_now] without overlap or gap
    # (the loader walks newest-to-oldest).
    sorted_calls = sorted(loader.calls, key=lambda c: c["start"])
    earliest = sorted_calls[0]["start"]
    latest = sorted_calls[-1]["end"]
    assert earliest == fixed_now - timedelta(days=30)
    assert latest == fixed_now


@pytest.mark.asyncio
async def test_deep_skipped_when_history_already_covers_target(fixed_now) -> None:
    # DB already has bars going back further than the target window.
    cov_fn = make_coverage_fn(
        bar_count=12345,
        earliest=fixed_now - timedelta(days=400),
        latest=fixed_now,
    )
    loader = FakeLoader(bars_per_call=1000)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_deep("AAPL", days=365)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "skipped"
    assert "no gap" in (st["reason"] or "")
    assert loader.calls == []


@pytest.mark.asyncio
async def test_deep_fetches_only_missing_gap(fixed_now) -> None:
    # DB has data starting 100 days ago. Target is 365 days. Gap = days 100..365.
    earliest_in_db = fixed_now - timedelta(days=100)
    cov_fn = make_coverage_fn(bar_count=50000, earliest=earliest_in_db, latest=fixed_now)
    loader = FakeLoader(bars_per_call=2000)
    svc = BackfillService(
        loader=loader,  # type: ignore[arg-type]
        coverage_fn=cov_fn,
        now_fn=lambda: fixed_now,
        chunk_days=30,  # bigger chunk so the test is fast
    )

    svc.enqueue_deep("AAPL", days=365)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["deep"]
    assert st["state"] == "done"
    # Gap is 265 days, chunked by 30 days -> ceil(265/30) = 9 chunks.
    assert st["chunks_total"] == 9
    assert st["chunks_done"] == 9
    assert len(loader.calls) == 9

    # Highest `end` should be the existing earliest (we filled the gap backwards).
    max_end = max(c["end"] for c in loader.calls)
    min_start = min(c["start"] for c in loader.calls)
    assert max_end == earliest_in_db
    assert min_start == fixed_now - timedelta(days=365)


# ---------- Misc ----------


@pytest.mark.asyncio
async def test_coverage_report_shape(fixed_now) -> None:
    cov_fn = make_coverage_fn(
        bar_count=1000,
        earliest=fixed_now - timedelta(days=5),
        latest=fixed_now,
    )
    loader = FakeLoader()
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    report = await svc.coverage("AAPL", days=30)
    assert report["symbol"] == "AAPL"
    assert report["window_days"] == 30
    assert report["bar_count"] == 1000
    assert isinstance(report["expected_approx"], int)
    assert 0.0 <= report["ratio"] <= 1.0


@pytest.mark.asyncio
async def test_empty_symbol_is_an_error(fixed_now, empty_coverage_fn) -> None:
    loader = FakeLoader()
    svc = BackfillService(loader=loader, coverage_fn=empty_coverage_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]
    result = svc.enqueue_quick("", days=30)
    assert result["state"] == "error"
    assert "empty" in result["error"]


# ---------- Intraday (5m) path ----------


class FakeProvider:
    """Stand-in for `loader.provider` used by the intraday + daily paths."""
    def __init__(self, df_to_return) -> None:
        self._df = df_to_return
        self.calls: list[dict] = []

    async def historical_df(self, symbol, start, end, *, timeframe: str = "1Min"):
        self.calls.append({"symbol": symbol, "start": start, "end": end, "timeframe": timeframe})
        return self._df


def _make_5m_df(start: datetime, count: int) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=count, freq="5min", tz=timezone.utc)
    return pd.DataFrame(
        {
            "open": [1.0] * count,
            "high": [1.0] * count,
            "low": [1.0] * count,
            "close": [1.0] * count,
            "volume": [1.0] * count,
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_intraday_fetches_when_5m_table_empty(fixed_now, monkeypatch) -> None:
    # Empty 5m coverage -> backfill fires.
    async def empty_5m_cov(symbol, start, end):
        return {"symbol": symbol.upper(), "start": start, "end": end,
                "earliest": None, "latest": None, "bar_count": 0}
    monkeypatch.setattr("app.db.queries.coverage_5m", lambda *a, **k: None)
    # Patch the async helper used by _execute_intraday
    from app.db import queries as q
    monkeypatch.setattr(q, "coverage_5m", lambda sym, s, e: {
        "symbol": sym.upper(), "start": s, "end": e,
        "earliest": None, "latest": None, "bar_count": 0,
    })

    df = _make_5m_df(fixed_now - timedelta(days=270), count=100)
    provider = FakeProvider(df)
    loader = FakeLoader()
    loader.provider = provider  # type: ignore[attr-defined]
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    # Stub the 5m persist so we don't touch ClickHouse from a unit test.
    persisted: list[int] = []

    async def fake_persist_5m(self, symbol, df):
        persisted.append(len(df))

    monkeypatch.setattr(BackfillService, "_persist_5m", fake_persist_5m)

    svc.enqueue_intraday("AAPL", days=270)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["intraday"]
    assert st["state"] == "done"
    assert st["bars"] == 100
    assert len(provider.calls) == 1
    assert provider.calls[0]["timeframe"] == "5m"
    # Provider should have been asked for the full 270d window.
    assert provider.calls[0]["start"] == fixed_now - timedelta(days=270)
    assert provider.calls[0]["end"] == fixed_now
    assert persisted == [100]


@pytest.mark.asyncio
async def test_intraday_skipped_when_5m_table_already_covers(fixed_now, monkeypatch) -> None:
    from app.db import queries as q
    # Existing coverage older than target start - should short-circuit.
    monkeypatch.setattr(q, "coverage_5m", lambda sym, s, e: {
        "symbol": sym.upper(), "start": s, "end": e,
        "earliest": fixed_now - timedelta(days=280),
        "latest": fixed_now,
        "bar_count": 9999,
    })

    provider = FakeProvider(_make_5m_df(fixed_now, count=1))
    loader = FakeLoader()
    loader.provider = provider  # type: ignore[attr-defined]
    svc = BackfillService(loader=loader, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_intraday("AAPL", days=270)
    for task in list(svc._tasks.values()):
        await task

    st = svc.status("AAPL")["AAPL"]["intraday"]
    assert st["state"] == "skipped"
    assert "already covers" in (st["reason"] or "")
    # Provider should NOT have been called.
    assert provider.calls == []


# ---------- Throttle ----------


@pytest.mark.asyncio
async def test_quick_throttle_blocks_repeat_enqueue(fixed_now, empty_coverage_fn) -> None:
    """Once a quick job completes, a second enqueue within the throttle window returns 'throttled'."""
    cov_fn = make_coverage_fn(bar_count=0, earliest=None, latest=None)
    loader = FakeLoader(bars_per_call=500)

    # Advanceable clock so we can simulate "later".
    now_ref = {"t": fixed_now}
    svc = BackfillService(
        loader=loader, coverage_fn=cov_fn, now_fn=lambda: now_ref["t"],
    )  # type: ignore[arg-type]

    # First run: executes normally.
    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task
    assert svc.status("AAPL")["AAPL"]["quick"]["state"] == "done"
    first_call_count = len(loader.calls)
    assert first_call_count == 1

    # Second enqueue 1 minute later: throttled (quick cooldown = 4h).
    now_ref["t"] = fixed_now + timedelta(minutes=1)
    result = svc.enqueue_quick("AAPL", days=30)
    assert result["state"] == "throttled"
    assert "cooldown" in result["reason"]
    # No new in-flight task, no new loader call.
    assert (("AAPL", "quick") not in svc._tasks
            or svc._tasks[("AAPL", "quick")].done())
    assert len(loader.calls) == first_call_count


@pytest.mark.asyncio
async def test_throttle_expires_after_cooldown(fixed_now, empty_coverage_fn) -> None:
    """After the cooldown window elapses, the enqueue runs again."""
    cov_fn = make_coverage_fn(bar_count=0, earliest=None, latest=None)
    loader = FakeLoader(bars_per_call=500)
    now_ref = {"t": fixed_now}
    svc = BackfillService(
        loader=loader, coverage_fn=cov_fn, now_fn=lambda: now_ref["t"],
    )  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task
    assert len(loader.calls) == 1

    # Jump 5 hours forward (quick cooldown is 4h).
    now_ref["t"] = fixed_now + timedelta(hours=5)
    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task
    # Should have done a second fetch now.
    assert len(loader.calls) == 2


@pytest.mark.asyncio
async def test_force_bypasses_throttle(fixed_now, empty_coverage_fn) -> None:
    """force=True ignores the cooldown."""
    cov_fn = make_coverage_fn(bar_count=0, earliest=None, latest=None)
    loader = FakeLoader(bars_per_call=500)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task
    assert len(loader.calls) == 1

    # Immediate retry with force=True must run.
    svc.enqueue_quick("AAPL", days=30, force=True)
    for task in list(svc._tasks.values()):
        await task
    assert len(loader.calls) == 2


@pytest.mark.asyncio
async def test_throttle_per_kind_is_independent(fixed_now, empty_coverage_fn) -> None:
    """A quick throttle does NOT prevent a deep/daily/intraday from running."""
    cov_fn = make_coverage_fn(bar_count=0, earliest=None, latest=None)
    loader = FakeLoader(bars_per_call=500)
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    # Now deep should still be runnable - different kind, separate cooldown.
    result = svc.enqueue_deep("AAPL", days=365)
    assert result["state"] == "queued"


@pytest.mark.asyncio
async def test_error_state_does_not_set_cooldown(fixed_now, empty_coverage_fn) -> None:
    """A failed job must NOT record completion (so retries aren't blocked)."""
    cov_fn = make_coverage_fn(bar_count=0, earliest=None, latest=None)
    loader = FakeLoader(bars_per_call=500)

    async def boom(symbol, start, end):
        loader.calls.append({"symbol": symbol, "start": start, "end": end})
        raise RuntimeError("provider exploded")

    loader._fetch_from_provider = boom  # type: ignore[assignment]
    svc = BackfillService(loader=loader, coverage_fn=cov_fn, now_fn=lambda: fixed_now)  # type: ignore[arg-type]

    svc.enqueue_quick("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task
    assert svc.status("AAPL")["AAPL"]["quick"]["state"] == "error"

    # Immediate retry must NOT be throttled (no completion recorded).
    result = svc.enqueue_quick("AAPL", days=30)
    assert result["state"] == "queued"


def test_seconds_until_next_sweep_picks_next_06_utc() -> None:
    """Pure-function test for the sweeper schedule."""
    svc = BackfillService(now_fn=lambda: datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc))
    # 10:00 UTC -> next 06:00 UTC is tomorrow (~20h away)
    wait = svc._seconds_until_next_sweep()
    assert 19.5 * 3600 < wait <= 20 * 3600


def test_seconds_until_next_sweep_same_day_before_hour() -> None:
    """If we're before today's sweep hour, the next sweep is today, not tomorrow."""
    svc = BackfillService(now_fn=lambda: datetime(2026, 5, 12, 2, 0, tzinfo=timezone.utc))
    # 02:00 UTC -> next 06:00 UTC is today (4h away)
    wait = svc._seconds_until_next_sweep()
    assert 3.9 * 3600 < wait <= 4 * 3600


# ---------- Flat-files deep dispatch ----------


@pytest.fixture
def _ff_settings(monkeypatch):
    """Helper to set the env-var-driven flags ``_should_use_flatfiles_for_deep``
    checks. Returns a single ``apply(provider, enabled, key, secret)`` that
    callers use to express the test's intent compactly."""
    import app.services.backfill_service as bs_mod

    def apply(*, provider: str, enabled: bool, key: str = "k", secret: str = "s"):
        monkeypatch.setattr(bs_mod.settings, "history_provider", provider,
                            raising=False)
        # ``effective_history_provider`` is a @property on settings; we patch
        # its return value via the underlying ``history_provider`` /
        # ``data_provider`` fields used by the property.
        monkeypatch.setattr(bs_mod.settings, "data_provider", provider,
                            raising=False)
        monkeypatch.setattr(bs_mod.settings, "polygon_flatfiles_enabled",
                            enabled, raising=False)
        monkeypatch.setattr(bs_mod.settings, "polygon_s3_access_key_id",
                            key, raising=False)
        monkeypatch.setattr(bs_mod.settings, "polygon_s3_secret_access_key",
                            secret, raising=False)
    return apply


class _FakeFlatFilesResult:
    def __init__(self, *, bars=0, ok=0, filtered=0, missing=0, errored=0,
                 listed=0):
        self.bars_persisted = bars
        self.days_ok = ok
        self.days_filtered = filtered
        self.days_missing = missing
        self.days_errored = errored
        self.days_listed = listed


class _FakeFlatFilesService:
    """Mimics the surface ``BackfillService`` consumes: a single async
    ``backfill_range`` returning a result object with the same attribute
    names as ``BackfillRangeResult``."""
    def __init__(self, result: _FakeFlatFilesResult):
        self.result = result
        self.calls: list[dict] = []

    async def backfill_range(self, symbols, start, end, *, kind="minute",
                             dry_run=False, on_progress=None):
        self.calls.append({
            "symbols": list(symbols), "start": start, "end": end,
            "kind": kind, "dry_run": dry_run,
        })
        return self.result


def test_should_use_flatfiles_for_deep_requires_all_conditions(_ff_settings):
    """All three conditions must be true (provider + enabled + creds)."""
    _ff_settings(provider="polygon", enabled=True)
    assert BackfillService._should_use_flatfiles_for_deep() is True

    _ff_settings(provider="schwab", enabled=True)
    assert BackfillService._should_use_flatfiles_for_deep() is False

    _ff_settings(provider="polygon", enabled=False)
    assert BackfillService._should_use_flatfiles_for_deep() is False

    _ff_settings(provider="polygon", enabled=True, key="", secret="s")
    assert BackfillService._should_use_flatfiles_for_deep() is False

    _ff_settings(provider="polygon", enabled=True, key="k", secret="")
    assert BackfillService._should_use_flatfiles_for_deep() is False


@pytest.mark.asyncio
async def test_deep_routes_through_flatfiles_when_enabled(
    fixed_now, empty_coverage_fn, _ff_settings,
):
    _ff_settings(provider="polygon", enabled=True)
    fake_ff = _FakeFlatFilesService(
        _FakeFlatFilesResult(bars=2690, ok=1, listed=1),
    )
    svc = BackfillService(
        coverage_fn=empty_coverage_fn,
        now_fn=lambda: fixed_now,
        flatfiles_service=fake_ff,
    )
    svc.enqueue_deep("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    assert len(fake_ff.calls) == 1
    call = fake_ff.calls[0]
    assert call["symbols"] == ["AAPL"]
    assert call["kind"] == "minute"
    # Window endpoints are date-typed and inclusive.
    assert call["start"] == (fixed_now - timedelta(days=30)).date()
    assert call["end"] == fixed_now.date()

    status = svc.status("AAPL")["AAPL"]["deep"]
    assert status["state"] == "done"
    assert status["bars"] == 2690
    assert "flat-files:" in (status.get("reason") or "")


@pytest.mark.asyncio
async def test_deep_skipped_when_flatfiles_path_already_covers_target(
    fixed_now, _ff_settings,
):
    _ff_settings(provider="polygon", enabled=True)
    # DB already has bars going further back than target_start, so the
    # flat-files path must short-circuit WITHOUT calling backfill_range.
    cov_fn = make_coverage_fn(
        bar_count=5000,
        earliest=fixed_now - timedelta(days=400),  # older than 365d target
        latest=fixed_now,
    )
    fake_ff = _FakeFlatFilesService(_FakeFlatFilesResult())
    svc = BackfillService(
        coverage_fn=cov_fn, now_fn=lambda: fixed_now,
        flatfiles_service=fake_ff,
    )
    svc.enqueue_deep("AAPL", days=365)
    for task in list(svc._tasks.values()):
        await task

    assert fake_ff.calls == []  # never invoked
    status = svc.status("AAPL")["AAPL"]["deep"]
    assert status["state"] == "skipped"
    assert status["bars"] == 5000


@pytest.mark.asyncio
async def test_deep_falls_back_to_rest_when_flatfiles_unavailable(
    fixed_now, empty_coverage_fn, _ff_settings,
):
    """If the flat-files service can't be built (boto3 missing, creds
    unreachable, etc.) ``_get_flatfiles_service`` returns ``None`` and the
    deep dispatch falls through to the REST chunked path. The contract is
    "never raise from the lazy build"; this test asserts the fallthrough."""
    _ff_settings(provider="polygon", enabled=True)
    loader = FakeLoader(bars_per_call=100)
    svc = BackfillService(
        loader=loader, coverage_fn=empty_coverage_fn,
        now_fn=lambda: fixed_now,
    )
    # Simulate "boto3 import failed" or "from_settings() raised" the way
    # the real helper handles it — swallowed exception, ``None`` return.
    svc._get_flatfiles_service = lambda: None  # type: ignore[method-assign]

    svc.enqueue_deep("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    # The REST loader saw the chunked deep calls -> fallback fired.
    assert len(loader.calls) > 0
    assert svc.status("AAPL")["AAPL"]["deep"]["state"] in ("done", "error")


@pytest.mark.asyncio
async def test_deep_records_errored_days_as_error_state(
    fixed_now, empty_coverage_fn, _ff_settings,
):
    _ff_settings(provider="polygon", enabled=True)
    fake_ff = _FakeFlatFilesService(
        _FakeFlatFilesResult(bars=900, ok=2, errored=1, listed=3),
    )
    svc = BackfillService(
        coverage_fn=empty_coverage_fn,
        now_fn=lambda: fixed_now,
        flatfiles_service=fake_ff,
    )
    svc.enqueue_deep("AAPL", days=30)
    for task in list(svc._tasks.values()):
        await task

    status = svc.status("AAPL")["AAPL"]["deep"]
    assert status["state"] == "error"
    assert "1 day" in (status.get("error") or "")
    # Bars still recorded so the user sees partial progress.
    assert status["bars"] == 900
