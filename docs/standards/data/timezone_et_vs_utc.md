# Timezone — ET, not UTC

Trading-day math anchors on `America/New_York`, not UTC.

## Why

US equities day spans ET 04:00–20:00. After-hours bars cross midnight
UTC.

A 19:45 ET bar on May 14 has UTC timestamp 2026-05-15 23:45. `.date()`
on UTC returns May 15 — wrong. Belongs to May 14 trading day.

## Convert

```python
from zoneinfo import ZoneInfo
trading_day = ts_utc.astimezone(ZoneInfo("America/New_York")).date()
```

## Use existing helpers — don't roll your own

- `app.services.bronze.gaps.yesterday_et()` — "yesterday"
- `latest_bronze_date(table)` — "most recent trading day with data"

`tests/test_bronze_gaps.py` covers weekend-spanning + boundary cases.
Add cases if you extend trading-day math.

## What broke once

A gap-detector shipped with this bug. Would have skipped an entire
trading day on the next nightly run. Caught only during smoke-test.
