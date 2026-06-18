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

## Storage convention — store UTC, convert at query time

All OHLCV timestamps are stored as **UTC instants** in both ClickHouse
and the S3 lake. Display/query code converts UTC → ET as needed. Never
store wall-clock ET.

### Daily bars are resampled from 1m, bucketed on the ET trading *date*

There is no daily table. A 1-day candle is resampled on read from
`ohlcv_1m` (`queries.list_bars_resampled`), and the daily bucket uses
`toStartOfDay(ts,'America/New_York')` — **not**
`toStartOfInterval(ts, INTERVAL 1 DAY)`, which floors to UTC midnight and
would split the ET session (a 19:45-ET after-hours bar lands on the next
UTC day). The result anchors at midnight ET (`04:00Z` EDT / `05:00Z` EST).

## What broke once

A gap-detector shipped with this bug. Would have skipped an entire
trading day on the next nightly run. Caught only during smoke-test.

The daily-bar duplicate bug (2026-06): a separate `ohlcv_daily` table was
fed by two providers that stamped the same session an hour apart (Polygon
at midnight ET, Schwab at 01:00 ET); a dedup keyed on exact `timestamp`
let both survive → doubled candles on every 1d chart. Root-caused and
fixed by retiring the daily/5m cache tables entirely — all timeframes now
resample from `ohlcv_1m`, the single source of truth.
