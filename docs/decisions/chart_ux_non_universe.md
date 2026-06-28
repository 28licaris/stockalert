# Decision — Chart UX for non-universe symbols

**Status:** Superseded by
[`standards/data/symbol_lifecycle.md`](../standards/data/symbol_lifecycle.md).
This file retains the pre-`stream_universe` decision context only.
**Owners:** operator + system design.
**Supersedes:** none.
**Affects:** frontend chart routes, `/api/silver/bars/*`, `/api/ohlcv/*`,
TA-5.6 (whole-market silver), TA-5.1.7 add-members flow.

---

## 1. Problem statement

After TA-5.1.7 (Path A), `silver.ohlcv_1m` and `ohlcv_1m` in ClickHouse
contain only the seed universe (~100 symbols). The frontend chart
route currently returns empty bars for any symbol outside the seed.
Users can technically type any ticker into the search box.

**What should the dashboard do when the user picks a symbol that
isn't in the universe?**

Two universes are at play:

| Universe | Source | Size | Lives in |
|---|---|---|---|
| **Seed** | `app/data/seed_universe.py` | ~100 | Always in silver + CH (post-TA-5.1.7) |
| **Active** (G1) | seed ∪ watchlist members | ~100-200 | Always in CH; silver coverage depends on `add_members` flow |
| **Bronze archive** | every ticker Polygon ever covered | ~10K | bronze.polygon_minute (5y) |
| **Polygon universe** | every listed US equity | ~10K | Polygon REST API on-demand |

The dashboard search box doesn't restrict to any of these — so a
user CAN type "ZYXI" or anything else.

## 2. The four options

### Option 1 — Strict block

When silver returns 0 rows, frontend shows a CTA:

> *"ZYXI isn't loaded. Add to watchlist to load 5 years of history?"*

**Pros:** simplest to build. No backend changes. No new code paths.
**Cons:** worst UX — user can't even peek at the chart before
committing. Watchlist gets polluted with "I just wanted to look once"
adds.

### Option 2 — Lazy materialize (silver build on demand)

When silver returns 0 rows, backend kicks off async silver build for
the requested symbol, returns 202 Accepted with a job ID. Frontend
polls until done (or uses SSE/WebSocket), then renders. Subsequent
clicks: instant from CH.

**Pros:** transparent UX after first load. Aligns with the
silver-canonical model — every viewed symbol ends up in silver.
**Cons:** first-load latency ~15-30 sec (silver slice build + CH load).
Needs:
- Job queue (or simple in-memory dict + Background Tasks)
- Status endpoint
- Frontend loading-state UI
- Idempotency (concurrent requests for same symbol coalesce)

### Option 3 — Direct-from-bronze fallback

When silver returns 0 rows, backend reads `bronze.polygon_minute`
directly, applies `silver.corp_actions` factors on the fly, returns
the adjusted bars **without persisting**.

**Pros:** instant UX. ~50 lines of code. No job infrastructure.
**Cons:** every chart request re-scans bronze (~3-6 sec). No caching.
Doesn't enable live alerts (still requires Schwab subscribe → watchlist
add). Adjustment math runs in the hot path of every request.

### Option 4 — Hybrid (3 then 2)

Best-of-both pattern from analytics systems:

1. **First render:** Option 3 — fall through to bronze + on-the-fly
   adjust. User sees chart in ~3-6 sec.
2. **In the background:** kick off Option 2 (silver materialize).
3. **Next time** the same symbol is requested (or even within the same
   session via a websocket push): served from CH, <500 ms.

**Pros:** instant UX, gracefully promotes to fast path. Operationally
self-healing (popular symbols accumulate in silver naturally).
**Cons:** most complex. Two code paths to maintain. Race conditions
(what if user requests the same symbol twice while background materialize
is in flight? Need debouncing + de-dup).

## 3. Context that changes the math — TA-5.6

TA-5.6 (whole-market silver) is planned. Once it lands:

- `silver.ohlcv_1m` contains ~10K symbols (every Polygon-covered ticker)
- ClickHouse mirrors silver (TA-5.7) — every Polygon ticker is in CH
- **The "non-universe" gap shrinks to ~5%**: brand-new IPOs added
  between full rebuilds, delisted tickers Polygon recently dropped,
  and a handful of edge cases.
- For those 5%, even strict Option 1 is reasonable — "this symbol
  isn't tracked in our archive, contact ops to add it" is a fine UX
  for genuinely-obscure requests.

## 4. The decision

**Phased rollout:**

### Phase 1 — NOW through TA-5.6 lands: **Option 1 (Strict)**
- Implement zero new backend code. Frontend shows "Not in universe —
  add to watchlist?" when `/api/silver/bars/{sym}` returns 0 bars.
- The add-to-watchlist path already exists (`add_members`). When the
  user clicks "add", the existing TA-5.3.3 flow triggers:
  `silver_to_ch_backfill` (will find 0 rows post-Path-A for non-seed),
  then `schwab_rest_tip_fill` (48 days), then Schwab streaming
  subscribe.
- **Limitation accepted:** added non-seed symbols get only 48 days of
  history until TA-5.6 lands or operator runs
  `silver --symbols <sym>` manually. Documented in the UI.

### Phase 2 — After TA-5.6: **Stay on Option 1 by default**
- 95%+ of search-box ticker entries will already be in silver+CH from
  the whole-market build, so the "non-universe" CTA fires rarely.
- For the remaining 5% (recent IPOs, delistings), Option 1 still works
  — the CTA becomes "this symbol isn't in our latest snapshot; add
  to watchlist (triggers a silver slice build)?".
- Re-evaluate based on real usage data: if the CTA fires > 5% of
  sessions, escalate to Option 4 (hybrid).

### Phase 3 — Only if Option 1 friction becomes a real complaint: **Option 4 (Hybrid)**
- Implement on-demand silver-slice build behind a `LAZY_MATERIALIZE_ENABLED`
  feature flag, default off.
- First-render falls through to direct-from-bronze (Option 3),
  background-materialize silver (Option 2), promote to CH on completion.
- Requires job queue infra (probably Background Tasks via FastAPI's
  `BackgroundTasks` + an in-memory de-dup dict; no Celery needed at
  this scale).

## 5. Implementation contract for Phase 1

### Backend — minimal change

`/api/silver/bars/{symbol}` (existing endpoint, no signature change):

- When the underlying `SilverOhlcvReader.get_bars()` returns 0 bars,
  the response payload includes a hint:

```json
{
  "symbol": "ZYXI",
  "count": 0,
  "bars": [],
  "snapshot_id": null,
  "hint": {
    "code": "not_in_universe",
    "message": "ZYXI is not in the active universe. Add to a watchlist to load 5 years of history.",
    "add_member_url": "/api/watchlists/{wl}/members"
  }
}
```

Implement by extending `SilverBarsResponse` (in
`app/services/readers/schemas.py`) with an optional `hint` field +
populate it in the reader when `count == 0`.

### Frontend — UI affordance

When `count == 0 && hint.code == "not_in_universe"`:
- Replace the chart canvas with an empty-state card
- Show `hint.message`
- Render a `[+ Add to Watchlist]` button → POSTs to the existing
  `add_members` endpoint
- After successful add, refetch `/api/silver/bars/{symbol}` and render

The 48-day-limit caveat (pre-TA-5.6) is communicated inline:

> *"You'll see the last 48 days immediately. Full 5-year history loads
> automatically when our next archive build runs (≤24h)."*

### Backstop for operators

If a hot symbol that's not in the universe is needed RIGHT NOW (e.g.
a news-driven spike on a non-seed name), v2 handles this automatically:
the first chart request through `/api/v1/bars` (or the bars gateway)
sees no ClickHouse coverage and fills the requested window on demand
from `equities.polygon_adjusted` via
`app/services/equities/lake_to_ch_fill.py`, then re-queries CH. To
pre-warm a symbol manually instead:

```bash
poetry run python scripts/rebuild_ch_from_lake.py --symbols ZYXI
```

The on-demand fill is bounded to the requested window (~seconds). The
dashboard returns real bars on the same request, and every subsequent
load is served hot from ClickHouse.

## 6. Open questions / what this defers

- **What does "search box" actually search?** Today's typeahead UX
  isn't specified. If we restrict typeahead to active-universe + CH-
  loaded symbols, the "non-universe" case becomes very rare even
  pre-TA-5.6. Worth a frontend-side audit when this lands.
- **Should `add_member` for non-seed pre-TA-5.6 automatically run
  the silver slice build?** Currently the TA-5.3.3 flow assumes
  silver has the data. Could extend `add_members` to:
  1. Detect "no silver rows for this symbol"
  2. Run a one-symbol silver build inline (~15-30 sec)
  3. Then proceed with the existing CH backfill + tip-fill
  This would close the 48-day-only-history gap immediately. **Not
  decided here** — separate design task for the add-members flow.
- **What's the failure mode if `silver --symbols ZYXI` is run but
  ZYXI has no bronze.polygon_minute coverage?** (e.g. brand-new IPO
  newer than our bronze archive.) The build returns empty silently
  today. The TA-5.3.3 add-members flow then falls through to
  `schwab_rest_tip_fill` for 48 days. Acceptable as-is.

## 7. Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-05-19 | Adopt Option 1 (Strict) for Phase 1; defer hybrid to post-TA-5.6 | Simplest, leverages existing add_members flow, the gap closes naturally once whole-market silver lands. Don't over-engineer for a 5% case. |

## 8. Reversal cost

If we decide Option 1 friction is too much:

- Implementing Option 4 (Hybrid) is **~2-3 dev days** (BackgroundTasks
  wiring + frontend loading state + de-dup logic + tests).
- No data migrations needed; the silver/CH tables are append-friendly.
- Existing Option 1 endpoint behavior stays as the fallback when
  on-demand build fails.

So the decision is cheaply reversible. Locking in Option 1 now does
NOT close the door on Option 4 later.
