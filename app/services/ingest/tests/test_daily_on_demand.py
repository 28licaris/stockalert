"""DailyDemand: check before saving, fetch after; guard rails against abuse and outages.

Fakes only: no ClickHouse, no Schwab, no real threads.
"""
from __future__ import annotations

import pytest

from app.services.ingest import daily_on_demand as mod
from app.services.ingest.daily_on_demand import DailyDataUnavailable, DailyDemand, TooManyNewTickers
from app.services.ingest.schwab_daily import Coverage, DailyEnsureResult


class FakeStore:
    def __init__(self, sessions: dict[str, int]):
        self.sessions = sessions

    def coverage(self, symbols):
        return {s: Coverage(self.sessions[s], None, None) for s in symbols if s in self.sessions}

    def insert(self, rows):  # pragma: no cover - never reached here
        raise AssertionError("check/schedule must not write")


def _demand(sessions=None, exists=None, ensure=None, clock=None, background=None):
    calls = {"exists": [], "ensure": [], "jobs": []}

    def default_exists(symbols):
        calls["exists"].append(list(symbols))
        return {s: True for s in symbols}

    async def default_ensure(symbols):
        calls["ensure"].append(list(symbols))
        return [DailyEnsureResult(s, "ok", rows=300) for s in symbols]

    def run_now(fn):
        calls["jobs"].append(fn)
        fn()

    d = DailyDemand(
        store=FakeStore(sessions or {}),
        exists=(lambda syms: (calls["exists"].append(list(syms)), exists(syms))[1]) if exists else default_exists,
        background=background or run_now,
        ensure=ensure or default_ensure,
        clock=clock or (lambda: 1000.0),
    )
    return d, calls


def test_covered_tickers_cost_no_schwab_calls():
    d, calls = _demand(sessions={"SPY": 5000, "XLK": 5000})
    check = d.check(["spy", "XLK"])
    assert check.covered == ("SPY", "XLK") and check.to_fetch == () and check.unknown == ()
    assert calls["exists"] == []


def test_partial_history_is_fetched_without_an_existence_check():
    """The 56-session shared-theme members: we know they exist; just top them up."""
    d, calls = _demand(sessions={"SCCO": 56})
    check = d.check(["SCCO"])
    assert check.to_fetch == ("SCCO",) and calls["exists"] == []


def test_brand_new_ticker_is_checked_with_schwab_then_fetched():
    d, calls = _demand()
    check = d.check(["CCJ", "UEC"])
    assert calls["exists"] == [["CCJ", "UEC"]]
    assert check.to_fetch == ("CCJ", "UEC") and check.unknown == ()


def test_ticker_schwab_does_not_know_is_unknown():
    d, _ = _demand(exists=lambda syms: {"CCJ": True, "ZZZZ": False})
    check = d.check(["CCJ", "ZZZZ"])
    assert check.unknown == ("ZZZZ",) and check.to_fetch == ("CCJ",)


@pytest.mark.parametrize("bad", ["/ES", "A B", "<script>", "TOOLONGTICKER1", "$X"])
def test_malformed_symbols_are_unknown_without_any_io(bad):
    d, calls = _demand()
    assert bad.strip().upper() in d.check([bad]).unknown
    assert calls["exists"] == []


def test_unknown_tickers_are_remembered_so_a_typo_cannot_loop():
    now = [1000.0]
    d, calls = _demand(exists=lambda syms: {s: False for s in syms}, clock=lambda: now[0])
    assert d.check(["ZZZZ"]).unknown == ("ZZZZ",)
    assert d.check(["ZZZZ"]).unknown == ("ZZZZ",)
    assert len(calls["exists"]) == 1, "the second attempt must not call Schwab again"
    now[0] += mod.UNKNOWN_TTL_S + 1
    d.check(["ZZZZ"])
    assert len(calls["exists"]) == 2, "after the TTL it is asked again"


def test_too_many_brand_new_tickers_in_one_request_is_refused():
    d, calls = _demand()
    syms = [f"T{i}" for i in range(mod.MAX_NEW_PER_REQUEST + 1)]
    with pytest.raises(TooManyNewTickers, match=str(mod.MAX_NEW_PER_REQUEST)):
        d.check(syms)
    assert calls["exists"] == []


def test_covered_tickers_do_not_count_toward_the_new_ticker_cap():
    d, _ = _demand(sessions={f"C{i}": 500 for i in range(30)})
    assert len(d.check([f"C{i}" for i in range(30)]).covered) == 30


def test_source_outage_is_raised_never_reported_as_unknown():
    def down(syms):
        raise DailyDataUnavailable("token expired")

    d, _ = _demand(exists=down)
    with pytest.raises(DailyDataUnavailable):
        d.check(["CCJ"])


def test_schedule_runs_ensure_in_the_background_and_returns_immediately():
    started = []
    d, calls = _demand(background=lambda fn: started.append(fn))
    d.schedule(["CCJ", "UEC"])
    assert calls["ensure"] == [], "nothing runs on the caller's thread"
    started[0]()
    assert calls["ensure"] == [["CCJ", "UEC"]]


def test_schedule_with_nothing_to_do_does_not_start_a_thread():
    d, calls = _demand()
    d.schedule([])
    assert calls["jobs"] == []


def test_a_failing_background_fetch_is_logged_not_raised(caplog):
    async def boom(symbols):
        raise RuntimeError("schwab exploded")

    d, _ = _demand(ensure=boom)
    d.schedule(["CCJ"])  # must not raise
    assert any("background fetch failed" in r.message for r in caplog.records)


def test_empty_result_in_background_is_remembered_as_unknown():
    async def ensure(symbols):
        return [DailyEnsureResult("ZZZZ", "empty", detail="Schwab has no price history")]

    d, _ = _demand(ensure=ensure)
    d.schedule(["ZZZZ"])
    assert d.check(["ZZZZ"]).unknown == ("ZZZZ",)
