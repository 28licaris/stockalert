"""Unit tests for app.providers.edgar — pure Atom parsing, no network."""
from __future__ import annotations

import pytest

from app.providers.edgar import EdgarClient, EdgarError, parse_latest_filings

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Latest Filings</title>
  <entry>
    <title>8-K - Apple Inc. (0000320193) (Filer)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000075/index.htm"/>
    <category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
    <updated>2026-06-27T16:32:01-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000075</id>
  </entry>
  <entry>
    <title>4 - SMITH JOHN (0001234567) (Reporting)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1234567/x.htm"/>
    <category term="4"/>
    <updated>2026-06-27T14:00:00-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001234567-26-000010</id>
  </entry>
  <entry>
    <title>garbage entry with no id or category</title>
  </entry>
</feed>"""


def test_parse_extracts_8k():
    filings = parse_latest_filings(_FEED)
    f = next(x for x in filings if x.form_type == "8-K")
    assert f.accession == "0000320193-26-000075"
    assert f.cik == "320193"            # zero-stripped
    assert f.company == "Apple Inc."
    assert "Archives/edgar/data/320193" in f.url
    assert f.published_at is not None and f.published_at.year == 2026


def test_parse_extracts_form4():
    filings = parse_latest_filings(_FEED)
    f = next(x for x in filings if x.form_type == "4")
    assert f.accession == "0001234567-26-000010"
    assert f.cik == "1234567"


def test_parse_skips_malformed_entry():
    # 3 entries, one is garbage (no id/category) → only 2 valid.
    assert len(parse_latest_filings(_FEED)) == 2


def test_parse_bad_xml_raises():
    with pytest.raises(EdgarError):
        parse_latest_filings("<not-xml")


def test_client_requires_contact_email_user_agent():
    with pytest.raises(EdgarError):
        EdgarClient(user_agent="StockAlert/1.0")          # no email
    EdgarClient(user_agent="StockAlert/1.0 (ops@x.com)")  # ok


def test_from_settings_constructs():
    # default EDGAR_USER_AGENT contains an email → constructs cleanly.
    assert EdgarClient.from_settings() is not None
