"""CLI contract for scripts/ensure_daily_history.py (the service is faked)."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from app.services.ingest.schwab_daily import Coverage, DailyEnsureResult

_spec = importlib.util.spec_from_file_location(
    "ensure_daily_history_cli", Path(__file__).resolve().parent.parent / "ensure_daily_history.py"
)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _fake(results):
    seen = {}

    async def ensure(symbols, **kw):
        seen["symbols"], seen["kw"] = symbols, kw
        return [results[s] for s in symbols]

    return ensure, seen


def test_all_ok_exits_zero_and_passes_options_through(capsys):
    ensure, seen = _fake({"SCCO": DailyEnsureResult("SCCO", "ok", 700, "2023 to 2026"),
                          "SPY": DailyEnsureResult("SPY", "already_covered", 0, "5000 sessions stored")})
    rc = cli.main(["--symbols", "scco, spy", "--years", "2", "--min-sessions", "300"], ensure=ensure)
    out = capsys.readouterr().out
    assert rc == 0 and "2/2 ok" in out
    assert seen["symbols"] == ["SCCO", "SPY"] and seen["kw"] == {"min_sessions": 300, "years": 2}


def test_any_failure_exits_one_and_names_the_symbol(capsys):
    ensure, _ = _fake({"AAA": DailyEnsureResult("AAA", "ok", 10),
                       "ZZZZ": DailyEnsureResult("ZZZZ", "empty", 0, "Schwab has no price history")})
    rc = cli.main(["--symbols", "AAA,ZZZZ"], ensure=ensure)
    out = capsys.readouterr().out
    assert rc == 1 and "NOT ok: ZZZZ" in out and "Schwab has no price history" in out


def test_dry_run_reads_coverage_and_writes_nothing(capsys):
    class Store:
        def coverage(self, symbols):
            return {"SCCO": Coverage(56, date(2026, 8, 12), date(2026, 11, 2))}

    async def must_not_run(*a, **k):
        raise AssertionError("dry run must not fetch")

    rc = cli.main(["--symbols", "SCCO,NEW", "--dry-run"], ensure=must_not_run, store=Store())
    out = capsys.readouterr().out
    assert rc == 0 and "WOULD FETCH" in out and "56" in out


def test_no_symbols_is_an_error(capsys):
    assert cli.main(["--symbols", " , "], ensure=None) == 1
