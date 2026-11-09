"""Unit tests for app.services.market_calendar (exchange_calendars-backed).

No network/AWS — exchange_calendars is a local rules library. Pins the
behaviour the gap filler + calendar API rely on against known 2026 dates.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import market_calendar as mc


def test_juneteenth_equities_closed_futures_open():
    jun = date(2026, 6, 19)
    assert mc.is_equities_session(jun) is False
    assert mc.is_futures_session(jun) is True
    assert "Juneteenth" in (mc.closed_reason("equities", jun) or "")


def test_normal_weekday_open():
    d = date(2026, 6, 18)  # Thursday, normal session
    assert mc.is_equities_session(d) is True
    assert mc.day_status("equities", d)["status"] == mc.STATUS_OPEN


def test_weekend_closed_with_reason():
    sat = date(2026, 6, 20)
    st = mc.day_status("equities", sat)
    assert st["status"] == mc.STATUS_CLOSED
    assert st["reason"] == "Weekend"


def test_day_after_thanksgiving_early_close_1300():
    d = date(2026, 11, 27)
    st = mc.day_status("equities", d)
    assert st["status"] == mc.STATUS_EARLY_CLOSE
    assert st["early_close_et"] == "13:00"


def test_thanksgiving_equities_closed_futures_early():
    # Equities fully closed; CME futures trade a shortened (early-close)
    # session — the asset-class distinction the gap filler needs.
    d = date(2026, 11, 26)
    assert mc.is_equities_session(d) is False
    assert mc.closed_reason("equities", d) == "Thanksgiving"
    assert mc.day_status("futures", d)["status"] == mc.STATUS_EARLY_CLOSE


def test_sessions_excludes_holiday():
    s = mc.equities_sessions(date(2026, 6, 17), date(2026, 6, 23))
    assert date(2026, 6, 19) not in s          # Juneteenth excluded
    assert date(2026, 6, 18) in s and date(2026, 6, 22) in s
    assert s == sorted(s)


def test_futures_sessions_include_juneteenth():
    s = mc.futures_sessions(date(2026, 6, 17), date(2026, 6, 23))
    assert date(2026, 6, 19) in s              # CME open on Juneteenth


def test_calendar_range_covers_every_day():
    days = mc.calendar_range("equities", date(2026, 6, 17), date(2026, 6, 21))
    assert [d["date"] for d in days] == [
        date(2026, 6, 17), date(2026, 6, 18), date(2026, 6, 19),
        date(2026, 6, 20), date(2026, 6, 21),
    ]
    by_date = {d["date"]: d for d in days}
    assert by_date[date(2026, 6, 18)]["status"] == mc.STATUS_OPEN
    assert by_date[date(2026, 6, 19)]["status"] == mc.STATUS_CLOSED  # Juneteenth
    assert by_date[date(2026, 6, 20)]["status"] == mc.STATUS_CLOSED  # Saturday


def test_unknown_asset_class_raises():
    with pytest.raises(ValueError, match="asset_class"):
        mc.day_status("crypto", date(2026, 6, 18))


def test_empty_range_is_empty():
    assert mc.equities_sessions(date(2026, 6, 20), date(2026, 6, 18)) == []


# ---- is_equities_regular_hours (the stale-stream watchdog's session test) ----

from datetime import datetime, timezone  # noqa: E402


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_regular_hours_boundaries_est():
    # Mon 2026-11-09, EST (UTC-5): 09:30 ET = 14:30 UTC, 16:00 ET = 21:00 UTC.
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 14, 29)) is False
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 14, 30)) is True
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 20, 59)) is True
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 21, 0)) is False


def test_regular_hours_boundaries_edt():
    # Wed 2026-06-17, EDT (UTC-4): open 13:30 UTC, close 20:00 UTC.
    assert mc.is_equities_regular_hours(_utc(2026, 6, 17, 13, 29)) is False
    assert mc.is_equities_regular_hours(_utc(2026, 6, 17, 13, 30)) is True
    assert mc.is_equities_regular_hours(_utc(2026, 6, 17, 19, 59)) is True
    assert mc.is_equities_regular_hours(_utc(2026, 6, 17, 20, 0)) is False


def test_regular_hours_grace_shrinks_both_ends():
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 14, 34), open_grace_min=5) is False
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 14, 35), open_grace_min=5) is True
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 20, 55), close_grace_min=5) is False
    assert mc.is_equities_regular_hours(_utc(2026, 11, 9, 20, 54), close_grace_min=5) is True


def test_regular_hours_closed_on_weekend_and_holiday():
    assert mc.is_equities_regular_hours(_utc(2026, 11, 7, 16, 0)) is False   # Saturday
    assert mc.is_equities_regular_hours(_utc(2026, 11, 26, 16, 0)) is False  # Thanksgiving


def test_regular_hours_honours_early_close():
    # Fri 2026-11-27 closes 13:00 ET = 18:00 UTC.
    assert mc.is_equities_regular_hours(_utc(2026, 11, 27, 17, 59)) is True
    assert mc.is_equities_regular_hours(_utc(2026, 11, 27, 18, 0)) is False


def test_regular_hours_rejects_naive_datetime():
    with pytest.raises(ValueError):
        mc.is_equities_regular_hours(datetime(2026, 11, 9, 15, 0))
