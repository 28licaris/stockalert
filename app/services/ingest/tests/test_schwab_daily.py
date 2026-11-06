"""``ensure_daily_history``: fetch and store only what is missing, lake first.

Every collaborator is a fake (Schwab provider, ClickHouse store, lake writer): no
network, no ClickHouse, no Iceberg.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from app.services.ingest import schwab_daily as mod
from app.services.ingest.schwab_daily import Coverage, LakeWriteResult, ensure_daily_history

TODAY = date(2026, 11, 6)          # yesterday = 2026-11-05
YESTERDAY = date(2026, 11, 5)


def _bars(start: date, end: date) -> pd.DataFrame:
    days = pd.bdate_range(start, end, tz="America/New_York")
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000.0}, index=days
    )


class FakeProvider:
    def __init__(self, empty=(), boom=()):
        self.calls: list[tuple[str, date, date]] = []
        self.empty, self.boom = set(empty), set(boom)

    async def historical_df(self, sym, start_dt, end_dt, timeframe="1d"):
        assert timeframe == "1d"
        s, e = start_dt.date(), (end_dt - timedelta(days=1)).date()
        self.calls.append((sym, s, e))
        if sym in self.boom:
            raise RuntimeError("schwab 500")
        if sym in self.empty:
            return pd.DataFrame()
        return _bars(s, e)


class FakeStore:
    def __init__(self, existing: dict[str, tuple[int, date, date]] | None = None, fail_insert=False):
        self.cov = {k: Coverage(*v) for k, v in (existing or {}).items()}
        self.inserted: list[list] = []
        self.fail_insert = fail_insert
        self.events: list[str] = []

    def coverage(self, symbols):
        return {s: self.cov[s] for s in symbols if s in self.cov}

    def insert(self, rows):
        self.events.append("ch")
        if self.fail_insert:
            raise RuntimeError("clickhouse down")
        self.inserted += rows
        for sym in {r[0] for r in rows}:
            mine = [r for r in rows if r[0] == sym]
            old = self.cov.get(sym, Coverage(0, None, None))
            days = [r[1].date() for r in mine]
            self.cov[sym] = Coverage(
                old.sessions + len(mine),
                min([d for d in days] + ([old.first] if old.first else [])),
                max([d for d in days] + ([old.last] if old.last else [])),
            )


def _writer(store: FakeStore, fail=False):
    batches: list[list[list]] = []

    async def write(batch, version):
        store.events.append("lake")
        if fail:
            return LakeWriteResult(0, 1, ("lake 2026-01-02: error — boom",))
        batches.append(batch)
        return LakeWriteResult(len(batch), 0)

    write.batches = batches  # type: ignore[attr-defined]
    return write


def run(symbols, store, provider=None, writer=None, **kw):
    provider = provider or FakeProvider()
    writer = writer or _writer(store)
    return asyncio.run(
        ensure_daily_history(symbols, provider=provider, store=store, lake_writer=writer,
                             today=TODAY, sleep_s=0, **kw)
    ), provider, writer


def test_new_symbol_fetches_full_horizon_and_writes_lake_before_clickhouse():
    store = FakeStore()
    (r,), provider, writer = run(["ccj"], store, years=1)
    assert r.status == "ok" and r.rows > 200
    sym, start, end = provider.calls[0]
    assert sym == "CCJ" and end == YESTERDAY and (YESTERDAY - start).days in (365, 366)
    assert store.events == ["lake", "ch"], "lake is ground truth: it must be written first"
    assert len(writer.batches[0]) == len(store.inserted) == r.rows
    assert all(row[0] == "CCJ" and row[1].hour == 14 and row[1].minute == 30 for row in store.inserted)


def test_already_covered_does_nothing():
    store = FakeStore({"SPY": (5000, date(2006, 1, 3), date(2026, 11, 4))})
    (r,), provider, writer = run(["SPY"], store)
    assert r.status == "already_covered"
    assert provider.calls == [] and store.events == []


def test_partial_history_only_fetches_before_the_earliest_stored_day():
    """The 56-session case: never re-fetch (and so never double-append to the
    non-deduplicating lake) days we already hold."""
    first = date(2026, 8, 12)
    store = FakeStore({"SCCO": (56, first, date(2026, 11, 2))})
    (r,), provider, _ = run(["SCCO"], store, years=1)
    assert r.status == "ok"
    _, start, end = provider.calls[0]
    assert end == first - timedelta(days=1)
    assert max(row[1].date() for row in store.inserted) < first
    assert store.cov["SCCO"].sessions >= 250  # now deep enough


def test_symbol_schwab_does_not_know_is_empty_and_writes_nothing():
    store = FakeStore()
    (r,), _, writer = run(["ZZZZ"], store, provider=FakeProvider(empty=["ZZZZ"]))
    assert r.status == "empty" and "no price history" in r.detail
    assert store.events == [] and writer.batches == []


def test_lake_failure_leaves_clickhouse_untouched():
    store = FakeStore()
    (r,), _, _ = run(["CCJ"], store, writer=_writer(store, fail=True))
    assert r.status == "lake_failed" and "ClickHouse not touched" in r.detail
    assert store.events == ["lake"] and store.inserted == []


def test_clickhouse_failure_after_lake_is_its_own_outcome():
    store = FakeStore(fail_insert=True)
    (r,), _, _ = run(["CCJ"], store)
    assert r.status == "ch_failed" and "lake written" in r.detail
    assert store.events == ["lake", "ch"]


def test_one_symbols_error_does_not_abort_the_others():
    store = FakeStore()
    rs, _, _ = run(["AAA", "BBB", "CCC"], store, provider=FakeProvider(boom=["BBB"]), years=1)
    assert [r.status for r in rs] == ["ok", "error", "ok"]
    assert "schwab 500" in rs[1].detail


def test_readback_mismatch_is_an_error_not_a_success():
    class LossyStore(FakeStore):
        def insert(self, rows):
            self.events.append("ch")  # accepts the call, stores nothing

    store = LossyStore()
    (r,), _, _ = run(["CCJ"], store, years=1)
    assert r.status == "error" and "readback mismatch" in r.detail


@pytest.mark.parametrize("bad", ["", "  ", "AA PL", "A;DROP", "/ES", "TOOLONGTICKER1", "$X"])
def test_invalid_tickers_are_rejected_without_any_io(bad):
    store = FakeStore()
    rs, provider, _ = run(["GOOD", bad], store, years=1)
    by = {r.symbol: r.status for r in rs}
    assert by["GOOD"] == "ok"
    if bad.strip():
        assert by[bad.strip().upper()] == "invalid"
    assert all(c[0] == "GOOD" for c in provider.calls)


def test_duplicates_and_case_are_normalised():
    store = FakeStore()
    rs, provider, _ = run(["ccj", "CCJ", " ccj "], store, years=1)
    assert [r.symbol for r in rs] == ["CCJ"] and len(provider.calls) == 1


def test_concurrent_requests_for_one_symbol_fetch_once():
    store = FakeStore()
    gate = asyncio.Event()

    class SlowProvider(FakeProvider):
        async def historical_df(self, *a, **k):
            await gate.wait()
            return await super().historical_df(*a, **k)

    async def both():
        provider = SlowProvider()
        writer = _writer(store)
        t1 = asyncio.create_task(ensure_daily_history(["CCJ"], provider=provider, store=store,
                                                      lake_writer=writer, today=TODAY, sleep_s=0, years=1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        t2 = asyncio.create_task(ensure_daily_history(["CCJ"], provider=provider, store=store,
                                                      lake_writer=writer, today=TODAY, sleep_s=0, years=1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set()
        return await t1, await t2, provider

    (r1,), (r2,), provider = asyncio.run(both())
    assert sorted([r1.status, r2.status]) == ["in_flight", "ok"]
    done = r1 if r1.status == "ok" else r2
    assert len(provider.calls) == 1, "the second request must not hit Schwab"
    assert len(store.inserted) == done.rows, "and must not write a second copy"


def test_in_flight_marker_is_released_after_failure():
    store = FakeStore()
    run(["CCJ"], store, provider=FakeProvider(boom=["CCJ"]), years=1)
    assert "CCJ" not in mod._inflight
    (r,), _, _ = run(["CCJ"], store, years=1)
    assert r.status == "ok"


def test_no_refresh_token_raises_a_clear_error(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(Settings, "get_schwab_refresh_token", lambda self: "")
    with pytest.raises(RuntimeError, match="No Schwab refresh token"):
        mod.build_schwab_provider()


def test_fetch_daily_bars_maps_et_dates_and_filters_the_window():
    class P:
        async def historical_df(self, sym, s, e, timeframe="1d"):
            return _bars(date(2026, 11, 2), date(2026, 11, 6))

    rows, status = asyncio.run(mod.fetch_daily_bars(P(), "X", date(2026, 11, 3), date(2026, 11, 5)))
    assert status == "ok" and [r[1].date() for r in rows] == [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]
    rows, status = asyncio.run(mod.fetch_daily_bars(P(), "X", date(2026, 12, 1), date(2026, 12, 2)))
    assert rows == [] and status == "out_of_range"
