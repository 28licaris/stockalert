"""Job registry DTOs — the only file other services should import.

Per service_modules.md: schemas + contract are the cross-service
boundary. service.py is implementation detail.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


JobStatus = Literal["idle", "running", "ok", "error", "unknown"]
"""Status for the most recent run.

- ``idle``    -> registered but has never run (no ingestion_runs row).
- ``running`` -> a run is currently in flight (the per-job lock is held).
- ``ok``      -> last completed run finished successfully.
- ``error``   -> last completed run errored (or wrote `status="error"`).
- ``unknown`` -> last-run lookup failed (CH down).
"""


class JobMetadata(BaseModel):
    """One scheduled background job as seen by the Status page."""

    name: str = Field(
        ...,
        description=(
            "Stable identifier used as the `ingestion_runs.job_name` key. "
            "Operator-facing strings live in `display_name`."
        ),
    )
    display_name: str = Field(..., description="Human-friendly name for the UI.")
    schedule: str = Field(
        ...,
        description=(
            "Human-readable cadence (e.g. 'daily at 07:00 UTC', "
            "'every 5 min'). Operators tune via env vars."
        ),
    )
    setting_key: Optional[str] = Field(
        default=None,
        description=(
            "Env var name controlling the schedule, shown as a tooltip "
            "(e.g. 'POLYGON_NIGHTLY_RUN_HOUR_UTC'). None = not configurable."
        ),
    )
    runnable: bool = Field(
        ...,
        description=(
            "True iff a manual `run_now` callable was registered. "
            "Surface gates the play button on the UI."
        ),
    )
    last_success: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 (UTC, with `Z`) of the most recent `status='ok'` run, "
            "or None if no successful run has been recorded."
        ),
    )
    last_run_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 of the most recent run regardless of outcome.",
    )
    last_status: JobStatus = Field(
        default="idle",
        description="Outcome of the most recent run.",
    )
    last_error: Optional[str] = Field(
        default=None,
        description=(
            "Short error message from the most recent failed run "
            "(truncated to 500 chars). None when last run succeeded."
        ),
    )
    running: bool = Field(
        default=False,
        description="True iff a run is currently in flight for this job.",
    )


class JobListing(BaseModel):
    """Result of `JobRegistry.list()` — what GET /api/v1/jobs returns."""

    jobs: list[JobMetadata]


class RunResult(BaseModel):
    """In-process result of one `JobRegistry.run_now` call."""

    job: str
    started_at: str
    finished_at: Optional[str] = None
    status: Literal["ok", "error", "already_running"]
    error: Optional[str] = None


class JobRunResult(BaseModel):
    """Response for POST /api/v1/jobs/{name}/run."""

    job: str = Field(..., description="Job name that was triggered.")
    status: Literal["started", "already_running", "not_found", "not_runnable"]
    started_at: Optional[str] = Field(
        default=None,
        description=(
            "Set when `status='started'`. The job runs in the background; "
            "poll GET /api/v1/jobs for completion."
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        description="Human message (e.g. 'job already running').",
    )
