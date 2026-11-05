"""LakeArchive: exact gap detection, idempotent apply, cross-side verification.

Every collaborator is a fake; nothing here touches ClickHouse, Glue or S3.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.lake_integrity.schemas import EQUITIES, FUTURES, Bar
from app.services.lake_integrity.service import LakeArchive

D1, D2 = date(2026, 7, 1), date(2026, 7, 2)


def ts(day: date, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, 14, minute, tzinfo=timezone.utc)


def bar(sym: str, day: date, minute: int, source: str = "schwab-stream") -> Bar:
    return Bar(symbol=sym, timestamp=ts(day, minute), open=1, high=2, low=0.5, close=1.5, volume=10, source=source)


class FakeCh:
    def __init__(self, by_day: dict[date, list[Bar]]):
        self.by_day = by_day

    def days(self, family, since, until):
        return sorted(d for d in self.by_day if since <= d <= until)

    def bars(self, family, day):
        return list(self.by_day.get(day, []))


class FakeLake:
    """Index + writer in one: appended bars become visible, like the real lake."""

    def __init__(self, existing: dict[date, set] | None = None, *, visible: bool = True, fail_on: date | None = None, short_write: bool = False):
        self.index: dict[date, set] = {d: set(v) for d, v in (existing or {}).items()}
        self.appends: list[tuple[str, int]] = []
        self.fresh_reads = 0
        self.visible, self.fail_on, self.short_write = visible, fail_on, short_write

    def keys(self, family, day, *, fresh=False):
        self.fresh_reads += int(fresh)
        return set(self.index.get(day, set()))

    def append(self, family, bars, run_id):
        day = bars[0].timestamp.date()
        if self.fail_on == day:
            raise RuntimeError("commit failed")
        self.appends.append((run_id, len(bars)))
        if self.visible:
            self.index.setdefault(day, set()).update(b.key for b in bars)
        return len(bars) - 1 if self.short_write else len(bars)


def make(ch, lake) -> LakeArchive:
    return LakeArchive(ch=ch, lake=lake, writer=lake, run_id_factory=lambda: "run-1")


def test_complete_when_lake_has_every_bar():
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2)]
    lake = FakeLake({D1: {b.key for b in bars}})
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D2)
    assert (r.status, r.missing_total, r.days_checked) == ("complete", 0, 1)
    assert lake.appends == []


def test_dry_run_reports_exact_missing_and_never_writes():
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2), bar("MSFT", D1, 1)]
    lake = FakeLake({D1: {bars[0].key}})
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D2)
    assert r.status == "gaps_found"
    assert (r.missing_total, r.days_with_gaps, r.archived_total) == (2, 1, 0)
    assert r.days[0].ch_bars == 3 and r.days[0].missing == 2
    assert lake.appends == []


def test_lake_extras_do_not_hide_a_missing_bar():
    # Same row COUNT on both sides but different bars: counting would say "equal".
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2)]
    lake = FakeLake({D1: {bars[0].key, ("ZZZ", ts(D1, 9))}})
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D1)
    assert r.missing_total == 1


def test_apply_appends_only_missing_and_verifies_from_the_lake_side():
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2), bar("AAPL", D1, 3)]
    lake = FakeLake({D1: {bars[0].key}})
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D1, apply=True)
    assert r.status == "archived"
    assert (r.missing_total, r.archived_total, r.remaining) == (2, 2, 0)
    assert lake.appends == [("run-1", 2)]
    assert lake.fresh_reads == 1  # verification bypasses the index cache


def test_apply_is_idempotent():
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2)]
    lake = FakeLake({})
    arch = make(FakeCh({D1: bars}), lake)
    first = arch.run(EQUITIES, since=D1, until=D1, apply=True)
    second = arch.run(EQUITIES, since=D1, until=D1, apply=True)
    assert first.status == "archived" and second.status == "complete"
    assert len(lake.appends) == 1


def test_write_that_is_not_visible_is_an_error_not_success():
    bars = [bar("AAPL", D1, 1)]
    lake = FakeLake({}, visible=False)
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D1, apply=True)
    assert r.status == "error"
    assert r.archived_total == 0 and r.remaining == 1
    assert "not visible" in r.errors[0]


def test_writer_reporting_a_short_write_is_an_error():
    bars = [bar("AAPL", D1, 1), bar("AAPL", D1, 2)]
    lake = FakeLake({}, short_write=True)
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D1, apply=True)
    assert r.status == "error" and "reported" in r.errors[0]


def test_one_failing_day_is_isolated_and_the_others_still_archive():
    ch = FakeCh({D1: [bar("AAPL", D1, 1)], D2: [bar("AAPL", D2, 1)]})
    lake = FakeLake({}, fail_on=D1)
    r = make(ch, lake).run(EQUITIES, since=D1, until=D2, apply=True)
    assert r.status == "error"
    by_day = {g.day: g for g in r.days}
    assert by_day[D1].error and "RuntimeError" in by_day[D1].error
    assert by_day[D2].archived == 1 and by_day[D2].error is None


def test_duplicate_keys_collapse_with_source_priority():
    a = bar("AAPL", D1, 1, source="silver-schwab")
    b = bar("AAPL", D1, 1, source="schwab-stream")  # higher priority tag wins
    lake = FakeLake({})
    r = make(FakeCh({D1: [a, b]}), lake).run(EQUITIES, since=D1, until=D1)
    assert r.days[0].ch_bars == 1


def test_large_gaps_are_written_in_batches(monkeypatch):
    monkeypatch.setattr(LakeArchive, "BATCH_ROWS", 2)
    bars = [bar("AAPL", D1, m) for m in range(5)]
    lake = FakeLake({})
    r = make(FakeCh({D1: bars}), lake).run(EQUITIES, since=D1, until=D1, apply=True)
    assert r.status == "archived"
    assert [n for _, n in lake.appends] == [2, 2, 1]


def test_since_after_until_is_rejected():
    with pytest.raises(ValueError):
        make(FakeCh({}), FakeLake()).run(EQUITIES, since=D2, until=D1)


def test_works_for_the_futures_family():
    bars = [bar("/ES", D1, 1)]
    lake = FakeLake({})
    r = make(FakeCh({D1: bars}), lake).run(FUTURES, since=D1, until=D1, apply=True)
    assert r.family == "futures" and r.status == "archived"


def test_futures_stream_bars_are_matched_to_the_lake_at_their_real_minute():
    # Stream stamps arrival: 15:01:03.228 is the bar the lake labels 15:00:00.
    stream = Bar(symbol="/ES", timestamp=datetime(2026, 11, 4, 15, 1, 3, 228000, tzinfo=timezone.utc),
                 open=1, high=2, low=0.5, close=1.5, volume=3, source="schwab-stream")
    real_minute = ("/ES", datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc))
    day = date(2026, 11, 4)
    lake = FakeLake({day: {real_minute}})
    r = make(FakeCh({day: [stream]}), lake).run(FUTURES, since=day, until=day)
    assert r.status == "complete" and r.missing_total == 0


def test_futures_genuinely_absent_bar_is_archived_at_the_corrected_timestamp():
    stream = Bar(symbol="/ES", timestamp=datetime(2026, 8, 12, 15, 1, 3, 228000, tzinfo=timezone.utc),
                 open=1, high=2, low=0.5, close=1.5, volume=3, source="schwab-stream")
    day = date(2026, 8, 12)
    written = []
    lake = FakeLake({})
    orig = lake.append
    lake.append = lambda fam, bars, run_id: (written.extend(bars), orig(fam, bars, run_id))[1]
    r = make(FakeCh({day: [stream]}), lake).run(FUTURES, since=day, until=day, apply=True)
    assert r.status == "archived"
    assert written[0].timestamp == datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


def test_futures_bar_just_after_midnight_belongs_to_the_previous_utc_day():
    stream = Bar(symbol="/ES", timestamp=datetime(2026, 11, 5, 0, 0, 2, tzinfo=timezone.utc),
                 open=1, high=2, low=0.5, close=1.5, volume=3, source="schwab-stream")
    prev = date(2026, 11, 4)
    lake = FakeLake({prev: {("/ES", datetime(2026, 11, 4, 23, 59, tzinfo=timezone.utc))}})
    r = make(FakeCh({date(2026, 11, 5): [stream]}), lake).run(FUTURES, since=prev, until=date(2026, 11, 5))
    assert r.missing_total == 0  # looked it up on the 4th, where it really lives


def test_equities_timestamps_are_never_shifted():
    b = bar("AAPL", D1, 7)
    lake = FakeLake({})
    r = make(FakeCh({D1: [b]}), lake).run(EQUITIES, since=D1, until=D1)
    assert r.missing_total == 1
    assert LakeArchive._normalize(EQUITIES, b).timestamp == b.timestamp
