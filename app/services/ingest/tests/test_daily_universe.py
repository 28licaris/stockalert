"""The nightly daily refresh also covers stream-universe and personal-theme tickers."""
from __future__ import annotations

from app.services.ingest.daily_universe import extra_daily_symbols


def test_union_is_sorted_upper_cased_and_deduplicated():
    syms, warns = extra_daily_symbols(
        stream_universe=lambda: ["xlk", "SPY", "SPY"],
        personal_tickers=lambda: {"CCJ", "spy", " uec "},
    )
    assert syms == ["CCJ", "SPY", "UEC", "XLK"] and warns == []


def test_unreadable_source_is_a_warning_and_the_other_still_counts():
    def boom():
        raise RuntimeError("clickhouse down")

    syms, warns = extra_daily_symbols(stream_universe=boom, personal_tickers=lambda: ["CCJ"])
    assert syms == ["CCJ"]
    assert len(warns) == 1 and "stream_universe" in warns[0] and "clickhouse down" in warns[0]


def test_both_sources_down_gives_two_warnings_and_no_symbols():
    def boom():
        raise OSError("nope")

    syms, warns = extra_daily_symbols(stream_universe=boom, personal_tickers=boom)
    assert syms == [] and len(warns) == 2


def test_blank_entries_are_ignored():
    syms, _ = extra_daily_symbols(stream_universe=lambda: ["", "  ", "AAA"], personal_tickers=lambda: [])
    assert syms == ["AAA"]
