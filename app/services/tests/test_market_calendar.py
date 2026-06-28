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
