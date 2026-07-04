"""Unit tests for the durable company-name resolver.

CH + Polygon access is stubbed so these stay pure unit tests (no live tier).
"""
from __future__ import annotations

import app.services.instruments.names as names


def test_resolve_ch_first_then_polygon_on_miss(monkeypatch):
    """Known symbols come from CH; misses are fetched from Polygon exactly
    once and written back; every requested symbol gets an entry."""
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

    out = names.resolve_names(["AAPL", "NVDA", "ZZZZ"])

    # CH hit not re-fetched; both misses fetched exactly once.
    assert set(fetched) == {"NVDA", "ZZZZ"}
    assert out["AAPL"]["description"] == "Apple Inc."
    assert out["NVDA"]["description"] == "NVDA Co"
    # Unresolved symbol kept with an empty description (negative-cached).
    assert out["ZZZZ"]["description"] == ""
    # Every requested symbol is present.
    assert set(out) == {"AAPL", "NVDA", "ZZZZ"}
    # Both misses (including the negative) were written back so they won't
    # hit Polygon again.
    assert set(put) == {"NVDA", "ZZZZ"}


def test_resolve_dedups_and_uppercases(monkeypatch):
    monkeypatch.setattr(names, "ensure_table", lambda: None)
    monkeypatch.setattr(names, "_ch_get", lambda syms: {})
    monkeypatch.setattr(names, "_ch_put", lambda recs: None)
    calls = []
    monkeypatch.setattr(names, "_polygon_fetch", lambda s: calls.append(s) or None)

    out = names.resolve_names(["aapl", "AAPL", " aapl "])
    assert list(out) == ["AAPL"]
    assert calls == ["AAPL"]  # fetched once despite three spellings


def test_resolve_empty_input(monkeypatch):
    assert names.resolve_names([]) == {}
    assert names.resolve_names(["", "  "]) == {}
