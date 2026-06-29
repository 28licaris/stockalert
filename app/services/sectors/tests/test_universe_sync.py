"""Tests for the tracked-instruments → universe reconciler."""
from __future__ import annotations

import asyncio

import app.services.stream as stream_mod
import app.services.universe as universe_mod
from app.services.sectors import universe_sync


def test_tracked_symbols_covers_sectors_and_theme_members():
    syms = set(universe_sync.tracked_symbols())
    assert {"XLK", "XLV", "SPY"} <= syms      # sector ETFs + benchmark
    assert {"NEM", "GOLD", "AG"} <= syms      # miner basket members
    assert "MINERS" not in syms               # the group id is not a tradable symbol


def test_ensure_adds_only_missing(monkeypatch):
    monkeypatch.setattr(universe_mod, "get_active_universe", lambda: ["XLK", "SPY"])
    added: list[str] = []

    class _FakeStream:
        def add(self, sym, **kw):  # noqa: ANN001
            added.append(sym)
            return {"changed": [sym]}

    monkeypatch.setattr(stream_mod, "stream_service", _FakeStream())

    res = asyncio.run(universe_sync.ensure_tracked_in_universe(tip_fill=False))

    assert "XLK" not in added           # already active → skipped
    assert {"XLV", "NEM", "AG"} <= set(added)  # missing → added
    assert set(res["added"]) == set(added)
    assert res["tracked"] == len(universe_sync.tracked_symbols())


def test_ensure_noop_when_all_active(monkeypatch):
    monkeypatch.setattr(
        universe_mod, "get_active_universe",
        lambda: list(universe_sync.tracked_symbols()),
    )
    called = {"add": 0}

    class _FakeStream:
        def add(self, *a, **k):
            called["add"] += 1

    monkeypatch.setattr(stream_mod, "stream_service", _FakeStream())

    res = asyncio.run(universe_sync.ensure_tracked_in_universe(tip_fill=False))
    assert res["added"] == []
    assert called["add"] == 0  # nothing to do — cheap no-op
