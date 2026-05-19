"""
Unit tests for app.services.jobs (JobRegistry + audit_run).

The CH client + ingestion_runs reads are patched so these tests run
without ClickHouse. Integration tests covering the real ingestion_runs
write live behind the `integration` marker (TBD).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.jobs import service as jobs_module
from app.services.jobs.service import JobRegistry, audit_run


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> JobRegistry:
    """Fresh registry per test — never mutate the module singleton."""
    return JobRegistry()


@pytest.fixture
def no_ingestion_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the ingestion_runs read so list() doesn't need CH."""
    monkeypatch.setattr(
        jobs_module, "fetch_last_runs", lambda: {},
    )


# ─────────────────────────────────────────────────────────────────────
# Registration + list()
# ─────────────────────────────────────────────────────────────────────


def test_register_and_list_returns_metadata(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    registry.register(
        name="alpha",
        display_name="Alpha job",
        schedule="every 5 min",
        run_now=lambda: None,
    )
    registry.register(
        name="beta",
        display_name="Beta job",
        schedule="daily at 06:00 UTC",
        setting_key="BETA_HOUR_UTC",
    )
    items = registry.list()

    # Sorted by display_name
    assert [m.name for m in items] == ["alpha", "beta"]
    assert items[0].schedule == "every 5 min"
    assert items[0].runnable is True
    assert items[1].setting_key == "BETA_HOUR_UTC"
    assert items[1].runnable is False  # no run_now registered


def test_register_is_idempotent(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    """Re-registering replaces the prior entry — useful for hot-reload."""
    registry.register(name="x", display_name="X v1", schedule="daily")
    registry.register(name="x", display_name="X v2", schedule="hourly")
    items = registry.list()
    assert len(items) == 1
    assert items[0].display_name == "X v2"
    assert items[0].schedule == "hourly"


def test_register_rejects_empty_name(registry: JobRegistry) -> None:
    with pytest.raises(ValueError):
        registry.register(name="", display_name="bad", schedule="daily")


def test_list_joins_ingestion_runs(
    registry: JobRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jobs_module,
        "fetch_last_runs",
        lambda: {
            "alpha": {
                "last_success": "2026-05-19T20:00:00Z",
                "last_run_at": "2026-05-19T20:00:00Z",
                "last_status": "ok",
                "last_error": None,
            },
            "beta": {
                "last_success": None,
                "last_run_at": "2026-05-19T19:00:00Z",
                "last_status": "error",
                "last_error": "schwab 503",
            },
        },
    )
    registry.register(name="alpha", display_name="Alpha", schedule="every 5 min", run_now=lambda: None)
    registry.register(name="beta", display_name="Beta", schedule="daily")

    items = {m.name: m for m in registry.list()}
    assert items["alpha"].last_success == "2026-05-19T20:00:00Z"
    assert items["alpha"].last_status == "ok"
    assert items["beta"].last_success is None
    assert items["beta"].last_status == "error"
    assert items["beta"].last_error == "schwab 503"


def test_list_marks_running(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=lambda: None)
    # Manually acquire the entry's lock to simulate "in-flight".
    entry = registry._jobs["alpha"]  # type: ignore[attr-defined]
    lock = entry.get_lock()

    async def _run_with_lock() -> bool:
        async with lock:
            items = registry.list()
            return items[0].running and items[0].last_status == "running"

    assert asyncio.run(_run_with_lock()) is True


def test_list_unknown_status_when_no_runs(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=lambda: None)
    items = registry.list()
    assert items[0].last_status == "idle"
    assert items[0].last_success is None


# ─────────────────────────────────────────────────────────────────────
# run_now: dispatch + lock semantics
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_unknown_returns_not_found(registry: JobRegistry) -> None:
    result = await registry.run_now("nope")
    assert result.status == "not_found"


@pytest.mark.asyncio
async def test_run_now_no_callable_returns_not_runnable(
    registry: JobRegistry,
) -> None:
    registry.register(name="alpha", display_name="Alpha", schedule="daily")
    result = await registry.run_now("alpha")
    assert result.status == "not_runnable"


@pytest.mark.asyncio
async def test_run_now_fires_async_callable(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    seen: list[int] = []

    async def _spy() -> None:
        seen.append(1)

    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=_spy)

    result = await registry.run_now("alpha")
    assert result.status == "started"
    assert result.started_at is not None

    # Give the fire-and-forget task a tick to run.
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)

    assert seen == [1]


@pytest.mark.asyncio
async def test_run_now_fires_sync_callable_via_thread(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    seen: list[int] = []

    def _spy() -> None:
        seen.append(1)

    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=_spy)

    await registry.run_now("alpha")
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen == [1]


@pytest.mark.asyncio
async def test_run_now_already_running_refuses(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    """A second click while the first run is in flight returns
    `already_running` (per-job lock prevents overlap)."""
    started = asyncio.Event()
    finish = asyncio.Event()

    async def _slow() -> None:
        started.set()
        await finish.wait()

    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=_slow)

    first = await registry.run_now("alpha")
    assert first.status == "started"
    await started.wait()

    second = await registry.run_now("alpha")
    assert second.status == "already_running"

    finish.set()
    # Let the slow task drain.
    for _ in range(50):
        if not registry.is_running("alpha"):
            break
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_run_now_callable_exception_does_not_propagate(
    registry: JobRegistry, no_ingestion_runs: None,
) -> None:
    async def _boom() -> None:
        raise RuntimeError("planned failure")

    registry.register(name="alpha", display_name="Alpha", schedule="daily", run_now=_boom)
    result = await registry.run_now("alpha")
    assert result.status == "started"  # background task started cleanly
    # Drain the task.
    for _ in range(50):
        if not registry.is_running("alpha"):
            break
        await asyncio.sleep(0.01)
    # Lock released even though the callable raised.
    assert registry.is_running("alpha") is False


# ─────────────────────────────────────────────────────────────────────
# audit_run context manager
# ─────────────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []

    def insert(self, table: str, rows: list[list[Any]], *, column_names: list[str]) -> None:
        for r in rows:
            self.inserts.append(dict(zip(column_names, r)))


@pytest.mark.asyncio
async def test_audit_run_writes_ok_on_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("app.db.client.get_client", lambda: fake)

    async with audit_run("test_job"):
        pass

    assert len(fake.inserts) == 1
    row = fake.inserts[0]
    assert row["job_name"] == "test_job"
    assert row["status"] == "ok"
    assert row["per_provider_errors_json"] == ""
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


@pytest.mark.asyncio
async def test_audit_run_writes_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr("app.db.client.get_client", lambda: fake)

    with pytest.raises(RuntimeError):
        async with audit_run("test_job"):
            raise RuntimeError("kaboom")

    assert len(fake.inserts) == 1
    row = fake.inserts[0]
    assert row["status"] == "error"
    assert "kaboom" in row["per_provider_errors_json"]


@pytest.mark.asyncio
async def test_audit_run_tolerates_ch_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the CH client is unavailable, audit_run logs but doesn't raise
    — the work itself still completes."""

    def _no_client():
        raise RuntimeError("CH down")

    monkeypatch.setattr("app.db.client.get_client", _no_client)

    # Should NOT raise.
    async with audit_run("test_job"):
        pass
