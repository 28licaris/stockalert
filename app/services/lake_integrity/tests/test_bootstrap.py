from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.lake_integrity.bootstrap import rebuild_windows, split_window, verify_coverage
from app.services.lake_integrity.schemas import CoverageRow

LAKE_START = date(2021, 1, 4)


def cov(sym, first, last, rows) -> CoverageRow:
    d = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)  # noqa: E731
    return CoverageRow(symbol=sym, first=d(first), last=d(last), rows=rows)


def test_windows_give_early_history_symbols_their_own_start():
    groups = rebuild_windows(
        [cov("SPY", "2006-01-03", "2026-11-04", 1), cov("QQQ", "2006-01-03", "2026-11-04", 1),
         cov("AAPL", "2021-01-04", "2026-11-04", 1), cov("NEWCO", "2024-06-03", "2026-11-04", 1)],
        LAKE_START,
    )
    assert groups == {date(2006, 1, 3): ["QQQ", "SPY"], date(2021, 1, 4): ["AAPL", "NEWCO"]}


def test_verified_when_the_rebuild_holds_at_least_what_was_recorded():
    exp = [cov("SPY", "2006-01-03", "2026-11-04", 1000)]
    act = [cov("SPY", "2006-01-03", "2026-11-05", 1400)]  # the lake has MORE: fine
    assert verify_coverage(exp, act) == []


def test_each_failure_mode_is_reported_by_symbol():
    exp = [cov("A", "2006-01-03", "2026-11-04", 1000), cov("B", "2021-01-04", "2026-11-04", 1000),
           cov("C", "2021-01-04", "2026-11-04", 1000), cov("D", "2021-01-04", "2026-11-04", 1000)]
    act = [cov("A", "2021-01-04", "2026-11-04", 1000),   # history truncated to the 2021 default window
           cov("B", "2021-01-04", "2026-10-01", 1000),   # stale
           cov("C", "2021-01-04", "2026-11-04", 400)]    # too few rows; D absent entirely
    kinds = {(p.symbol, p.kind) for p in verify_coverage(exp, act)}
    assert kinds == {("A", "history_truncated"), ("B", "stale"), ("C", "too_few_rows"), ("D", "missing_symbol")}


def test_first_date_slack_tolerates_a_few_days():
    exp = [cov("A", "2021-01-04", "2026-11-04", 100)]
    assert verify_coverage(exp, [cov("A", "2021-01-08", "2026-11-04", 100)]) == []


def test_split_window_is_contiguous_bounded_and_covers_the_whole_range():
    segs = split_window(date(2006, 1, 3), date(2026, 11, 5), max_years=3)
    assert segs[0][0] == date(2006, 1, 3) and segs[-1][1] == date(2026, 11, 5)
    for (a, b), (c, _) in zip(segs, segs[1:]):
        assert c == b + __import__("datetime").timedelta(days=1)  # no gap, no overlap
    assert all((b - a).days < 3 * 366 for a, b in segs)
    assert len(segs) == 7


def test_split_window_short_range_is_one_segment_and_bad_range_is_rejected():
    assert split_window(date(2025, 1, 2), date(2026, 11, 5)) == [(date(2025, 1, 2), date(2026, 11, 5))]
    assert split_window(date(2024, 2, 29), date(2024, 2, 29)) == [(date(2024, 2, 29), date(2024, 2, 29))]
    import pytest
    with pytest.raises(ValueError):
        split_window(date(2026, 1, 2), date(2025, 1, 1))
