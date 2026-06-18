"""Unit tests for futures gap detection (F3).

The CME Globex calendar differs from equities: futures trade Sun-Fri, so
only Saturday is skipped. Mocked PyIceberg tables keep the suite offline.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

pytest.importorskip("pyiceberg")

from app.services.futures import gaps  # noqa: E402
from app.services.futures.gaps import (  # noqa: E402
    is_futures_session_day,
    missing_futures_sessions,
)


def test_saturday_is_not_a_session_day():
    # 2026-06-20 is a Saturday.
    assert date(2026, 6, 20).weekday() == 5
    assert is_futures_session_day(date(2026, 6, 20)) is False


def test_sunday_is_a_session_day():
    # 2026-06-21 is a Sunday — Globex evening session opens 18:00 ET.
    assert date(2026, 6, 21).weekday() == 6
    assert is_futures_session_day(date(2026, 6, 21)) is True


def test_weekdays_are_session_days():
    # Mon 2026-06-15 .. Fri 2026-06-19.
    for dom in range(15, 20):
        assert is_futures_session_day(date(2026, 6, dom)) is True


def _table_mock(name="lake.futures.schwab_futures"):
    t = MagicMock()
    t.name.return_value = name
    return t


def test_missing_sessions_includes_sunday_skips_saturday(monkeypatch):
    """latest = Fri 2026-06-19; through = Mon 2026-06-22 → expect Sun + Mon,
    NOT Saturday."""
    monkeypatch.setattr(gaps, "latest_loaded_date", lambda table, **kw: date(2026, 6, 19))
    out = missing_futures_sessions(_table_mock(), through=date(2026, 6, 22))
    assert out == [date(2026, 6, 21), date(2026, 6, 22)]  # Sun, Mon — Sat dropped


def test_missing_sessions_empty_when_up_to_date(monkeypatch):
    monkeypatch.setattr(gaps, "latest_loaded_date", lambda table, **kw: date(2026, 6, 19))
    assert missing_futures_sessions(_table_mock(), through=date(2026, 6, 19)) == []


def test_missing_sessions_cold_start_returns_window(monkeypatch):
    """No rows in lookback → cold-start window of session days back from
    `through` (Saturday excluded)."""
    monkeypatch.setattr(gaps, "latest_loaded_date", lambda table, **kw: None)
    out = missing_futures_sessions(
        _table_mock(), through=date(2026, 6, 22), max_lookback_days=5,
    )
    # Window 06-17 (Wed) .. 06-22 (Mon); 06-20 Saturday excluded.
    assert date(2026, 6, 20) not in out
    assert out == [
        date(2026, 6, 17), date(2026, 6, 18), date(2026, 6, 19),
        date(2026, 6, 21), date(2026, 6, 22),
    ]


def test_missing_sessions_scan_failure_returns_empty(monkeypatch):
    """A coverage-scan error must NOT trigger a blind cold-start re-fetch."""
    def _boom(table, **kw):
        raise RuntimeError("S3 ACCESS_DENIED")

    monkeypatch.setattr(gaps, "latest_loaded_date", _boom)
    assert missing_futures_sessions(_table_mock(), through=date(2026, 6, 22)) == []
