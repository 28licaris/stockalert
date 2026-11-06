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
