"""Unit tests for app.services.news.econ — pure parse/transforms + ingest."""
from __future__ import annotations

import calendar

import pytest

from app.services.news.econ import (
    ALL_SERIES,
    BEA_SERIES,
    BLS_SERIES,
    BeaError,
    BlsError,
    EconPoint,
    EconService,
    build_release_headline,
    compute_indicator,
    parse_bea_table,
    parse_bea_timeperiod,
    parse_bls_response,
)
from app.services.news.service import _NEWS_COLUMNS

_BLS_JSON = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {"series": [{
        "seriesID": "LNS14000000",
        "data": [
            {"year": "2026", "period": "M13", "periodName": "Annual", "value": "4.0"},
            {"year": "2026", "period": "M05", "periodName": "May", "value": "4.1"},
            {"year": "2026", "period": "M04", "periodName": "April", "value": "4.0"},
            {"year": "2026", "period": "M03", "periodName": "March", "value": "bad"},
        ],
    }]},
}

_BEA_JSON = {
    "BEAAPI": {"Results": {"Data": [
        {"LineNumber": "1", "TimePeriod": "2025Q4", "DataValue": "3.1"},
        {"LineNumber": "2", "TimePeriod": "2026Q1", "DataValue": "9.9"},
        {"LineNumber": "1", "TimePeriod": "2026Q1", "DataValue": "2.4"},
    ]}},
}


def _pts(sid, values, start_year=2024, start_month=1):
    pts, y, m = [], start_year, start_month
    for v in values:
        pts.append(EconPoint(sid, f"{y}-{m:02d}", f"{calendar.month_name[m]} {y}", float(v)))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return pts


# ── BLS parsing ──
def test_parse_bls_skips_annual_and_bad_and_sorts():
    pts = parse_bls_response(_BLS_JSON)["LNS14000000"]
    assert [p.period_key for p in pts] == ["2026-04", "2026-05"]
    assert pts[-1].value == 4.1
    assert pts[-1].period_label == "May 2026"


def test_parse_bls_raises_on_failure():
    with pytest.raises(BlsError):
        parse_bls_response({"status": "REQUEST_NOT_PROCESSED", "message": ["bad"]})


# ── BEA parsing ──
def test_parse_bea_timeperiod():
    assert parse_bea_timeperiod("2026Q1") == ("2026-Q1", "Q1 2026")
    assert parse_bea_timeperiod("2026M05") == ("2026-05", "May 2026")
    assert parse_bea_timeperiod("junk") is None


def test_parse_bea_filters_line_and_sorts():
    pts = parse_bea_table(_BEA_JSON, series_id="BEA_GDP", line="1")
    assert [p.period_key for p in pts] == ["2025-Q4", "2026-Q1"]   # line 2 dropped
    assert pts[-1].value == 2.4 and pts[-1].period_label == "Q1 2026"


def test_parse_bea_strips_commas_in_value():
    data = {"BEAAPI": {"Results": {"Data": [
        {"LineNumber": "1", "TimePeriod": "2026Q1", "DataValue": "1,234.5"},
    ]}}}
    assert parse_bea_table(data, series_id="X", line="1")[0].value == 1234.5


def test_parse_bea_error_raises():
    with pytest.raises(BeaError):
        parse_bea_table({"BEAAPI": {"Results": {"Error": "bad key"}}}, series_id="X", line="1")


# ── transforms ──
def test_level_indicator():
    ind = compute_indicator(BLS_SERIES["LNS14000000"], _pts("LNS14000000", [4.0, 4.1]))
    assert ind.value == pytest.approx(4.1)
    assert ind.change == pytest.approx(0.1)
    assert ind.value_label == "4.1%"


def test_yoy_indicator_monthly():
    vals = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 103, 104]
    ind = compute_indicator(BLS_SERIES["CUUR0000SA0"], _pts("CUUR0000SA0", vals))
    assert ind.value == pytest.approx(4.0)
    assert ind.change == pytest.approx(1.0)


def test_mom_delta_indicator():
    ind = compute_indicator(BLS_SERIES["CES0000000001"], _pts("CES0000000001", [100, 200, 380]))
    assert ind.value == pytest.approx(180)
    assert ind.change == pytest.approx(80)
    assert ind.value_label == "+180k"


def test_bea_gdp_level_quarterly():
    pts = parse_bea_table(_BEA_JSON, series_id="BEA_GDP", line="1")  # 3.1, 2.4
    ind = compute_indicator(BEA_SERIES["BEA_GDP"], pts)
    assert ind.value == pytest.approx(2.4)
    assert ind.change == pytest.approx(-0.7)     # 2.4 - 3.1
    assert ind.value_label == "2.4%"
    assert ind.period_label == "Q1 2026"


def test_headline_is_deterministic():
    pts = _pts("LNS14000000", [4.0, 4.1], start_year=2026, start_month=4)
    h = build_release_headline(BLS_SERIES["LNS14000000"], pts)
    assert h == "Unemployment rate: 4.1% (May 2026)"


# ── ingest / read service ──
class _FakeQR:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCH:
    def __init__(self, max_period=None, points_rows=None):
        self.max_period = max_period
        self.points_rows = points_rows or []
        self.inserts = []

    def query(self, sql, parameters=None):
        if "max(period)" in sql:
            return _FakeQR([[self.max_period]])
        return _FakeQR(self.points_rows)

    def insert(self, table, data, column_names=None):
        self.inserts.append((table, data, column_names))


class _FakeBls:
    def __init__(self, by):
        self._by = by

    def fetch(self, ids, *, start_year, end_year):
        return self._by


def test_ingest_upserts_and_emits_release():
    pts = _pts("LNS14000000", [4.0, 4.1], start_year=2026, start_month=4)
    ch = _FakeCH(max_period=None)
    svc = EconService(bls=_FakeBls({"LNS14000000": pts}), ch_client=ch)

    res = svc.ingest()

    assert res.points == 2 and res.releases == 1
    news = next(i for i in ch.inserts if i[0] == "news_items")
    row = dict(zip(_NEWS_COLUMNS, news[1][0]))
    assert row["source"] == "bls" and row["event_type"] == "econ"
    assert row["symbol"] == "" and row["enriched"] == 1 and row["materiality"] == "high"
    assert row["id"] == "bls:LNS14000000:2026-05"


def test_ingest_no_new_release_when_already_seen():
    pts = _pts("LNS14000000", [4.0, 4.1], start_year=2026, start_month=4)
    ch = _FakeCH(max_period="2026-05")
    svc = EconService(bls=_FakeBls({"LNS14000000": pts}), ch_client=ch)
    assert svc.ingest().releases == 0
    assert not any(i[0] == "news_items" for i in ch.inserts)


def test_history_reads_descending():
    ch = _FakeCH(points_rows=[["2026-05", "May 2026", 4.1], ["2026-04", "April 2026", 4.0]])
    h = EconService(ch_client=ch).history("LNS14000000")
    assert h[0].period == "2026-05" and h[0].value == pytest.approx(4.1)


def test_bea_in_catalog():
    assert "BEA_GDP" in ALL_SERIES and "BEA_PCEPI" in ALL_SERIES
    assert ALL_SERIES["BEA_GDP"].frequency == "Q"
