"""Unit tests for the durable company-name resolver.

CH + Polygon access is stubbed so these stay pure unit tests (no live tier).

Architecture under test:
  - resolve_names = READ path — ClickHouse only, never touches the provider.
  - warm          = WRITE path — the only Polygon caller; fills CH.
"""
from __future__ import annotations

import pytest

import app.services.instruments.names as names


# ── read path: resolve_names is CH-only ──────────────────────────────

def test_resolve_names_is_ch_only(monkeypatch):
    """resolve_names reads ClickHouse and NEVER calls the provider; misses
    come back with an empty description, and every symbol gets an entry."""
    monkeypatch.setattr(names, "_ch_get", lambda syms: {
        "AAPL": {"description": "Apple Inc.", "exchange": "XNAS", "asset_type": "CS"},
    })
    # Fail loudly if the read path ever touches the provider.
    monkeypatch.setattr(names, "_polygon_fetch", lambda s: (_ for _ in ()).throw(
        AssertionError("read path must not call the provider")))

    out = names.resolve_names(["AAPL", "NVDA", "ZZZZ"])

    assert out["AAPL"]["description"] == "Apple Inc."
    assert out["NVDA"]["description"] == ""   # not cached -> empty, no fetch
    assert out["ZZZZ"]["description"] == ""
    assert set(out) == {"AAPL", "NVDA", "ZZZZ"}


def test_resolve_names_dedups_and_uppercases(monkeypatch):
    monkeypatch.setattr(names, "_ch_get", lambda syms: {})
    out = names.resolve_names(["aapl", "AAPL", " aapl "])
    assert list(out) == ["AAPL"]


def test_resolve_names_empty_input():
    assert names.resolve_names([]) == {}
    assert names.resolve_names(["", "  "]) == {}


# ── write path: warm fetches from Polygon + caches ───────────────────

def test_warm_fetches_misses_and_negative_caches(monkeypatch):
    """warm skips already-cached symbols, fetches the rest from Polygon once,
    and writes back — including negatives so unknowns aren't re-fetched."""
    monkeypatch.setattr(names, "ensure_table", lambda: None)
    monkeypatch.setattr(names, "_ch_get", lambda syms: {
        "AAPL": {"description": "Apple Inc.", "exchange": "XNAS", "asset_type": "CS"},
    })
    fetched, put = [], {}
    monkeypatch.setattr(names, "_ch_put", lambda recs: put.update(recs))

    def fake_poly(sym):
        fetched.append(sym)
        return {"description": f"{sym} Co", "exchange": "XNAS", "asset_type": "CS"} if sym == "NVDA" else None

    monkeypatch.setattr(names, "_polygon_fetch", fake_poly)

    named = names.warm(["AAPL", "NVDA", "ZZZZ"])

    assert set(fetched) == {"NVDA", "ZZZZ"}      # cached AAPL not re-fetched
    assert set(put) == {"NVDA", "ZZZZ"}          # both misses written (incl. negative)
    assert put["ZZZZ"]["description"] == ""      # negative cached
    assert named == 2                            # AAPL (cached) + NVDA (fetched)


def test_warm_refresh_refetches_cached(monkeypatch):
    """refresh=True re-fetches even already-cached symbols (catches renames)."""
    monkeypatch.setattr(names, "ensure_table", lambda: None)
    monkeypatch.setattr(names, "_ch_get", lambda syms: {
        "AAPL": {"description": "OLD NAME", "exchange": "", "asset_type": ""},
    })
    put = {}
    monkeypatch.setattr(names, "_ch_put", lambda recs: put.update(recs))
    monkeypatch.setattr(names, "_polygon_fetch",
                        lambda s: {"description": "Apple Inc.", "exchange": "XNAS", "asset_type": "CS"})

    names.warm(["AAPL"], refresh=True)
    assert put["AAPL"]["description"] == "Apple Inc."  # re-fetched despite being cached


# ─────────────────────────────────────────────────────────────────────
# Transient-vs-not-found (2026-08-06). A 429 during a bulk warm was being
# cached as "this ticker has no name", permanently blanking 552 symbols
# (AMZN, AMD, ABBV…) on the stream/watchlist pages.
# ─────────────────────────────────────────────────────────────────────


def test_non_200_raises_unavailable_and_is_not_cached(monkeypatch):
    """Rate limits / 5xx must NOT become permanent negative cache entries."""
    from unittest.mock import MagicMock
    import app.services.instruments.names as names

    from app.config import settings
    monkeypatch.setattr(settings, "polygon_api_key", "k", raising=False)
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: MagicMock(status_code=429, json=lambda: {})
    )
    with pytest.raises(names._NameFetchUnavailable):
        names._polygon_fetch("AMZN")

    written: dict = {}
    monkeypatch.setattr(names, "_ch_get", lambda syms: {})
    monkeypatch.setattr(names, "_ch_put", lambda rows: written.update(rows))
    named = names.warm(["AMZN"])

    assert written == {}, "a transient failure must not be cached"
    assert named == 0


def test_200_with_no_match_is_cached_as_negative(monkeypatch):
    """A genuine not-found SHOULD be cached so we stop re-asking."""
    from unittest.mock import MagicMock
    import app.services.instruments.names as names

    from app.config import settings
    monkeypatch.setattr(settings, "polygon_api_key", "k", raising=False)
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: MagicMock(status_code=200, json=lambda: {"results": []}),
    )
    assert names._polygon_fetch("NOTATICKER") is None

    written: dict = {}
    monkeypatch.setattr(names, "_ch_get", lambda syms: {})
    monkeypatch.setattr(names, "_ch_put", lambda rows: written.update(rows))
    names.warm(["NOTATICKER"])

    assert "NOTATICKER" in written and written["NOTATICKER"]["description"] == ""


# ─────────────────────────────────────────────────────────────────────
# 2026-08-08 incident: two consecutive nightly runs (refresh=True, 224-way
# concurrent per-symbol lookups) wiped 216/219 and then 209/224 previously
# -correct names in a single run. Under sustained load Polygon returned
# HTTP 200 with an EMPTY results list for most requests — not a 429, so
# the transient-failure guard never fired — and that read as "confirmed
# not found", overwriting names we already knew were right.
# ─────────────────────────────────────────────────────────────────────


def test_refresh_true_never_downgrades_a_known_name_to_blank(monkeypatch):
    """The core invariant: a single per-request 'not found' must not erase
    a name we already trust, even on a refresh=True sweep."""
    from unittest.mock import MagicMock
    import app.services.instruments.names as names

    from app.config import settings
    monkeypatch.setattr(settings, "polygon_api_key", "k", raising=False)
    # Simulate Polygon's observed soft-throttle: HTTP 200, empty results —
    # not a 429, so this does NOT raise _NameFetchUnavailable.
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: MagicMock(status_code=200, json=lambda: {"results": []}),
    )

    prior_state = {"AMZN": {"description": "Amazon.Com Inc", "exchange": "XNAS", "asset_type": "CS"}}
    written: dict = {}
    monkeypatch.setattr(names, "_ch_get", lambda syms: dict(prior_state))
    monkeypatch.setattr(names, "_ch_put", lambda rows: written.update(rows))

    names.warm(["AMZN"], refresh=True)

    assert "AMZN" not in written, "a 'not found' answer downgraded a known-good name"


def test_refresh_true_still_accepts_a_real_update(monkeypatch):
    """The guard must not freeze names forever — an actual resolved name
    (e.g. after a corporate rename) still overwrites the old one."""
    from unittest.mock import MagicMock
    import app.services.instruments.names as names

    from app.config import settings
    monkeypatch.setattr(settings, "polygon_api_key", "k", raising=False)
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: MagicMock(
            status_code=200,
            json=lambda: {"results": [{"name": "Amazon Renamed Inc",
                                        "primary_exchange": "XNAS", "type": "CS"}]},
        ),
    )
    monkeypatch.setattr(names, "_ch_get",
                        lambda syms: {"AMZN": {"description": "Amazon.Com Inc",
                                               "exchange": "XNAS", "asset_type": "CS"}})
    written: dict = {}
    monkeypatch.setattr(names, "_ch_put", lambda rows: written.update(rows))

    names.warm(["AMZN"], refresh=True)

    assert written["AMZN"]["description"] == "Amazon Renamed Inc"


def test_refresh_names_uses_the_bulk_crawl_not_the_per_symbol_hammer(monkeypatch):
    """The nightly job must go through bulk_refresh_from_reference() (a
    paced, complete crawl) rather than warm(syms, refresh=True) (the
    224-way concurrent sweep that caused the incident)."""
    import app.services.instruments.names as names

    calls: list[str] = []
    monkeypatch.setattr(names, "bulk_refresh_from_reference",
                        lambda **kw: calls.append("bulk") or 5000)
    monkeypatch.setattr(names, "_stream_universe_symbols", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(names, "_ch_get",
                        lambda syms: {s: {"description": "X"} for s in syms})
    monkeypatch.setattr(names, "warm", lambda syms, **kw: calls.append(("warm", tuple(syms))) or 0)

    result = names.refresh_names()

    assert calls[0] == "bulk"
    assert result["bulk_written"] == 5000
    assert result["still_missing_after_bulk"] == 0
