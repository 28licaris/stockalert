"""Resolver tests — daily closes in, one series per group out."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.services.sectors import resolver
from app.services.sectors.resolver import GroupResolutionError, load_daily_closes, resolve
from app.services.sectors.schemas import RotationGroup


def _s(values, day0=2) -> pd.Series:
    idx = pd.Index([date(2024, 1, day0 + i) for i in range(len(values))], name="date")
    return pd.Series(values, index=idx, dtype="float64")


def _etf(sym="XLK") -> RotationGroup:
    return RotationGroup(id=sym, name=sym, benchmark="SPY", members=[sym])


def _basket(members, weights=None) -> RotationGroup:
    return RotationGroup(id="MINERS", name="Miners", kind="basket", benchmark="SPY",
                         members=members, weights=weights)


def test_etf_passthrough_returns_close_series():
    s = resolve(_etf(), {"XLK": _s([10.0, 11.0, 12.0])})
    assert list(s.values) == [10.0, 11.0, 12.0]
    assert s.is_monotonic_increasing


def test_missing_etf_names_the_symbol():
    with pytest.raises(GroupResolutionError, match="no daily data for XLB"):
        resolve(_etf("XLB"), {"XLK": _s([1.0, 2.0])})


def test_basket_equal_weight_composite():
    closes = {"AAA": _s([100.0, 100.0, 100.0]), "BBB": _s([100.0, 150.0, 200.0])}
    s = resolve(_basket(["AAA", "BBB"]), closes)
    assert s.iloc[0] == pytest.approx(100.0)   # (1.0 + 1.0)/2 * 100
    assert s.iloc[-1] == pytest.approx(150.0)  # (1.0 + 2.0)/2 * 100


def test_basket_drops_missing_members():
    s = resolve(_basket(["AAA", "GHOST"]), {"AAA": _s([10.0, 11.0, 12.0])})
    assert len(s) == 3 and s.iloc[0] == pytest.approx(100.0)


def test_basket_all_missing_raises_with_reason():
    with pytest.raises(GroupResolutionError, match="no daily data for any of 2 members"):
        resolve(_basket(["X", "Y"]), {})


def test_basket_weighted():
    closes = {"AAA": _s([100.0, 100.0, 100.0]), "BBB": _s([100.0, 150.0, 200.0])}
    s = resolve(_basket(["AAA", "BBB"], {"AAA": 0.25, "BBB": 0.75}), closes)
    assert s.iloc[-1] == pytest.approx(175.0)


# ── load_daily_closes: one query, grouped, de-spiked ──────────────────


class FakeCH:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))

        class R:
            result_rows = self.rows

        return R()


def _rows(sym, closes, day0=2):
    return [(sym, date(2024, 1, day0 + i), c) for i, c in enumerate(closes)]


def test_load_issues_exactly_one_query_for_many_symbols():
    ch = FakeCH(_rows("SPY", [1, 2, 3]) + _rows("XLK", [4, 5, 6]) + _rows("XLE", [7, 8, 9]))
    out = load_daily_closes(["XLK", "SPY", "XLE", "XLK"], 360, client=ch)
    assert len(ch.queries) == 1, "one batched query, not one per symbol"
    sql, params = ch.queries[0]
    assert "ohlcv_daily" in sql and "FINAL" in sql
    assert params["syms"] == ["SPY", "XLE", "XLK"]  # sorted, de-duplicated
    assert set(out) == {"SPY", "XLK", "XLE"} and list(out["XLK"].values) == [4.0, 5.0, 6.0]


def test_symbols_without_rows_are_absent_not_empty():
    out = load_daily_closes(["SPY", "CCJ"], 360, client=FakeCH(_rows("SPY", [1, 2, 3])))
    assert "CCJ" not in out


def test_no_symbols_means_no_query():
    ch = FakeCH([])
    assert load_daily_closes([], 360, client=ch) == {} and ch.queries == []


def test_despike_removes_reverting_spike():
    ch = FakeCH(_rows("XLK", [146.0, 291.0, 146.0, 147.0]))
    s = load_daily_closes(["XLK"], 360, client=ch)["XLK"]
    assert s.iloc[1] == 146.0 and s.iloc[0] == 146.0 and s.iloc[2] == 146.0


def test_despike_preserves_genuine_step():
    ch = FakeCH(_rows("XLK", [100.0, 160.0, 162.0, 161.0]))
    assert list(load_daily_closes(["XLK"], 360, client=ch)["XLK"].values) == [100.0, 160.0, 162.0, 161.0]


def test_duplicate_days_keep_the_last():
    ch = FakeCH([("XLK", date(2024, 1, 2), 10.0), ("XLK", date(2024, 1, 2), 10.5), ("XLK", date(2024, 1, 3), 11.0)])
    s = load_daily_closes(["XLK"], 360, client=ch)["XLK"]
    assert len(s) == 2 and s.iloc[0] == 10.5
