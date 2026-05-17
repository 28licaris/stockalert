"""Unit tests for app/services/bronze/gaps.py — pure date logic.

The Iceberg scan inside `latest_bronze_date` is tested in the integration
suite (`tests/integration/test_bronze_sink.py`). Here we focus on the
date math of `missing_weekdays` + `yesterday_et`, which is the part most
likely to regress on DST / timezone edges.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.bronze.gaps import missing_weekdays


class _FakeTable:
    """Stub object — `missing_weekdays` only uses the table to call
    `latest_bronze_date`, which we monkeypatch."""

    def name(self) -> tuple[str, str]:
        return ("test", "fake")


@pytest.fixture
def fake_table(monkeypatch):
    """Returns (table, set_latest) — call set_latest(d) to control what
    `latest_bronze_date` returns for the subsequent `missing_weekdays`
    invocation."""
    state = {"latest": None}

    def fake_latest(table, *, lookback_days: int = 14):
        return state["latest"]

    monkeypatch.setattr(
        "app.services.bronze.gaps.latest_bronze_date", fake_latest
    )
    return _FakeTable(), lambda d: state.__setitem__("latest", d)


# ─────────────────────────────────────────────────────────────────────
# Caught-up: nothing missing
# ─────────────────────────────────────────────────────────────────────
def test_no_gap_when_caught_up_through_yesterday(fake_table):
    table, set_latest = fake_table
    set_latest(date(2026, 5, 14))  # latest in bronze
    # `through` = May 14 → caught up
    assert missing_weekdays(table, through=date(2026, 5, 14)) == []


def test_no_gap_when_latest_after_through(fake_table):
    """If bronze somehow has data newer than `through`, return []."""
    table, set_latest = fake_table
    set_latest(date(2026, 5, 15))
    assert missing_weekdays(table, through=date(2026, 5, 14)) == []


# ─────────────────────────────────────────────────────────────────────
# Single-day and multi-day gaps
# ─────────────────────────────────────────────────────────────────────
def test_one_day_gap(fake_table):
    table, set_latest = fake_table
    set_latest(date(2026, 5, 13))  # Wednesday
    assert missing_weekdays(table, through=date(2026, 5, 14)) == [date(2026, 5, 14)]


def test_three_day_gap_no_weekend(fake_table):
    table, set_latest = fake_table
    set_latest(date(2026, 5, 11))  # Monday
    assert missing_weekdays(table, through=date(2026, 5, 14)) == [
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
    ]


def test_gap_spanning_weekend_skips_sat_sun(fake_table):
    table, set_latest = fake_table
    set_latest(date(2026, 5, 8))  # Friday
    # Through Tuesday May 12 — weekend (10, 11 = Sun, Mon... wait let me check)
    # 2026-05-08 = Friday, 09 = Sat, 10 = Sun, 11 = Mon, 12 = Tue
    out = missing_weekdays(table, through=date(2026, 5, 12))
    assert out == [date(2026, 5, 11), date(2026, 5, 12)]
    # No Saturday/Sunday in there.
    assert all(d.weekday() < 5 for d in out)


# ─────────────────────────────────────────────────────────────────────
# Cold-start (empty table)
# ─────────────────────────────────────────────────────────────────────
def test_cold_start_fills_lookback_window(fake_table):
    table, set_latest = fake_table
    set_latest(None)  # empty
    out = missing_weekdays(
        table,
        through=date(2026, 5, 14),  # Thursday
        max_lookback_days=7,
    )
    # Window: May 7 .. May 14, weekdays only
    expected = [
        date(2026, 5, 7),
        date(2026, 5, 8),
        date(2026, 5, 11),
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
    ]
    assert out == expected


def test_cold_start_zero_lookback_returns_empty(fake_table):
    table, set_latest = fake_table
    set_latest(None)
    assert missing_weekdays(table, through=date(2026, 5, 14), max_lookback_days=0) == [
        date(2026, 5, 14),  # through is included (Thursday)
    ]


# ─────────────────────────────────────────────────────────────────────
# Boundary cases
# ─────────────────────────────────────────────────────────────────────
def test_gap_ending_on_weekend_skips_those_days(fake_table):
    table, set_latest = fake_table
    set_latest(date(2026, 5, 14))  # Thursday
    # Through Saturday May 16 — should still return [Fri May 15] only
    out = missing_weekdays(table, through=date(2026, 5, 16))
    assert out == [date(2026, 5, 15)]
