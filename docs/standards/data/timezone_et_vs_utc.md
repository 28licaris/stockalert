# Timezone — ET, not UTC, for Trading-Day Math

When computing "latest trading day in bronze", "yesterday", "missing
days", or anything that buckets bars by trading-day, **use Eastern
Time, not UTC**.

## Why

The US equities trading day spans ET 04:00 (pre-market) to ET 20:00
(after-hours). After-hours bars have UTC timestamps on the **next
calendar date**.

**Example:** a 19:45 ET bar on May 14 has UTC timestamp 2026-05-15
23:45. `.date()` on that UTC timestamp returns May 15 — wrong. The bar
belongs to the May 14 trading day.

Convert via:

```python
from zoneinfo import ZoneInfo
trading_day = ts_utc.astimezone(ZoneInfo("America/New_York")).date()
```

## What broke

A gap-detector was shipped with this exact bug. It would have caused
the next nightly run to skip an entire trading day. Caught during
smoke-test only because the catchup was run twice and the counter
advanced wrong.

This is the kind of silent-failure that
[`../coding.md`](../coding.md) rule 5 (verify cross-side) is designed
to catch — but rule 5 only helps if the dates being compared are
correct in the first place.

## How to apply

In this repo, use:

- `app.services.bronze.gaps.yesterday_et()` for "yesterday"
- `latest_bronze_date(table)` for "most recent trading day with data"

**Don't roll your own.** If you write new trading-day math anywhere
else, anchor on `America/New_York`.

## Regression detection

`tests/test_bronze_gaps.py` includes weekend-spanning and boundary
cases. Add to them if you extend the trading-day math.

## Related

- [`../coding.md`](../coding.md) rule 2 — log every outcome (the gap
  detector silently skipped a day).
- [`../testing.md`](../testing.md) — boundary cases belong in tests.
