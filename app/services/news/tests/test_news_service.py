"""Unit tests for NewsIngestService — fully injected, no network/CH."""
from __future__ import annotations

from datetime import datetime, timezone

from app.providers.edgar import EdgarFiling
from app.services.news.service import _NEWS_COLUMNS, NewsIngestService


class _FakeEdgar:
    def __init__(self, filings, cik_to_ticker):
        self._filings = filings
        self._map = cik_to_ticker

    def latest_filings(self, form_types, count):
        return self._filings

    def ticker_for_cik(self, cik):
        return self._map.get(str(int(cik))) if cik else None


class _FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, table, data, column_names=None):
        self.inserts.append((table, data, column_names))


def _filing(accession, cik, form="8-K"):
    return EdgarFiling(
        accession=accession, form_type=form, company="Co",
        cik=cik, title=f"{form} - Co ({cik})", url="https://sec.gov/x",
        published_at=datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc),
    )


def _service(filings, cik_map, universe):
    ch = _FakeCH()
    svc = NewsIngestService(
        edgar=_FakeEdgar(filings, cik_map),
        ch_client=ch,
        universe_resolver=lambda: universe,
    )
    return svc, ch


def test_keeps_only_universe_symbols_and_stores_unenriched():
    filings = [
        _filing("acc-aapl", "320193"),    # AAPL — in universe
        _filing("acc-tsla", "1318605"),   # TSLA — not in universe
        _filing("acc-none", "9999999"),   # no ticker mapping
    ]
    cik_map = {"320193": "AAPL", "1318605": "TSLA"}  # 9999999 absent
    svc, ch = _service(filings, cik_map, ["AAPL", "NVDA"])

    res = svc.ingest_filings()

    assert res.fetched == 3
    assert res.matched == 1
    assert res.stored == 1
    assert res.skipped_not_universe == 1   # TSLA
    assert res.skipped_no_ticker == 1      # 9999999

    table, rows, cols = ch.inserts[0]
    assert table == "news_items"
    assert cols == _NEWS_COLUMNS
    row = dict(zip(cols, rows[0]))
    assert row["symbol"] == "AAPL"
    assert row["id"] == "acc-aapl"
    assert row["source"] == "edgar"
    assert row["summary"] == "" and row["enriched"] == 0
    assert row["materiality"] == "unrated"


def test_no_relevant_filings_does_not_insert():
    svc, ch = _service([_filing("acc-x", "1318605")], {"1318605": "TSLA"}, ["AAPL"])
    res = svc.ingest_filings()
    assert res.matched == 0 and res.stored == 0
    assert ch.inserts == []                # no empty insert call


def test_universe_match_is_case_insensitive():
    svc, ch = _service([_filing("acc-a", "320193")], {"320193": "aapl"}, ["AAPL"])
    res = svc.ingest_filings()
    assert res.stored == 1
    assert dict(zip(_NEWS_COLUMNS, ch.inserts[0][1][0]))["symbol"] == "AAPL"
