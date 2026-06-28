"""Unit tests for app.services.news.econ — pure parse/transforms + ingest."""
from __future__ import annotations

import calendar

import pytest

from app.services.news.econ import (
    BLS_SERIES,
    BlsError,
    EconPoint,
    EconService,
    build_release_headline,
    compute_indicator,
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


def _pts(sid, values, start_year=2024, start_month=1):
    pts, y, m = [], start_year, start_month
    for v in values:
        pts.append(EconPoint(sid, y, f"M{m:02d}", calendar.month_name[m], float(v)))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return pts


# ── parsing ──
def test_parse_skips_annual_and_bad_and_sorts():
    out = parse_bls_response(_BLS_JSON)
    pts = out["LNS14000000"]
    assert [p.period for p in pts] == ["M04", "M05"]   # M13 + bad('M03') dropped, ascending
    assert pts[-1].value == 4.1
    assert pts[-1].ym == "2026-05" and pts[-1].label == "May 2026"


def test_parse_raises_on_failure_status():
    with pytest.raises(BlsError):
        parse_bls_response({"status": "REQUEST_NOT_PROCESSED", "message": ["bad"]})


# ── transforms ──
def test_level_indicator():
    ind = compute_indicator(BLS_SERIES["LNS14000000"], _pts("LNS14000000", [4.0, 4.1]))
    assert ind.value == pytest.approx(4.1)
    assert ind.change == pytest.approx(0.1)
    assert ind.value_label == "4.1%"


def test_yoy_indicator():
    # 14 months: index level. yoy[last]=(104/100-1)=4.0; yoy[prev]=(103/100-1)=3.0.
    vals = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 103, 104]
    ind = compute_indicator(BLS_SERIES["CUUR0000SA0"], _pts("CUUR0000SA0", vals))
    assert ind.value == pytest.approx(4.0)
    assert ind.change == pytest.approx(1.0)
    assert ind.value_label == "4.0%"


def test_mom_delta_indicator():
    ind = compute_indicator(BLS_SERIES["CES0000000001"], _pts("CES0000000001", [100, 200, 380]))
    assert ind.value == pytest.approx(180)
    assert ind.change == pytest.approx(80)    # 180 - prior delta(100)
    assert ind.value_label == "+180k"


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

    res = svc.ingest(series_ids=["LNS14000000"])

    assert res.points == 2 and res.releases == 1
    econ = next(i for i in ch.inserts if i[0] == "economic_data")
    news = next(i for i in ch.inserts if i[0] == "news_items")
    assert len(econ[1]) == 2
    row = dict(zip(_NEWS_COLUMNS, news[1][0]))
    assert row["source"] == "bls" and row["event_type"] == "econ"
    assert row["symbol"] == "" and row["enriched"] == 1
    assert row["materiality"] == "high"
    assert row["id"] == "bls:LNS14000000:2026-05"
    assert "Unemployment rate" in row["title"]


def test_ingest_no_new_release_when_period_already_seen():
    pts = _pts("LNS14000000", [4.0, 4.1], start_year=2026, start_month=4)
    ch = _FakeCH(max_period="2026-05")   # latest already stored
    svc = EconService(bls=_FakeBls({"LNS14000000": pts}), ch_client=ch)
    res = svc.ingest(series_ids=["LNS14000000"])
    assert res.releases == 0
    assert not any(i[0] == "news_items" for i in ch.inserts)


def test_history_reads_descending():
    ch = _FakeCH(points_rows=[["2026-05", "May 2026", 4.1], ["2026-04", "April 2026", 4.0]])
    h = EconService(ch_client=ch).history("LNS14000000")
    assert h[0].period == "2026-05" and h[0].value == pytest.approx(4.1)
    assert h[1].period == "2026-04"
