"""Unit tests for app.services.market_events (free events: computed + seed).

Pure / no AWS / no CH (ch_events is patched to [] so these stay isolated and
fast). exchange_calendars is a local rules lib.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import market_events as me


def test_opex_third_friday_normal_month():
    # July 2026: 3rd Friday = 2026-07-17 (a normal session) → OPEX, medium.
    evts = me.computed_events(date(2026, 7, 1), date(2026, 7, 31))
    opex = [e for e in evts if e["event_type"] == "opex"]
    assert len(opex) == 1
    assert opex[0]["event_date"] == date(2026, 7, 17)
    assert opex[0]["importance"] == "medium"


def test_opex_shifts_off_holiday_and_quad_witching():
    # June 2026: 3rd Friday = 2026-06-19 = Juneteenth (closed) → expiration
    # shifts to the prior session 2026-06-18; June is a quad-witching month.
    evts = me.computed_events(date(2026, 6, 1), date(2026, 6, 30))
    quad = [e for e in evts if e["event_type"] == "quad_witching"]
    assert len(quad) == 1
    assert quad[0]["event_date"] == date(2026, 6, 18)
    assert quad[0]["importance"] == "high"


def test_computed_empty_range():
    assert me.computed_events(date(2026, 6, 30), date(2026, 6, 1)) == []


def test_seed_fomc_in_window():
    evts = me.seed_events(date(2026, 6, 1), date(2026, 6, 30))
    fomc = [e for e in evts if e["event_type"] == "fomc"]
    assert any(e["event_date"] == date(2026, 6, 17) for e in fomc)
    assert fomc[0]["importance"] == "high"
    assert fomc[0]["event_time_et"] == "14:00"


def test_seed_filters_out_of_window():
    evts = me.seed_events(date(2026, 6, 18), date(2026, 6, 30))
    assert all(e["event_date"] >= date(2026, 6, 18) for e in evts)


def test_seed_missing_file_is_empty(tmp_path):
    assert me.seed_events(
        date(2026, 1, 1), date(2026, 12, 31), path=str(tmp_path / "nope.json")
    ) == []


def test_events_in_range_merges_sorted(monkeypatch):
    # Isolate from CH.
    monkeypatch.setattr(me, "ch_events", lambda *a, **k: [])
    evts = me.events_in_range(date(2026, 6, 1), date(2026, 6, 30))
    # Contains both the seeded FOMC (6/17) and the computed quad-witching (6/18).
    types_by_date = {(e["event_date"], e["event_type"]) for e in evts}
    assert (date(2026, 6, 17), "fomc") in types_by_date
    assert (date(2026, 6, 18), "quad_witching") in types_by_date
    # Sorted ascending by date.
    dates = [e["event_date"] for e in evts]
    assert dates == sorted(dates)


def test_events_in_range_symbol_filter_keeps_marketwide(monkeypatch):
    # CH returns an AAPL-specific event + a NVDA one; market-wide (FOMC/OPEX)
    # must always pass, NVDA must be filtered out when asking for AAPL.
    monkeypatch.setattr(me, "ch_events", lambda s, e, symbol=None, client=None: [
        me._event(date(2026, 6, 5), "dividend", "AAPL dividend", "low", "lake", symbol="AAPL"),
        me._event(date(2026, 6, 5), "dividend", "NVDA dividend", "low", "lake", symbol="NVDA"),
    ])
    evts = me.events_in_range(date(2026, 6, 1), date(2026, 6, 30), symbol="AAPL")
    syms = {e["symbol"] for e in evts}
    assert "NVDA" not in syms
    assert "AAPL" in syms
    assert "" in syms  # market-wide FOMC/OPEX retained
