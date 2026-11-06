"""scripts/rebuild_ch_from_lake.py batch mode: one lake read per batch, and a failed
batch falls back to the per-symbol path instead of recording silent zeros."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _loader():
    spec = importlib.util.spec_from_file_location("rebuild_loader", ROOT / "scripts" / "rebuild_ch_from_lake.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rebuild_loader"] = mod  # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_batch_returns_one_result_per_symbol_with_the_batch_counts(monkeypatch):
    m = _loader()
    monkeypatch.setattr(m, "fill_ch_from_lake_batch_sync", lambda syms, s, e, source_tag: {"AAA": 7, "BBB": 0})
    out = m._fill_batch(["AAA", "BBB"], T, T)
    assert [(r.symbol, r.rows_inserted, r.error) for r in out] == [("AAA", 7, None), ("BBB", 0, None)]


def test_a_failed_batch_falls_back_to_the_per_symbol_path(monkeypatch):
    m = _loader()

    def boom(*a, **k):
        raise RuntimeError("batch exploded")

    monkeypatch.setattr(m, "fill_ch_from_lake_batch_sync", boom)
    monkeypatch.setattr(m, "fill_ch_from_lake_sync", lambda sym, s, e, source_tag: 3)
    out = m._fill_batch(["AAA", "BBB"], T, T)
    assert [(r.symbol, r.rows_inserted) for r in out] == [("AAA", 3), ("BBB", 3)]


def test_a_symbol_that_also_fails_per_symbol_is_reported_as_failed(monkeypatch):
    m = _loader()

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(m, "fill_ch_from_lake_batch_sync", boom)
    monkeypatch.setattr(m, "fill_ch_from_lake_sync", boom)
    out = m._fill_batch(["AAA"], T, T)
    assert out[0].error and "RuntimeError" in out[0].error and not out[0].succeeded


# ── preflight: an empty window is a warning; a broken lake is still a failure ──


def _drive(monkeypatch, m, *, preflight_rows, reachable, others=None, argv=None):
    """Run main() with every collaborator faked. Returns (exit_code, fills_after_preflight)."""
    import argparse
    from datetime import date

    sym_results = others or {}
    fills = []

    def fill_one(symbol, s, e):
        fills.append(symbol)
        if symbol == "FIRST":
            return m.SymbolResult(symbol=symbol, rows_inserted=preflight_rows)
        return m.SymbolResult(symbol=symbol, rows_inserted=sym_results.get(symbol, 5))

    monkeypatch.setattr(m, "_fill_one", fill_one)
    monkeypatch.setattr(m, "_fill_batch", lambda syms, s, e: [fill_one(x, s, e) for x in syms])
    monkeypatch.setattr(m, "_lake_reachable", lambda: reachable)
    monkeypatch.setattr(m, "_resolve_symbols", lambda spec: ["FIRST", "AAA", "BBB"])
    monkeypatch.setattr(m, "_ch_ohlcv_row_count", lambda: 0)
    ns = argparse.Namespace(
        symbols="x", since=date(2013, 8, 8), until=date(2019, 8, 7), parallelism=1, batch_symbols=2,
        wipe=False, dry_run=False, out_json=None,
    )
    monkeypatch.setattr(m, "_build_parser", lambda: type("P", (), {"parse_args": lambda self: ns})())
    return m.main(), fills


def test_empty_preflight_with_a_healthy_lake_continues_and_warns(monkeypatch, capsys):
    """The RTX case: the lake is fine, it just has nothing for this symbol in the window."""
    m = _loader()
    code, fills = _drive(monkeypatch, m, preflight_rows=0, reachable=(True, "sentinel ok"))
    out = capsys.readouterr().out
    assert code == 0, "an empty symbol must not fail the window"
    assert fills == ["FIRST", "AAA", "BBB"], "the remaining symbols are still loaded"
    assert "ok_with_warnings" in out and "FIRST" in out and "empty_symbols:     1" in out


def test_empty_preflight_with_an_unreadable_lake_still_aborts(monkeypatch, capsys):
    m = _loader()
    code, fills = _drive(monkeypatch, m, preflight_rows=0, reachable=(False, "sentinel read of SPY failed: AccessDenied"))
    out = capsys.readouterr().out
    assert code == 2 and fills == ["FIRST"], "nothing else is attempted"
    assert "status:            fail" in out and "AccessDenied" in out


def test_a_healthy_preflight_is_unchanged(monkeypatch, capsys):
    m = _loader()
    code, fills = _drive(monkeypatch, m, preflight_rows=100, reachable=(True, ""))
    assert code == 0 and "status:            ok" in capsys.readouterr().out


def test_symbols_with_no_rows_are_listed_not_silent(monkeypatch, capsys):
    m = _loader()
    code, _ = _drive(monkeypatch, m, preflight_rows=100, reachable=(True, ""), others={"BBB": 0})
    out = capsys.readouterr().out
    assert code == 0 and "empty_symbols:     1" in out and "BBB" in out


def test_lake_reachable_requires_rows_from_the_sentinel(monkeypatch):
    import types

    m = _loader()
    mod = types.ModuleType("app.services.readers.read_arrow")

    class Arr:
        def __init__(self, n):
            self.num_rows = n

    mod.read_arrow = lambda sym, a, b: Arr(0)
    monkeypatch.setitem(sys.modules, "app.services.readers.read_arrow", mod)
    ok, why = m._lake_reachable()
    assert not ok and "0 rows" in why

    mod.read_arrow = lambda sym, a, b: Arr(42)
    assert m._lake_reachable()[0]

    def boom(*a):
        raise PermissionError("denied")

    mod.read_arrow = boom
    ok, why = m._lake_reachable()
    assert not ok and "PermissionError" in why
