"""ClickHouse read of `ingestion_runs` for the job registry.

Returns the latest successful + latest-overall run timestamps per
job_name. Defensive: any CH outage returns empty results so the
registry can still list jobs (with last_success=None) on cold start
or during a database hiccup.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _ts(value) -> Optional[str]:
    """ClickHouse returns naive datetimes; stamp with `Z` so the JS
    side parses them as UTC."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        if getattr(value, "tzinfo", None) is None:
            return value.isoformat() + "Z"
        return value.isoformat()
    return str(value)


def fetch_last_runs() -> dict[str, dict]:
    """Return `{job_name: {last_success, last_run_at, last_status, last_error}}`.

    Reads `ingestion_runs` once, aggregating per job. Cheap — the table
    is small in practice (a few rows per job per day). Bounded with
    LIMIT 5000 to keep the query trivial even if it ever grows.
    """
    try:
        from app.db.client import get_client

        client = get_client()
    except Exception as exc:  # noqa: BLE001 — CH unavailable
        logger.warning("job_registry: CH client init failed: %s", exc)
        return {}

    try:
        rows = client.query(
            """
            SELECT
                job_name,
                argMax(finished_at, finished_at)                      AS last_run_at,
                argMax(status,      finished_at)                      AS last_status,
                argMax(per_provider_errors_json, finished_at)         AS last_error,
                maxIf(finished_at, status = 'ok')                     AS last_success
            FROM ingestion_runs
            GROUP BY job_name
            """,
        ).result_rows
    except Exception as exc:  # noqa: BLE001 — query failure
        logger.warning("job_registry: ingestion_runs read failed: %s", exc)
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        job_name, last_run_at, last_status, last_error, last_success = r
        # last_success comes back as 1970-01-01 when no ok run yet
        # (maxIf returns the default datetime for an empty set).
        success_str = _ts(last_success) if last_success and last_success.year > 1971 else None
        out[job_name] = {
            "last_run_at": _ts(last_run_at),
            "last_status": last_status or "unknown",
            "last_error": (last_error or "").strip()[:500] or None,
            "last_success": success_str,
        }
    return out
