"""The nightly guard and snapshot jobs: gating, schedule math, and that a failure
raises (so audit_run records an error) instead of passing quietly."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.lake_integrity import jobs
from app.services.lake_integrity.schemas import DayGap, GapReport


def _report(family: str, *, missing: int, archived: int, errors: list[str] | None = None) -> GapReport:
    return GapReport(
        family=family, since=date(2026, 11, 1), until=date(2026, 11, 5), apply=True,
        days_checked=1, days_with_gaps=1 if missing else 0, missing_total=missing, archived_total=archived,
        status="error" if errors or archived < missing else ("archived" if missing else "complete"),
        days=[DayGap(day=date(2026, 11, 4), ch_bars=missing, missing=missing, archived=archived)], errors=errors or [],
    )


class FakeArchive:
    def __init__(self, by_family):
        self.by_family, self.calls = by_family, []

    def run(self, family, *, since, until, apply):
        self.calls.append((family.name, apply))
        return self.by_family[family.name]


def run(coro):
    return asyncio.run(coro)


def test_seconds_until_next_run_wraps_to_tomorrow():
    now = datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc)
    assert jobs.seconds_until_next_run(23, now=now) == 23.5 * 3600
    assert jobs.seconds_until_next_run(1, now=now) == 1.5 * 3600


def test_gate_requires_flag_and_bucket(monkeypatch):
    s = SimpleNamespace(lake_guard_enabled=True, ch_state_snapshot_enabled=True, stock_lake_bucket="b")
    monkeypatch.setattr(jobs, "settings", s)
    assert jobs.gate(jobs.GUARD_JOB) == (True, "") and jobs.gate(jobs.SNAPSHOT_JOB) == (True, "")
    s.lake_guard_enabled = False
    assert jobs.gate(jobs.GUARD_JOB) == (False, "LAKE_GUARD_ENABLED=false")
    s.stock_lake_bucket = " "
    assert jobs.gate(jobs.SNAPSHOT_JOB)[0] is False


def test_guard_applies_for_both_families_and_reports_what_it_archived(monkeypatch):
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(lake_guard_lookback_days=14))
    arch = FakeArchive({"equities": _report("equities", missing=5, archived=5), "futures": _report("futures", missing=0, archived=0)})
    monkeypatch.setattr("app.services.lake_integrity.service.LakeArchive.from_settings", classmethod(lambda cls: arch))
    out = run(jobs.run_guard_once())
    assert out == {"rows_written": 5, "missing_found": 5, "lookback_days": 14}
    assert sorted(arch.calls) == [("equities", True), ("futures", True)]


def test_guard_raises_when_a_write_does_not_verify(monkeypatch):
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(lake_guard_lookback_days=14))
    arch = FakeArchive({
        "equities": _report("equities", missing=5, archived=3, errors=["2026-11-04: 2 bar(s) not visible in the lake after append"]),
        "futures": _report("futures", missing=0, archived=0),
    })
    monkeypatch.setattr("app.services.lake_integrity.service.LakeArchive.from_settings", classmethod(lambda cls: arch))
    with pytest.raises(RuntimeError, match="3/5 archived"):
        run(jobs.run_guard_once())


def test_snapshot_job_raises_on_an_incomplete_export_and_does_not_prune(monkeypatch):
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(ch_state_snapshot_keep=14))
    pruned = []
    bad = SimpleNamespace(complete=False, failed={"paper_state": "boom"}, tables=[], files=[])
    snap = SimpleNamespace(export=lambda: bad, prune=lambda keep: pruned.append(keep) or [])
    monkeypatch.setattr("app.services.lake_integrity.snapshot.StateSnapshot.from_settings", classmethod(lambda cls: snap))
    with pytest.raises(RuntimeError, match="incomplete export"):
        run(jobs.run_snapshot_once())
    assert pruned == []  # never delete old backups after a bad one


def test_snapshot_job_summarises_a_complete_export(monkeypatch):
    monkeypatch.setattr(jobs, "settings", SimpleNamespace(ch_state_snapshot_keep=3))
    good = SimpleNamespace(complete=True, failed={}, tables=[SimpleNamespace(rows=10), SimpleNamespace(rows=5)], files=[1])
    snap = SimpleNamespace(export=lambda: good, prune=lambda keep: ["old"] if keep == 3 else [])
    monkeypatch.setattr("app.services.lake_integrity.snapshot.StateSnapshot.from_settings", classmethod(lambda cls: snap))
    assert run(jobs.run_snapshot_once()) == {"rows_written": 15, "tables": 2, "files": 1, "pruned": 1}
