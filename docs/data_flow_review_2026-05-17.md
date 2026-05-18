# Data Flow Review — 2026-05-17

Operator described the production data flow they want. This doc
maps each piece to current code, flags gaps, and proposes the plan.

---

## The intent (operator's words, structured)

```
                       ┌──────────────────────────────────┐
                       │ NIGHTLY                          │
                       ├──────────────────────────────────┤
                       │ Polygon → bronze (WHOLE MARKET)  │
                       │ Schwab  → bronze (universe ~100) │
                       └────────────┬─────────────────────┘
                                    │
                                    ▼
                       ┌──────────────────────────────────┐
                       │ Silver build (when bronze grows) │
                       │ merge polygon ≻ schwab           │
                       └────────────┬─────────────────────┘
                                    │
                                    ▼
                       ┌──────────────────────────────────┐
                       │ ClickHouse                       │
                       │ - seeded/hot-loaded from silver  │
                       │ - Schwab stream → CH (forever)   │
                       └──────────────────────────────────┘

EDGE CASES
──────────

1. Stream a new symbol NOT in universe:
   → Add to universe (so it gets nightly bronze updates going forward)
   → Backfill bronze + silver from any available source
   → Load history from silver to CH

2. Silver must sync periodically when bronze gets updated.

3. Adding a new streaming symbol:
   → Load history from silver → CH.
   → Handle gap robustly for seamless front-end UX.
```

---

## Current state vs intent (per piece)

| # | Intent | Current state | Gap |
|---|---|---|---|
| 1 | Polygon nightly → bronze, **whole market** | Polygon nightly runs against `POLYGON_NIGHTLY_SYMBOLS` (default `"seed"` = ~100). Whole-market was the **one-shot** historical bulk pull. Today's nightly is seed-only. | **GAP** — flip nightly to whole market (or expanded universe). |
| 2 | Schwab nightly → bronze, **universe seed (100)** | ✅ Built. `nightly_schwab_refresh` pulls yesterday's 1-min for SCHWAB_NIGHTLY_SYMBOLS (default seed). | None for seed; needs expansion if universe grows. |
| 3 | Silver merge polygon > schwab | ✅ For corp-actions (TA-5.0 done). 🔲 For OHLCV (TA-5.1.2 normalization landed today; merge + build orchestrator next). | **GAP** — silver.ohlcv_1m doesn't exist yet. |
| 4 | Silver syncs periodically | 🔲 No silver_ohlcv_build cron yet. silver_corp_actions_build is operator-triggered. | **GAP** — wire silver builds into nightly schedule. |
| 5 | CH seeded/hot-loaded from silver | 🔲 Not built. Legacy path ② still pulls Schwab REST → CH directly on `add_members`. | **GAP** — TA-5.3 (silver_to_ch_backfill). |
| 6 | Schwab stream → CH (only live source) | ✅ Built (path ①). Live-stream rows tagged `schwab-stream`. | None. |
| 7 | Live writer: stream → bronze every 5 min | ✅ TA-5.7 done. | None. |
| 8 | Edge: new streamed symbol → add to universe + backfill S3 | 🔲 `add_members` adds to watchlist but NOT to `SEED_SYMBOLS` (which is a static Python tuple). New streamed symbols do NOT get nightly Polygon/Schwab backfills. | **GAP** — dynamic universe. |
| 9 | Edge: add streamed symbol → history from silver to CH | 🔲 Not built. | **GAP** — TA-5.3 + on-add wiring. |
| 10 | Edge: gap between silver watermark + live → seamless UX | 🔲 Tip-fill designed, not built. Cockpit "warming up" UX designed, not built. | **GAP** — TA-5.3 tip-fill + cockpit (FE-2). |

---

## The architectural insight you've put your finger on

The **"seed universe"** today is a `tuple[str, ...]` hard-coded in
`app/data/seed_universe.py`. It's literally immutable Python at
process start. The nightly Polygon + Schwab jobs read from it.

The **"watchlist"** is dynamic (CH-backed, mutable, multi-watchlist).
`add_members` adds to a watchlist; the Schwab stream subscribes.

**These two concepts have drifted.** Streaming a symbol via watchlist
doesn't add it to the seed universe. So:

- Stream NVDA today → CH gets live ticks (via path ①) — fine for the
  chart's live tab.
- But NVDA doesn't get the nightly Polygon flat-file ingest unless
  it's in `SEED_SYMBOLS` (the static tuple).
- And nightly_schwab_refresh runs on seed-only too.
- So bronze has NVDA only through the one-shot historical pull —
  not nightly going forward.
- Silver build (when it lands) sees yesterday's bronze, which is
  stale for non-seed symbols.

Your intent fixes this: **the universe should be dynamic**, growing
as new symbols are streamed. There are two clean ways:

### Option A: "Universe = whatever's actively watchlisted"

Make nightly jobs query the live watchlist tables, not the static
`SEED_SYMBOLS` tuple:

```python
def get_active_universe() -> list[str]:
    """Union of SEED_SYMBOLS (the curated floor) + every symbol
    currently in any active watchlist."""
    static_seed = set(SEED_SYMBOLS)
    dynamic = set(watchlist_repo.list_all_active_symbols())
    return sorted(static_seed | dynamic)
```

Then:
- Polygon nightly: pulls flat-files for `get_active_universe()`
  (free; flat-files contain everything anyway, importing more is
  a metadata-only cost).
- Schwab nightly REST: pulls historical for `get_active_universe()`.
- Silver build: operates on whatever's in bronze.
- Stream-add for a new symbol → next night, the symbol is in the
  universe and gets backfilled.

**Pros:** simple, no new tables. **Cons:** the next nightly is up
to 24h away; chart needs Schwab REST tip-fill for the first day.

### Option B: "Whole market on Polygon side; universe on Schwab side"

You said "polygon is the whole market". Make this literal:
- Polygon nightly: `--symbols all` (ingests everything Polygon's
  flat-files cover — every US stock that traded yesterday). Storage
  cost is trivial; ~80 MB/day Parquet.
- Schwab nightly REST: `get_active_universe()` (dynamic).

**Pros:** every symbol gets Polygon nightly without operator action.
Adding a new symbol to the watchlist immediately benefits from
the previous night's whole-market Polygon pull. **Cons:** Polygon
costs (storage + ingest time) scale with ~10K-symbol market vs
~100-symbol universe — but at our scale this is ~5 min/night and
~$0.50/month in S3.

**My recommendation: Option B.** Matches "polygon is the whole
market" literally + auto-handles new symbols + cheap.

---

## The full plan to close every gap

Ordered by dependency:

### Phase G1 — Dynamic universe & nightly scope (1 day)

| Item | Effort |
|---|---|
| `get_active_universe()` helper in `app/data/seed_universe.py`: union of static seed + live watchlists | 1 hr |
| Flip `POLYGON_NIGHTLY_SYMBOLS=all` default (whole-market Polygon nightly) | 30 min |
| Flip `SCHWAB_NIGHTLY_SYMBOLS=universe` (dynamic — reads `get_active_universe()`) | 1 hr |
| Tests: helper round-trip, nightly job picks up newly-added watchlist symbols | 1 hr |
| Doc update: `streaming_universe_model.md` to reflect dynamic universe | 30 min |

Outcome: any symbol added to any watchlist gets nightly bronze
backfill from both providers automatically (within 24h).

### Phase G2 — Complete TA-5.1 (silver OHLCV build) (~3 days)

In progress today. Remaining sub-phases:

| Item | Effort |
|---|---|
| TA-5.1.3: provider precedence merge + bar_quality computation | 2 hr |
| TA-5.1.4: orchestrator (`silver_ohlcv_build.build_slice(symbol, day)`) + watermark + tests | 4 hr |
| TA-5.1.5: SilverOhlcvReader + HTTP + MCP | 1 hr |
| TA-5.1.6: operator CLI for initial backfill + nightly schedule | 1 hr |
| TA-5.1.7: live verification + initial overnight backfill | 30 min + overnight |

Outcome: `silver.ohlcv_1m` exists, gets refreshed nightly from
bronze, available to all consumers.

### Phase G3 — Wire silver build into nightly schedule (0.5 day)

| Item | Effort |
|---|---|
| Add `silver_build` to FastAPI lifespan (background task; runs at 02:00 ET after both nightly bronze ingests) | 1 hr |
| `ingestion_runs` audit row per silver_build cycle | 30 min |
| `silver_freshness` bronze-audit check (extension of TA-5.7 freshness check) | 1 hr |
| Tests + lifespan-shutdown handling | 1 hr |

Outcome: silver auto-syncs every night after bronze updates.

### Phase G4 — TA-5.3 silver→CH backfill + tip-fill (1 day)

| Item | Effort |
|---|---|
| `SilverToChBackfill` core class (read silver → arrow → CH bulk insert) | 2 hr |
| `schwab_tip_backfill` helper (fills silver-watermark → live-first-bar gap; ≤48h Schwab REST → bronze + CH) | 2 hr |
| Replace `watchlist_service._enqueue_backfill` calls with the new pair | 1 hr |
| Tests: cold-start, idempotency, tip-fill window | 1 hr |

Outcome: adding a symbol triggers silver→CH (10s) + tip-fill
(seconds). Chart populates within ~15s instead of 90s.

### Phase G5 — Delete legacy path ② + wipe-and-rebuild runbook (0.5 day)

| Item | Effort |
|---|---|
| Remove `quick`/`intraday`/`daily` REST-to-CH backfill modes from `backfill_service.py` | 1 hr |
| New `scripts/rebuild_ch_from_silver.py` — wipes CH; bulk-restores from silver for every active watchlist symbol | 2 hr |
| Operator runbook: when/how to use it | 30 min |

Outcome: CH is purely a silver-derived cache. Wipe and rebuild is
a single command.

### Phase G6 — Gap handling UX in cockpit (parallel; cockpit work)

| Item | Effort |
|---|---|
| `GET /api/silver/coverage/{symbol}` — exposes silver watermark per symbol | 30 min |
| `GET /api/silver_to_ch/progress/{symbol}` — live progress for warming-up state | 1 hr |
| FE-2 Symbol page: "warming up" card while silver→CH backfills (already in frontend_plan) | covered in FE plan |

Outcome: frontend can show users when data is loading vs. when it's
ready.

---

## Sequenced timeline (recommended)

This week (this and next session):

1. **G2 (silver OHLCV build)** — finish TA-5.1.3..5.1.7 today + next session. ~3 days.

2. **G1 (dynamic universe)** — small focused commit between TA-5.1.4 and TA-5.1.5. The new silver build will benefit from a dynamic universe.

3. **G3 (silver nightly schedule)** — folded into TA-5.1.6 cleanly.

4. **Initial silver overnight backfill** — operator runs once after G2 lands.

Next week:

5. **G4 (silver→CH backfill + tip-fill)** — ~1 day. After this, the chart populates from silver.

6. **G5 (delete legacy + wipe-rebuild runbook)** — ~0.5 day. After this, CH wipe-and-rebuild is a single operator command and the system is fully production-grade.

7. **G6 (gap-handling UX)** — comes with FE-2 when the cockpit rebuild starts.

---

## Open decisions for operator

1. **Polygon nightly scope** — Option A (universe-driven) or Option B
   (whole market)?
   **My recommendation: Option B** (whole market). Cost is negligible;
   eliminates the "what if I add a new symbol" race entirely.

2. **`get_active_universe()` definition** — union of static seed +
   live watchlists? Or just live watchlists? Static seed is a "floor"
   so backtests have a guaranteed-stable list.
   **My recommendation: union.**

3. **Auto-promote behavior** — when a symbol is streamed and isn't in
   silver yet, the cockpit's warming-up card shows for ~24h until
   the next silver build catches it up. Alternative: trigger a
   one-shot silver build for that symbol immediately after the
   add_members call.
   **My recommendation: defer the one-shot for now** (G7 if needed
   after operating the system for a few weeks). The 24h wait is fine
   if the symbol was already in the universe; if it wasn't, the
   chart shows live + Schwab REST tip-fill which is enough.

4. **Whole-market Polygon storage** — `bronze.polygon_minute` jumps
   from 2.1B rows (5y × seed-ish) to ~6B (5y × whole-market). About
   100 GB S3 Standard. Trivial cost but worth flagging.
   **My recommendation: proceed.**

---

## Tactical: what I'm doing right now

Continuing TA-5.1 surgically (TA-5.1.3 merge → 5.1.4 orchestrator).
G1 (dynamic universe) folds in cleanly as a focused commit between
those steps.

I'll stop after TA-5.1.4 lands cleanly to checkpoint, then resume
TA-5.1.5..5.1.7 + G1 + G3.

After this whole sequence lands, **every gap in your data flow is
closed.** The system is production-grade.
