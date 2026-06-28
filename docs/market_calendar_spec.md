# Spec — Market Calendar (sessions + events)

Status: **PHASE 1 IMPLEMENTED** (2026-06-27). Core session engine
(`app/services/market_calendar.py`, exchange_calendars), calendar API
(`/api/v1/calendar`), and frontend month view (`routes/calendar.tsx`) shipped.
Equities gap detection wired to the calendar; **futures kept the Sun–Fri
heuristic** (CMES labels sessions Mon–Fri but futures data is keyed by ET
date incl. Sunday — see §4 note). Events layer (§11) = Phase 2.
Author: ops/data
Date: 2026-06-26 (expanded 2026-06-27 with the product-facing calendar:
API + frontend + events layer)

> **Scope note:** §1–8 are the *core session engine* (the gap-filler's
> "is the market open?" need). §9–12 add the product calendar: a query API,
> a frontend month view, and an extensible **events** layer (FOMC, CPI,
> earnings) for the future. One core module backs both.

## 1. Problem

Gap detection and freshness reasoning are **holiday-blind**. The system
treats every Mon–Fri as a trading day and relies on "the provider is the
source of truth": it attempts every weekday and skips whatever comes back
empty (`404`/no rows) as `"weekend / holiday / out-of-range"`.

This is robust but has three concrete costs, all observed on **2026-06-19
(Juneteenth)**:

1. **Wasted work.** A holiday burns a full universe of provider calls
   (217 Schwab pricehistory requests) before the "no rows" skip.
2. **Holiday ≡ gap.** `missing_weekdays()`
   ([app/services/equities/gaps.py](app/services/equities/gaps.py)) counts
   every weekday as expected, so a holiday is indistinguishable from a real
   missing-data gap. Freshness/monitoring can never assert "we hold every
   session" — a permanent holiday hole looks like a defect forever.
3. **No asset-class distinction.** Equities were closed on Juneteenth but
   CME futures traded. Nothing in the code knows this, so the futures gap
   detector and the equities gap detector can't diverge correctly.

The existing code deliberately avoided a calendar — the comment in
[app/providers/polygon_flatfiles.py:546](app/providers/polygon_flatfiles.py)
says it skips holidays implicitly "so we don't ship a fragile holiday
calendar." That instinct (don't hand-maintain a drifting table) is correct
and this spec honors it.

## 2. Goal / requirements

- A single authoritative answer to: *"Is `<date>` a trading session for
  `<asset class>`, and if so, is it a half-day / early close?"*
- Cover **equities (XNYS / NYSE+Nasdaq)** and **CME futures (CMES /
  Globex)** including the equities-closed-but-futures-open case.
- Handle **half-days / early closes** (e.g. day after Thanksgiving 13:00
  ET, Christmas Eve) — needed so freshness doesn't flag a short session as
  "missing the afternoon".
- All session math in **ET**, consistent with the existing ET-vs-UTC
  trading-day handling.
- **No annual manual upkeep.** Calendar correctness must not depend on a
  human editing a table each year.

Non-goals: a full execution-grade trading-hours engine; intraday
microstructure; non-US venues.

## 3. Approach — maintained library, not a hand-kept table

Add **`exchange_calendars`** (alt: `pandas_market_calendars`). It ships and
maintains:
- `XNYS` — NYSE/Nasdaq sessions, holidays, half-days, early-close times.
- `CMES` — CME Globex sessions (Sun–Fri) + CME holiday rules.

Library = source of truth. Optionally cache into a small CH table for
SQL-side coverage joins (§5), but the table is a derived cache, never the
authority.

### Why library over DB table
A DB `market_holidays` table is exactly the "fragile calendar" the codebase
warned against: it drifts, needs yearly edits, and silently rots. A pinned,
tested library updates with a dependency bump and is unit-testable against
known holidays.

## 4. Design

New module `app/services/market_calendar.py` (pure, no I/O beyond the
library), exposing:

```python
def is_equities_session(d: date) -> bool
def is_futures_session(d: date) -> bool
def equities_sessions(start: date, end: date) -> list[date]
def futures_sessions(start: date, end: date) -> list[date]
def equities_early_close(d: date) -> time | None   # None = full day
def futures_early_close(d: date) -> time | None
```

Calendars are constructed once (module-level, lazy) and cached — building an
`exchange_calendars` instance is ~100ms, so we do it once per process.

### Integration points (the only behavioral changes)
1. `app/services/equities/gaps.py::missing_weekdays` → filter the candidate
   window to `equities_sessions(...)` instead of `weekday() < 5`. Keeps the
   bounded cold-start fallback.
2. `app/services/futures/gaps.py::missing_futures_sessions` → use
   `futures_sessions(...)` (replaces the current Sun–Fri heuristic + adds
   CME holiday awareness).
3. Freshness checks (read-layer / status) → assert coverage against the
   calendar so "complete through `<last session>`" becomes a real claim,
   and holidays are reported as `expected-closed`, not `missing`.

### Dependency
Add `exchange_calendars` to `pyproject.toml`, pinned. One transitive concern
to verify in the spike: it pulls `pandas`/`numpy` (already present).

## 5. Optional — CH `market_sessions` cache (decide during review)

For SQL-side coverage queries (e.g. "which sessions are we missing in
`ohlcv_1m`?"), a tiny table helps:

```
market_sessions(exchange LowCardinality(String), session_date Date,
                is_open UInt8, early_close_et Nullable(String))
```

Populated by a startup/nightly job from the library (library = truth, table
= cache, rebuilt idempotently). **Recommend deferring** to a phase 2 unless
we need calendar-aware SQL now.

## 6. Edge cases / test matrix

Unit tests against known dates (no network):
- Juneteenth 2026-06-19 — equities **closed**, futures **open**.
- Good Friday — equities closed; CME partial (verify library's answer).
- Thanksgiving — both closed; **day-after** equities early close 13:00 ET.
- July 4 (+ observed-on-weekday shifts), Christmas (24th early close).
- Normal weekday — both open. Weekend — both closed (futures Sun evening
  session boundary handled by the library's session dates).

## 7. Rollout

- Land module + tests (no behavior change) → flip `gaps.py` over → verify a
  nightly run skips the next holiday with **zero** wasted provider calls and
  logs `expected-closed`.
- Low risk: gap detection only ever *narrows* the candidate set (drops
  holidays); the bounded cold-start fallback is unchanged.

## 8. Open questions for sign-off

1. `exchange_calendars` vs `pandas_market_calendars` — preference?
2. Build the CH `market_sessions` cache now (§5) or defer to phase 2?
3. Put the new module under `app/services/` directly, or a small
   `app/services/calendar/` package per the service-module template?

---

## 9. Product calendar — API (new)

A read API over the core engine so the frontend (and agents) can render the
calendar. Sessions are computed on-demand from the library per request (fast
for a month range — no storage needed).

```
GET /api/v1/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD&asset_class=equities|futures
→ { "asset_class": "equities",
    "days": [
      { "date": "2026-06-18", "status": "open",        "early_close_et": null,    "events": [] },
      { "date": "2026-06-19", "status": "closed",       "reason": "Juneteenth",    "events": [] },
      { "date": "2026-11-28", "status": "early_close",  "early_close_et": "13:00", "events": [] },
      ...
    ] }
```

- `status` ∈ `open | closed | early_close`. `reason` is the holiday name when
  closed (from the library), null otherwise.
- `events` is always present (empty until §11 lands) so the frontend renders
  event markers without an API change later.
- New `app/api/routes_calendar.py`, registered in `main_api` like the other
  `routes_*`. Pydantic response shape is the single contract for HTTP + MCP.
- Cheap + cacheable (sessions for a year are deterministic) — add an
  in-process/HTTP cache keyed on (asset_class, year) if needed.

## 10. Frontend — calendar view (new)

A new **Calendar** route in the cockpit (`frontend/src/routes/calendar.tsx`,
added to `router.tsx` + `Sidebar`):
- **Month grid**, equities/futures toggle (reuses the repo's tab pattern).
- Each day cell shows session status: open / closed (greyed + holiday name) /
  early-close (badge with the close time). Today highlighted.
- Event markers (§11) render as dots/chips in the cell; empty for now.
- Data via the §9 endpoint through the existing `openapi-fetch` client +
  React Query (regen types with `npm run codegen`).

## 11. Events layer (FUTURE — design now, build later)

Extensible so "important events" (FOMC, CPI/NFP, OPEX, earnings) attach to
calendar days without reworking the API/frontend.

- **Shape:** `event(id, date[, time_et], asset_scope, type, title,
  description, importance, source, url)`. `type` ∈ FOMC | econ_release |
  earnings | opex | custom; `importance` ∈ low|medium|high.
- **Store (decide when built):** low-volume curated reference data. Options:
  PostgreSQL (identity DB — relational, admin-editable), a small CH table, or
  a seed JSON checked into the repo + optional ingestion. Leaning PostgreSQL
  (it's relational/curated, not market-tick data).
- **Population:** seed manually first; later ingest FOMC dates (Fed publishes
  the schedule), econ releases (BLS/Census calendars), earnings (provider).
- The §9 API merges events into each day's `events[]`; §10 renders them.
  **No API/UI rework needed when events arrive** — only the store + a join.

## 12. Phasing

- **Phase 1 (this work):** core session engine (§1–8) + calendar API (§9) +
  frontend month view (§10). `events[]` ships empty. Optionally also wire the
  gap-detection integration (§4 — same module, real win: no wasted holiday
  pulls).
- **Phase 2 (future):** events store + seed (FOMC first) + ingestion; the API
  and frontend already carry events, so this is additive.

## 12a. Phase 2 — FREE events (approved 2026-06-27)

Constraint: **free sources only, production-robust.** Robustness rule: runtime
reads ONLY *computed* values, *data we already own*, *committed seed files*, or
*entitled APIs* — **never live HTML scraping**. Any scrape is a reviewed
dev-time tool that emits a committed seed.

**v1 sources (all free):**
1. **Computed** — OPEX / quad-witching / quarter-end (pure function off the
   session calendar; can't break, no dependency).
2. **Owned** — dividend + split ex-dates from `market_corp_actions` /
   `market_splits` (Polygon REST, already entitled + ingested).
3. **Seeded** — FOMC + key macro (CPI, NFP, GDP, PCE) from a committed seed
   file (`data/market_events_seed.json`), refreshed by a reviewed dev script +
   the Fed/BLS published schedules.

**Deferred:** earnings — no free + robust source (Schwab provider exposes no
earnings; Polygon earnings = paid Benzinga). The model below already supports
`event_type='earnings'` so it slots in later.

**Store:** ClickHouse `market_events` — ReplacingMergeTree(version),
`PARTITION BY toYYYYMM(event_date)`,
`ORDER BY (event_date, symbol, event_type, external_id)`. Columns:
event_date, event_time_et (nullable), symbol (''=macro), event_type, title,
importance, source, external_id, payload(JSON), version. Idempotent on the
ORDER BY key + version. Fast for the calendar AND the symbol page.

**Populate:** `app/services/market_events.py` — computed generator + seed
loader + corp-actions→events sync; idempotent. Scheduled like the nightly jobs
(computed/seed cheap; corp-actions reads the small splits table + universe-
scoped dividends so the grid isn't flooded with whole-market dividend rows).

**Surface:** the calendar API joins events into each day's `events[]` (already
in the contract); the frontend already renders markers.

**Phasing within Phase 2:** (2a) table + computed + macro seed + API/UI join;
(2b) corp-actions ex-date sync; (2c) earnings when a free/funded source exists.

## 13. Open questions (product)

4. Phase 1 scope: calendar API + frontend **only**, or **also** wire the
   gap-detection integration now (same core module)?
5. Frontend home: a dedicated **Calendar** nav page (recommended), or fold it
   into the existing Status page?
6. Events store (when Phase 2 lands): PostgreSQL vs CH vs seed-JSON — defer,
   or decide now?
