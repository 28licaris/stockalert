"""Unit tests for app.services.news.macro — pure Fed RSS parsing, no network."""
from __future__ import annotations

import pytest

from app.services.news.macro import FedError, parse_fed_rss

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>FRB Press Releases</title>
  <item>
    <title>Federal Reserve issues FOMC statement</title>
    <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm</link>
    <pubDate>Wed, 17 Jun 2026 14:00:00 GMT</pubDate>
    <guid>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm</guid>
  </item>
  <item>
    <title>Federal Reserve Board announces approval of an application</title>
    <link>https://www.federalreserve.gov/other.htm</link>
    <pubDate>Tue, 16 Jun 2026 10:00:00 GMT</pubDate>
    <guid>g2</guid>
  </item>
  <item>
    <title>Minutes of the Federal Open Market Committee, May 2026</title>
    <link>https://www.federalreserve.gov/min.htm</link>
    <pubDate>not-a-date</pubDate>
    <guid>g3</guid>
  </item>
</channel></rss>"""


def test_keeps_only_fomc_items():
    items = parse_fed_rss(_RSS)
    assert len(items) == 2                       # statement + minutes; approval dropped
    titles = " ".join(i.title for i in items)
    assert "FOMC statement" in titles
    assert "Minutes of the Federal Open Market Committee" in titles


def test_statement_fields():
    stmt = next(i for i in parse_fed_rss(_RSS) if "statement" in i.title)
    assert stmt.event_type == "fomc"
    assert stmt.url.endswith("monetary20260617a.htm")
    assert stmt.id == stmt.url                    # guid == link here
    assert stmt.published_at is not None and stmt.published_at.year == 2026


def test_bad_pubdate_is_none_not_fatal():
    minutes = next(i for i in parse_fed_rss(_RSS) if i.id == "g3")
    assert minutes.published_at is None


def test_bad_xml_raises():
    with pytest.raises(FedError):
        parse_fed_rss("<nope")
