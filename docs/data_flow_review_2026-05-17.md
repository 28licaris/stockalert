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
| 1 | Polygon nightly → bronze, **whole market** | Polygon nightly accepts `POLYGON_NIGHTLY_SYMBOLS={seed,active,all,...}`. `all` = whole-market (free via flat-files). G1 added `active` keyword (= SEED ∪ active watchlists). Default still `seed`; flip to `all` for whole-market or `active` for dynamic. | None (op decision is which spec to use). |
| 2 | Schwab nightly → bronze, **universe seed (100)** | ✅ Built; G1 added `active` keyword. Default still `seed`; flip to `active` for dynamic universe. | None. |
| 3 | Silver merge polygon > schwab | ✅ For corp-actions (TA-5.0). ✅ For OHLCV (TA-5.1.1–.6 LANDED 2026-05-17 — silver.ohlcv_1m + silver.bar_quality + orchestrator + reader + HTTP + MCP). Pending operator validate + initial backfill (TA-5.1.7). | None (pending live verify). |
| 4 | Silver syncs periodically | ✅ TA-5.1.6 in-process nightly loop (default 23:00 UTC, 1h after Schwab nightly). Gated on `SILVER_OHLCV_BUILD_ENABLED=true`. silver_corp_actions_build still operator-triggered (separate). | None. |
| 5 | CH seeded/hot-loaded from silver | 🔲 Not built. Legacy path ② still pulls Schwab REST → CH directly on `add_members`. | **GAP** — TA-5.3 (silver_to_ch_backfill). |
| 6 | Schwab stream → CH (only live source) | ✅ Built (path ①). Live-stream rows tagged `schwab-stream`. | None. |
| 7 | Live writer: stream → bronze every 5 min | ✅ TA-5.7 done. | None. |
| 8 | Edge: new streamed symbol → add to universe + backfill S3 | ✅ G1 LANDED 2026-05-17. `get_active_universe()` = SEED ∪ active-watchlist symbols. Set `*_NIGHTLY_SYMBOLS=active` to flip each nightly to the dynamic universe. Adding any symbol to any watchlist now grows the nightly bronze refresh + silver build automatically. | None (op decision: flip env to `active`). |
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

### Phase G1 — Dynamic universe & nightly scope [✅ LANDED 2026-05-17]

| Item | Status |
|---|---|
| `get_active_universe()` helper — placed in `app/services/universe/active_universe.py` (not `app/data/seed_universe.py`; keeps the static-tuple module pure, free of CH dependencies) | ✅ |
| `resolve_universe_spec("seed" \| "active" \| CSV)` — single resolver used by all three nightlies | ✅ |
| Polygon nightly: `POLYGON_NIGHTLY_SYMBOLS=active` now valid (alongside `seed`/`all`/CSV) | ✅ |
| Schwab nightly: `SCHWAB_NIGHTLY_SYMBOLS=active` now valid | ✅ |
| Silver build: `SILVER_OHLCV_BUILD_SYMBOLS=active` now valid + `SilverOhlcvBuild.run_nightly()` default flipped to `get_active_universe()` | ✅ |
| Tests: 18 cover seed/active/CSV routing, CH-outage fallback, kinds filter, each nightly's delegation through the shared resolver, dynamic-build default | ✅ |
| Doc update: `streaming_universe_model.md` + `data_flow_review` updated | ✅ |

**Defaults preserved.** Each `*_NIGHTLY_SYMBOLS` env var still
defaults to `seed` (curated 100). Operators opt into the dynamic
universe explicitly by setting it to `active`.

Outcome: any symbol added to any watchlist gets nightly bronze
backfill from both providers + silver build automatically (within
24h) once the operator flips `*_NIGHTLY_SYMBOLS=active`.

Recommended production setting (per "Option B" above):
```
POLYGON_NIGHTLY_SYMBOLS=all      # whole-market via flat-files (free)
SCHWAB_NIGHTLY_SYMBOLS=active    # only what's watchlisted
SILVER_OHLCV_BUILD_SYMBOLS=active
```

### Phase G2 — Complete TA-5.1 (silver OHLCV build) [✅ .1–.6 LANDED 2026-05-17]

| Item | Status |
|---|---|
| TA-5.1.3: provider precedence merge + bar_quality computation | ✅ LANDED |
| TA-5.1.4: orchestrator (`SilverOhlcvBuild.build_slice/window/nightly/full`) + tests | ✅ LANDED |
| TA-5.1.5: SilverOhlcvReader + HTTP `/api/silver/*` + MCP `get_silver_bars` + `get_silver_bar_quality` | ✅ LANDED |
| TA-5.1.6: operator CLI `scripts/run_silver_ohlcv_build.py` + in-process nightly loop | ✅ LANDED |
| TA-5.1.7: flip `SILVER_OHLCV_BUILD_ENABLED=true` + run `--full` once + verify | ⏳ pending (operator step) |

Outcome (after .1.7): `silver.ohlcv_1m` exists, gets refreshed
nightly from bronze, available to all consumers via the same
Pydantic contract over HTTP + MCP.

### Phase G3 — Wire silver build into nightly schedule [✅ LANDED as TA-5.1.6]

Merged into TA-5.1.6 above. In-process asyncio loop at default
23:00 UTC (1h after Schwab nightly), gated on
`SILVER_OHLCV_BUILD_ENABLED`. Records best-effort run row to
`ingestion_runs`. Lifespan-shutdown symmetric. 12 tests cover
scheduling + gating + summary shape.

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

## Scheduling architecture — in-process, not external cron

**All ingest jobs are in-process asyncio background tasks wired
into the FastAPI lifespan.** Confirmed by the existing pattern:

- `backfill_service` (started via `_safe_start` in lifespan)
- `watchlist_service` (streams Schwab; live → CH)
- `live_lake_writer` (TA-5.7; every 5 min, CH → bronze)
- `nightly_polygon_refresh` (lifespan asyncio.create_task; sleeps
  until configured hour)
- `nightly_schwab_refresh` (same pattern)
- `journal_sync_service` (Schwab account sync, every 5 min)
- `_initial_gap_sweep_after_warmup` (one-shot after startup)

No external cron, no systemd timer, no OS-level scheduler. The
FastAPI process IS the scheduler. Restarting the server restarts
every job from a clean state.

**What we'll add** to this same pattern:

| Job | Cadence | Started by | What it does |
|---|---|---|---|
| `silver_corp_actions_build` | nightly 01:30 ET | lifespan asyncio task | bronze.{provider}_corp_actions → silver.corp_actions |
| `silver_ohlcv_build` | nightly 02:30 ET | lifespan asyncio task | bronze → silver.ohlcv_1m + silver.bar_quality |
| `bronze_compaction` | daily 03:00 ET | lifespan asyncio task | Athena OPTIMIZE on bronze.{tables} |
| `silver_compaction` | weekly Sat 03:00 ET | lifespan asyncio task | Athena OPTIMIZE on silver.{tables} |

All gated by `SETTINGS.*_ENABLED` flags so operators can disable
individual jobs without code changes.

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

## Final step — wipe CH + verify (after all phases land)

Once G1-G5 are in, the final verification:

1. **Run** `scripts/rebuild_ch_from_silver.py --wipe`:
   - Stops the live stream momentarily
   - Truncates CH `ohlcv_1m` + `ohlcv_5m` + `ohlcv_daily`
   - Reloads every active watchlist symbol from silver via
     `silver_to_ch_backfill`
   - Restarts live stream

2. **Verify each use case end-to-end:**

   | Use case | What to check |
   |---|---|
   | Live ticks land in CH | New bar in seconds of market activity |
   | Live ticks land in bronze within 5 min | `audit_bronze.py --check live_freshness` is 🟢 |
   | Chart loads history fast | `/symbol/AAPL` loads 2y in <2s |
   | Add new streamed symbol | `add_members("XYZ")` → CH has ≥48d within ~10s |
   | Silver freshness | `silver.ohlcv_1m` includes yesterday's bars after 02:30 ET |
   | Adjusted vs raw | NVDA 2024-06-07: close_raw≈$1208.88, close_adj≈$120.88 |
   | Provider precedence | Polygon-covered bars show `source_provider="polygon"` |
   | bar_quality | Full-coverage days show `actual_bars ≈ expected_bars` |

3. Record verification in BUILD_JOURNAL as the **TA-5 LANDED** entry.
