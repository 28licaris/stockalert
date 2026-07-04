"""Unit tests for the durable company-name resolver.

CH + Polygon access is stubbed so these stay pure unit tests (no live tier).

Architecture under test:
  - resolve_names = READ path — ClickHouse only, never touches the provider.
  - warm          = WRITE path — the only Polygon caller; fills CH.
"""
from __future__ import annotations

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
