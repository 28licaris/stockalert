"""HTTP API for the scheduled-jobs registry.

Mounted at `/api/v1/jobs/*`. Drives the cockpit Status page's
"Scheduled jobs" section.

GET  /api/v1/jobs              -> list with schedule + last_success
POST /api/v1/jobs/{name}/run   -> trigger a manual run (returns immediately)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas.jobs import JobListing, JobRun, JobRunHistory, JobRunResult
from app.services.jobs import job_registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/jobs", response_model=JobListing)
async def list_jobs() -> JobListing:
    """Catalog of background jobs + last-run state.

    Joins the in-memory registry with `ingestion_runs` for the
    last_success / last_run_at / last_status fields. Polled by the
    cockpit's Status page at the same cadence as health/services.
    """
    items = await asyncio.to_thread(job_registry.list)
    return JobListing(jobs=items)


@router.get("/jobs/{name}/runs", response_model=JobRunHistory)
async def job_runs(name: str, limit: int = 10) -> JobRunHistory:
    """Recent run history for one job (newest first), for the Status page's
    per-job log view. `limit` is clamped to [1, 50]."""
    if job_registry.get(name) is None:
        raise HTTPException(404, f"no job registered with name {name!r}")
    lim = max(1, min(50, int(limit)))
    from app.services.jobs.repo import fetch_recent_runs

    rows = await asyncio.to_thread(fetch_recent_runs, name, lim)
    return JobRunHistory(job=name, runs=[JobRun(**r) for r in rows])


@router.post("/jobs/{name}/run", response_model=JobRunResult)
async def run_job(name: str) -> JobRunResult:
    """Trigger a manual run for the named job.

    Returns immediately with `status='started'` (the job runs in the
    background — poll GET /jobs for completion), or a structured
    refusal:

      - `status='not_found'`         -> 404
      - `status='not_runnable'`      -> 409 (registered but no run_now callable)
      - `status='already_running'`   -> 409
    """
    result = await job_registry.run_now(name)
    if result.status == "not_found":
        raise HTTPException(404, result.detail or "job not found")
    if result.status in ("not_runnable", "already_running"):
        # 409 Conflict communicates "cannot fulfill request given current state".
        raise HTTPException(
            409,
            result.detail or f"job {name!r} cannot be triggered right now",
            headers={"X-Error-Code": result.status},
        )
    return result
