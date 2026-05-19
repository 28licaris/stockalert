"""JobRegistry public contract — what callers (routes, main_api) rely on.

Implementation lives in service.py; callers depend on this Protocol
(or just the module singleton + schemas) and NEVER import service.py
directly. Mirrors the pattern used by `app.services.stream`.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol, Union

from app.services.jobs.schemas import JobMetadata, JobRunResult, RunResult


RunNowCallable = Callable[[], Union[None, Awaitable[None]]]
"""A registered job's manual-trigger callable.

May be a sync function (called via `asyncio.to_thread`) or an async
function (awaited directly). It runs ONE cycle of the job and is
expected to either:

  - audit itself by writing to the CH `ingestion_runs` table, OR
  - be wrapped at registration time with `audit_run(job_name)` so the
    registry can record the cycle uniformly.

The callable should not raise for predictable errors (return early
with a logged warning instead) — the registry's per-job lock release
relies on the callable returning normally OR raising a clean
exception captured as `status='error'`.
"""


class JobRegistryProtocol(Protocol):
    """Singleton registry of all scheduled background jobs.

    The registry does NOT schedule jobs — each background loop owns its
    own scheduling logic in main_api lifespan. The registry's role is:

      1. Catalog jobs so the Status page can list them.
      2. Provide manual `run_now` triggering with per-job locks so a
         scheduled run and an operator-triggered run don't overlap.
      3. Join the catalog with last-success data from `ingestion_runs`.
    """

    def register(
        self,
        *,
        name: str,
        display_name: str,
        schedule: str,
        setting_key: Optional[str] = None,
        run_now: Optional[RunNowCallable] = None,
    ) -> None:
        """Add a job to the registry. Idempotent: re-registering the
        same `name` replaces the prior entry (useful for hot-reload)."""

    def list(self) -> list[JobMetadata]:
        """Return registered jobs, joined with last-run data from
        `ingestion_runs`. Ordered by display_name."""

    def get(self, name: str) -> Optional[JobMetadata]:
        ...

    async def run_now(self, name: str) -> JobRunResult:
        """Fire the job's `run_now` callable in the background.

        Returns immediately with `status='started'` (the actual run
        happens in a fire-and-forget task), `status='already_running'`
        (per-job lock is held), `status='not_found'`, or
        `status='not_runnable'` (no run_now registered).
        """

    def is_running(self, name: str) -> bool:
        ...

    async def _execute_run(self, name: str) -> RunResult:
        """Internal: invoked by run_now's background task. Awaitable
        end-to-end for tests."""
