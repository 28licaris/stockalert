"""Unit tests for nightly Polygon → S3 lake refresh helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.nightly_lake_refresh import (
    _parse_nightly_kind,
    _seconds_until_next_run,
    resolve_nightly_lake_symbols,
)


def test_seconds_until_next_same_calendar_day():
    now = datetime(2026, 5, 14, 6, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    assert s == pytest.approx(3600.0, abs=0.01)


def test_seconds_until_next_rolls_to_next_day():
    now = datetime(2026, 5, 14, 8, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    nxt = datetime(2026, 5, 15, 7, 0, 0, tzinfo=timezone.utc)
    assert s == pytest.approx((nxt - now).total_seconds(), abs=0.5)


def test_seconds_until_next_exact_boundary():
    now = datetime(2026, 5, 14, 7, 0, 0, tzinfo=timezone.utc)
    s = _seconds_until_next_run(7, now=now)
    assert s >= 86400.0 - 1.0


def test_resolve_seed_non_empty():
    syms = resolve_nightly_lake_symbols("seed")
    assert len(syms) >= 10
    assert all(isinstance(x, str) and x.isupper() for x in syms)


def test_resolve_explicit_and_all():
    assert resolve_nightly_lake_symbols("aapl, msft") == ["AAPL", "MSFT"]
    assert resolve_nightly_lake_symbols("all") == []


def test_parse_nightly_kind_variants():
    assert _parse_nightly_kind("minute") == ("minute",)
    assert _parse_nightly_kind("DAY") == ("day",)
    assert _parse_nightly_kind("both") == ("minute", "day")
