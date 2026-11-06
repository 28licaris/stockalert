"""scripts/ch_bootstrap.py step orchestration: independent loads stay independent."""
from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _boot():
    spec = importlib.util.spec_from_file_location("ch_bootstrap_mod", ROOT / "scripts" / "ch_bootstrap.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ch_bootstrap_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fns(fail=()):
    ran: list[str] = []

    def make(name):
        def fn(args, ctx):
            ran.append(name)
            if name in fail:
                raise RuntimeError(f"{name} exploded")
            return f"{name} ok"
        return fn

    return {n: make(n) for n in ("schema", "state", "bars", "futures", "daily", "verify")}, ran


def _run(fail):
    m = _boot()
    fns, ran = _fns(fail)
    results, failed = m.run_steps(["schema", "state", "bars", "futures", "daily", "verify"], fns, Namespace(), {})
    return {r["step"]: r for r in results}, failed, ran


def test_all_ok_runs_everything_and_reports_no_failure():
    by, failed, ran = _run(fail=())
    assert not failed and ran == ["schema", "state", "bars", "futures", "daily", "verify"]


def test_a_failed_equity_window_no_longer_cancels_futures_and_daily():
    """The 2026-11-06 incident: one empty RTX window failed `bars`, so futures history and
    the daily table were never loaded."""
    by, failed, ran = _run(fail=("bars",))
    assert failed
    assert not by["bars"]["ok"] and by["futures"]["ok"] and by["daily"]["ok"] and by["verify"]["ok"]
    assert "futures" in ran and "daily" in ran


def test_a_failed_schema_skips_every_load_but_verify_still_runs():
    by, failed, ran = _run(fail=("schema",))
    assert failed
    for step in ("state", "bars", "futures", "daily"):
        assert not by[step]["ok"] and "skipped: needs schema" in by[step]["detail"]
    assert ran == ["schema", "verify"], "verify is the judge and always runs"


def test_a_failed_verify_is_reported_as_failure():
    by, failed, _ = _run(fail=("verify",))
    assert failed and not by["verify"]["ok"]


def test_failures_are_named_in_the_result_detail():
    by, _, _ = _run(fail=("futures",))
    assert "RuntimeError: futures exploded" in by["futures"]["detail"]
