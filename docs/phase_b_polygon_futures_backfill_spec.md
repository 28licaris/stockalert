# Phase B — Polygon Futures Historical Backfill

**Status:** Spec (pending approval)
**Scope:** New script + two new service modules + optional nightly wire-in

---

## Problem

Schwab's 1m REST API returns at most ~48 calendar days of history.
CH currently holds 43 days of `/ES 1m` bars. This makes Elliott Wave
analysis on intraday futures timeframes (1h, 4h) effectively blind to
older structure.

Polygon has per-contract 1m history going back to each contract's first
trade date (ESZ5 trades from ~Oct 2024; older contracts go back years).
By stitching quarterly contracts into a continuous `/ES` series we can
populate years of 1m futures history.

---

## Data Model

### Polygon contract naming

```
Root  Suffix  Contract
ES    Z5      ESZ5  (Dec 2025)
ES    H6      ESH6  (Mar 2026)
ES    M6      ESM6  (Jun 2026)
ES    U6      ESU6  (Sep 2026)
```

Month codes: H=Mar, M=Jun, U=Sep, Z=Dec (quarterly)

### Front-month assignment

A contract is "front month" from its listing date until ~4 trading days
before its expiry (roll date). Expiry = 3rd Friday of the expiry month
at 09:30 ET. Roll = expiry − 4 trading days.

Example for ESZ5 (Dec 2025, expires 2025-12-19):
- Roll date: ~2025-12-15 (4 trading days before)
- ESZ5 is front month from its list date (~Sep 2025) through 2025-12-14
- ESH6 becomes front month on 2025-12-15

### Polygon API call

```python
from massive.polygon.client import PolygonClient

client = PolygonClient(api_key=settings.polygon_api_key)
for agg in client.list_futures_aggregates(
    ticker="ESZ5",
    resolution="1min",         # NOT "minute" — Polygon rejects that
    window_start_gte="2025-09-01T00:00:00Z",
    window_start_lte="2025-12-15T00:00:00Z",
    order="asc",
    limit=50000,
):
    # agg.window_start is nanoseconds since epoch
    ts = datetime.fromtimestamp(agg.window_start / 1e9, tz=timezone.utc)
    ...
```

Rate limits: 5 req/min (free), 100 req/min (Starter+). Use `time.sleep`
between pages or use the SDK's built-in iterator (it handles pagination).

---

## Files Created

### `app/services/futures/contract_chain.py` (new)

Pure functions; no I/O.

```python
"""Front-month contract chain logic for US CME futures."""
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Iterator

@dataclass
class ContractWindow:
    ticker: str        # e.g. "ESZ5"
    root: str          # e.g. "ES"
    front_start: date  # inclusive
    front_end: date    # inclusive (last front-month day)
    expiry: date       # actual CME expiry date

MONTH_CODES = ["H", "M", "U", "Z"]          # Mar, Jun, Sep, Dec
MONTHS_FOR_CODE = {"H": 3, "M": 6, "U": 9, "Z": 12}
ROLL_DAYS_BEFORE_EXPIRY = 4                  # business days

def third_friday(year: int, month: int) -> date: ...
def expiry_for(root: str, month_code: str, year2: int) -> date: ...
def contract_chain(root: str, start_year: int, end_year: int) -> list[ContractWindow]: ...
```

### `app/services/futures/polygon_sink.py` (new)

Thin Iceberg appender — same structure as `schwab_sink.py`.

```python
"""Write Polygon futures 1m bars to the lake.

Target table: futures.polygon_futures
Schema: same as futures.schwab_futures (symbol, timestamp, open, high,
        low, close, volume, source='polygon-futures')
"""
def write_bars(bars: list[dict]) -> int:
    """Append bars to futures.polygon_futures. Returns rows written."""
```

### `scripts/polygon_futures_backfill.py` (new)

CLI entrypoint. Runnable standalone or via CodeBuild.

```
Usage:
  poetry run python scripts/polygon_futures_backfill.py \
    --root ES \
    --start-year 2022 \
    --end-year 2026 \
    [--dry-run]
```

Flow:
1. `contract_chain("ES", start_year, end_year)` → list of `ContractWindow`
2. For each window:
   a. Call `list_futures_aggregates(ticker, resolution="1min", ...)`
   b. Filter to `window.front_start ≤ bar_date ≤ window.front_end`
   c. Map bar to `{"symbol": "/ES", "timestamp": ts, "open": ..., ...,"source": "polygon-futures"}`
   d. Batch-write to `futures.polygon_futures` via `polygon_sink.write_bars()`
3. Log progress per contract; exit non-zero on any hard failure
4. Pre-flight: check `POLYGON_API_KEY` and lake bucket are set

Rate limiting: sleep 12s between pages when on free tier (5 req/min cap).
Add `--rate-limit N` flag to override.

---

## Lake Table

### `futures.polygon_futures`

New Iceberg table. Schema mirrors `futures.schwab_futures`:

| Column | Type | Notes |
|--------|------|-------|
| symbol | string | `/ES`, `/NQ`, etc. (continuous root) |
| timestamp | timestamptz | UTC, 1m bar open |
| open | double | |
| high | double | |
| low | double | |
| close | double | |
| volume | long | |
| source | string | `"polygon-futures"` |

Partition: `month(timestamp)` + `bucket(8, symbol)`

### Glue DB registration

Same as `futures.schwab_futures` — register in `app/services/futures/tables.py`.

---

## Bars Gateway Integration

After the backfill runs, `bars_gateway.py` needs to read from the new
table when CH doesn't cover the window:

```python
def _lake_fill_fn(symbol: str):
    if is_futures_symbol(symbol):
        from app.services.futures.lake_to_ch_fill import fill_ch_from_futures_lake_sync
        return fill_ch_from_futures_lake_sync  # already reads ALL lake tables unioned
```

The existing `fill_ch_from_futures_lake_sync` reads `futures.schwab_futures`.
It needs to be extended to union `futures.polygon_futures` so both
sources feed CH. Alternatively: a single `futures.polygon_futures` can
hold BOTH Schwab and Polygon bars once Phase B runs (backfill replaces
Schwab rows for the same window with higher-quality Polygon data).

**Recommendation**: keep tables separate; union at read time in the fill
function. This preserves source attribution and avoids overwrite complexity.

---

## Nightly Wire-In (optional — Phase B+)

After manual backfill validates data quality, add a nightly Polygon
futures refresh that pulls the previous day's bars for all active
contracts and appends to `futures.polygon_futures`.

Gate behind `POLYGON_FUTURES_NIGHTLY_ENABLED` (default off). Wire into
`nightly_futures_refresh.py` alongside the existing Schwab path.

---

## Roots to Backfill (Priority Order)

| Root | CH Symbol | Priority |
|------|-----------|----------|
| ES   | /ES  | P0 — most actively traded, critical for EWT |
| NQ   | /NQ  | P1 — tech index |
| GC   | /GC  | P2 — gold |
| CL   | /CL  | P2 — crude |
| RTY  | /RTY | P3 — small cap |

Start with ES. Once the pipeline validates, the same script handles
others by changing `--root`.

---

## Test Plan

1. Dry-run `scripts/polygon_futures_backfill.py --root ES --start-year 2025 --dry-run`
   — prints bar counts per contract window; no writes.
2. Run for 2025 only; verify `futures.polygon_futures` has rows in Athena.
3. Run `fill_ch_from_futures_lake_sync("/ES", ...)` extended to read the new table;
   verify CH contains bars from Polygon.
4. Load `/ES 1h` chart in cockpit — bars should extend back ~1 year.
5. Unit tests: `contract_chain.py` logic (third_friday, front-month windows).
