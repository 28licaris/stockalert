"""
Unit tests for `/api/v1/jobs` and `/api/v1/jobs/{name}/run`.

The job_registry singleton is replaced with a fresh JobRegistry per
test via monkeypatch, so the production registry isn't mutated.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with FastAPI lifespan disabled + a clean JobRegistry."""
    from app.main_api import app
    from app.services.jobs import service as jobs_module
    from app.services.jobs.service import JobRegistry
    from app.api import routes_jobs as routes_jobs_module

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)

    fresh = JobRegistry()
    # The route imports `job_registry` directly; patch BOTH the source
    # module's singleton AND the route's reference so the test sees a
    # clean registry from every angle.
    monkeypatch.setattr(jobs_module, "job_registry", fresh)
    monkeypatch.setattr(routes_jobs_module, "job_registry", fresh)
    monkeypatch.setattr(jobs_module, "fetch_last_runs", lambda: {})

    return TestClient(app)


def _register_simple_job(client: TestClient, *, name: str = "alpha", with_callable: bool = True) -> None:
    from app.api import routes_jobs as r

    async def _noop() -> None:
        return None

    r.job_registry.register(
        name=name,
        display_name=name.title(),
        schedule="daily at 06:00 UTC",
        setting_key="ALPHA_HOUR_UTC",
        run_now=_noop if with_callable else None,
    )


# ─────────────────────────────────────────────────────────────────────
# GET /api/v1/jobs
# ─────────────────────────────────────────────────────────────────────


def test_list_returns_registered_jobs(app_client: TestClient) -> None:
    _register_simple_job(app_client, name="alpha")
    _register_simple_job(app_client, name="beta", with_callable=False)

    r = app_client.get("/api/v1/jobs")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [j["name"] for j in body["jobs"]]
    assert sorted(names) == ["alpha", "beta"]
    alpha = next(j for j in body["jobs"] if j["name"] == "alpha")
    assert alpha["runnable"] is True
    assert alpha["schedule"] == "daily at 06:00 UTC"
    assert alpha["setting_key"] == "ALPHA_HOUR_UTC"
    beta = next(j for j in body["jobs"] if j["name"] == "beta")
    assert beta["runnable"] is False


def test_list_empty_when_no_jobs_registered(app_client: TestClient) -> None:
    r = app_client.get("/api/v1/jobs")
    assert r.status_code == 200
    assert r.json()["jobs"] == []


def test_list_published_response_model(app_client: TestClient) -> None:
    """OpenAPI must declare the JobListing schema so the cockpit can codegen it."""
    spec = app_client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "JobListing" in schemas
    assert "JobMetadata" in schemas
    assert "JobRunResult" in schemas


# ─────────────────────────────────────────────────────────────────────
# POST /api/v1/jobs/{name}/run
# ─────────────────────────────────────────────────────────────────────


def test_run_unknown_returns_404(app_client: TestClient) -> None:
    r = app_client.post("/api/v1/jobs/nope/run")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"


def test_run_not_runnable_returns_409(app_client: TestClient) -> None:
    _register_simple_job(app_client, name="alpha", with_callable=False)
    r = app_client.post("/api/v1/jobs/alpha/run")
    assert r.status_code == 409
    assert r.json()["code"] == "not_runnable"


def test_run_started_returns_200(app_client: TestClient) -> None:
    _register_simple_job(app_client, name="alpha")
    r = app_client.post("/api/v1/jobs/alpha/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "started"
    assert body["started_at"] is not None
    assert body["job"] == "alpha"


def test_run_double_click_returns_409_already_running(
    app_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second POST while a run is in flight returns 409
    `already_running` (per-job lock prevents overlap).

    Sync TestClient + fire-and-forget background tasks are awkward to
    interleave, so this test simulates "in flight" by directly holding
    the entry's lock from a background task while the endpoint is
    hit. The lock primitive is what the production code checks; the
    test exercises that contract end-to-end through the route.
    """
    from app.api import routes_jobs as r

    _register_simple_job(app_client, name="alpha")

    # Acquire the lock from a background asyncio loop, hold it across
    # the POST, then release.
    holder_started = asyncio.Event()
    release = asyncio.Event()

    async def _hold_lock() -> None:
        entry = r.job_registry._jobs["alpha"]  # type: ignore[attr-defined]
        async with entry.get_lock():
            holder_started.set()
            await release.wait()

    loop = asyncio.new_event_loop()

    import threading

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_hold_lock())
        loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    # Spin briefly until the holder reports it acquired the lock.
    for _ in range(200):
        if holder_started.is_set():
            break
        import time as _time
        _time.sleep(0.005)
    assert holder_started.is_set(), "background holder failed to start"
    assert r.job_registry.is_running("alpha") is True

    response = app_client.post("/api/v1/jobs/alpha/run")
    assert response.status_code == 409
    assert response.json()["code"] == "already_running"

    # Drain.
    loop.call_soon_threadsafe(release.set)
    t.join(timeout=2.0)
