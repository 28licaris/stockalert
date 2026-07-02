"""
Daily paper-trading pipeline — run after the close (the scheduled task entrypoint).

Steps, in order (each failure is reported; later steps still run so a stale
lake never silently stops the paper record from recomputing):

  1. Lake catch-up: equities Polygon flat-files → equities.polygon_raw
     (auto-catchup fills every missing weekday; idempotent).
  2. ohlcv_daily incremental refresh (lake → CH, split-aware).
  3. paper_run_all: recompute every library strategy's paper record + alerts.

Exit code = number of failed steps (0 = clean).

  poetry run python scripts/paper_daily_pipeline.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def _step_lake_catchup() -> None:
    from app.services.ingest.nightly_equities_polygon_refresh import (
        refresh_polygon_lake_yesterday,
    )
    out = asyncio.run(refresh_polygon_lake_yesterday())
    if out.get("skipped"):
        print(f"  lake catch-up skipped: {out.get('reason')}")
    else:
        print(f"  lake catch-up: {out.get('days_processed', 0)} day(s) processed "
              f"→ {out.get('dates', [])}")


def _step_refresh_daily() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "refresh_ohlcv_daily.py")],
        cwd=ROOT, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError(f"refresh_ohlcv_daily exited {r.returncode}")


def _step_paper_run_all() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "paper_run_all.py")],
        cwd=ROOT, timeout=3600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"paper_run_all exited {r.returncode}")


def main() -> int:
    steps = [
        ("lake catch-up (polygon flat-files)", _step_lake_catchup),
        ("ohlcv_daily incremental refresh", _step_refresh_daily),
        ("paper run (all library strategies)", _step_paper_run_all),
    ]
    failures: list[str] = []
    for name, fn in steps:
        print(f"\n=== {name} ===", flush=True)
        try:
            fn()
            print(f"  OK: {name}")
        except Exception as e:  # noqa: BLE001 — every step outcome is reported
            failures.append(name)
            print(f"  FAILED: {name}: {e}")
    print(f"\npipeline done: {len(steps) - len(failures)}/{len(steps)} steps ok"
          + (f" — FAILED: {', '.join(failures)}" if failures else ""))
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
