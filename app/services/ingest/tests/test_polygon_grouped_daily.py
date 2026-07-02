"""Grouped-daily → canonical frame transform (pure)."""
from __future__ import annotations

from datetime import date, timezone
from types import SimpleNamespace

from app.services.ingest.polygon_grouped_daily import (
    SOURCE_TAG,
    day_timestamp_utc,
    grouped_daily_to_frame,
)

DAY = date(2026, 6, 30)


def test_day_timestamp_is_et_date_at_1430_utc():
    ts = day_timestamp_utc(DAY)
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2026, 6, 30, 14, 30)
    assert ts.tzinfo == timezone.utc


def test_sdk_objects_transform():
    rows = [SimpleNamespace(ticker="aapl", open=1.0, high=2.0, low=0.5,
                            close=1.5, volume=1e6, vwap=1.4, transactions=1234)]
    df = grouped_daily_to_frame(rows, DAY)
    assert len(df) == 1
    r = df.iloc[0]
    assert r.symbol == "AAPL"                 # uppercased
    assert r.close == 1.5 and r.volume == 1e6 and r.trade_count == 1234
    assert r.source == SOURCE_TAG
    assert r.timestamp == day_timestamp_utc(DAY)


def test_raw_json_short_keys_transform():
    rows = [{"T": "NVDA", "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.5,
             "v": 5e6, "vw": 10.4, "n": 999}]
    df = grouped_daily_to_frame(rows, DAY)
    assert df.iloc[0].symbol == "NVDA" and df.iloc[0].close == 10.5


def test_rows_without_symbol_or_close_dropped_and_empty_ok():
    rows = [{"T": "", "c": 1.0}, {"T": "X", "c": None}, {"T": "OK", "c": 2.0}]
    df = grouped_daily_to_frame(rows, DAY)
    assert list(df.symbol) == ["OK"]
    empty = grouped_daily_to_frame([], DAY)
    assert empty.empty and list(empty.columns)[:2] == ["symbol", "timestamp"]
