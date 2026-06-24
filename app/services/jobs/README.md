# Job Registry

Catalogs every background loop so the cockpit `/app/status` page can
list them with **schedule**, **last successful run**, and a **play
button** for manual triggering.

## What it owns

| Concern | Where |
|---|---|
| In-memory registry of jobs + per-job `asyncio.Lock` | `service.py` (`JobRegistry`) |
| `audit_run(job_name)` context manager that writes one `ingestion_runs` row per cycle | `service.py` |
| ClickHouse read of `ingestion_runs` for last-success per job | `repo.py` |
| Pydantic DTOs (`JobMetadata`, `JobListing`, `JobRunResult`) | `schemas.py` |

## Public contract

```python
from app.services.jobs import job_registry, audit_run

# main_api lifespan
job_registry.register(
    name="nightly_schwab_refresh",
    display_name="Nightly Schwab refresh",
    schedule=f"daily at {hour:02d}:00 UTC",
    setting_key="SCHWAB_NIGHTLY_RUN_HOUR_UTC",
    run_now=_run_schwab_once,           # async; wrapped with audit_run
)

# Routes
jobs = job_registry.list()              # joins registry + ingestion_runs
result = await job_registry.run_now("nightly_schwab_refresh")
```

## Sticky design rules

1. **No double-audit.** Some loops (e.g. `live_lake_writer`,
   `silver_ohlcv_build`) already write `ingestion_runs` themselves.
   When registering those, the `run_now` callable should be their
   existing one-cycle function — do NOT wrap with `audit_run`. For
   loops that don't self-audit, wrap them.

2. **Per-job lock.** Each entry has an `asyncio.Lock`. `run_now`
   refuses concurrent triggers with `status='already_running'`.
   Scheduled runs that race with a manual click serialize on the
   same lock (or — if the scheduled loop bypasses the registry,
   which the existing ones do today — the manual click skips when
   the scheduled run is mid-flight via the lock).

3. **Background dispatch.** `run_now` returns immediately with
   `status='started'`. The actual run lives in an `asyncio.create_task`.
   Callers poll `list()` to see when it finishes.

4. **CH outages don't break the registry.** `repo.fetch_last_runs`
   catches exceptions and returns `{}`; `list()` still returns the
   in-memory catalog with `last_status='idle'`.

## How to test

```bash
poetry run pytest app/services/jobs/tests -m "not integration"
poetry run pytest app/api/tests/test_routes_jobs.py
curl http://localhost:8000/api/v1/jobs | jq
curl -X POST http://localhost:8000/api/v1/jobs/backfill_gap_sweeper/run | jq
```

## Module shape (`docs/standards/service_modules.md`)

```
jobs/
├── __init__.py    Re-exports the singleton + schemas
├── schemas.py     Pydantic DTOs — only file other services import
├── contract.py    Protocol — public interface
├── service.py     Implementation (NEVER imported across services)
├── repo.py        CH reads from ingestion_runs
└── README.md      This file
```

Service tests live under [`tests/`](tests/); HTTP adapter tests live under
[`../../api/tests/`](../../api/tests/).
