# health/

Data **freshness** probes — "is data still arriving?"

## What this owns

Deliberately separate from `/health/services`, which answers a different
question: "can I connect?". Both are needed because **connectivity was green
through every real incident**:

- the Schwab streamer thread died 2026-07-20 while `/stream/status` still
  reported `started=true, provider_ready=true, streaming_count=240` — and
  ClickHouse took zero new bars for 15 days;
- the ThetaData options bronze sat 34 days stale after the terminal stopped,
  and nothing surfaced it;
- 552 instrument names were cached blank, so pages showed bare tickers.

This module is that missing signal. It does **not** own connectivity checks,
job scheduling, or alert delivery.

## The load-bearing rule — the expected-fresh window

A 1-minute bar feed 14 hours stale at 03:00 is **IDLE**, not broken; the same
reading at 11:00 ET is an **ERROR**. A panel that cries wolf overnight gets
ignored, and an ignored panel is worse than no panel.

Each source declares the window in which it is *expected* to be fresh, and
staleness is only escalated inside that window.

| Threshold | Meaning |
|---|---|
| `WARN_CADENCES = 2.0` | stale by 2× the source's expected cadence |
| `ERROR_CADENCES = 10.0` | stale by 10× — treat as broken |

## Layout

| File | Owns |
|---|---|
| [freshness.py](freshness.py) | `FreshnessSource`, `FreshnessRow`, `ExpectedWindow`, the `SOURCES` registry, `classify()`, `is_expected_fresh()` and the per-source `_probe_*` functions |

## Public contract

```python
from app.services.health.freshness import SOURCES, FreshnessRow, classify
```

- **`SOURCES`** — the registry of probed sources (live bars, daily bars,
  options GEX, equities lake, ThetaData bronze, paper runs, instrument names).
  Adding a source means adding one `FreshnessSource` entry with its probe,
  cadence and expected window — nothing else changes.
- **`classify(...) -> FreshnessRow`** — turns a last-seen timestamp into an
  `ok` / `idle` / `warn` / `error` state plus a human "N ago" string.
- **`is_expected_fresh(window)`** — whether a window is currently open.

Consumed by `app/api/routes_health.py` (the health page). Trading-day logic is
ET-based per
[`timezone_et_vs_utc.md`](../../../docs/standards/data/timezone_et_vs_utc.md).

## Testing

```bash
poetry run pytest app/services/health/tests -q
```

Classification and window logic are pure functions tested with injected
`now=` — no clock mocking and no ClickHouse. The `_probe_*` functions hit
ClickHouse and Iceberg, so tests that exercise them carry the `integration`
marker.
