# Sector Rotation (RRG) Dashboard — Spec

Status: **Phase 1 — approved 2026-06-28**
Owner: Shane Licari

## 1. Goal

A "glance at the market" page that classifies the 11 S&P 500 sectors into
**Leading / Weakening / Improving / Lagging** relative to a benchmark (SPY),
in the spirit of a Relative Rotation Graph (RRG). This is *our own flavor* of
the technique — it does not need to pixel-match any reference UI, only to
deliver the insight: which sectors are gaining/losing relative strength and
where they sit in the rotation cycle.

Phase 1 ships the **11 SPDR sector ETFs**. The system is engineered so the
future ~50-theme catalog (custom multi-stock baskets) drops in **without
touching the RRG math, the API, or the frontend** — only a new group type and
its data resolution.

### Non-goals (Phase 1)
- The full ~50 custom theme catalog (each a hand-curated basket). Deferred to
  Phase 2; the architecture below reserves the seam for it.
- The donut "count per quadrant" widget and the week-by-week rotation timeline
  heatmap from the reference screenshot. These are pure presentation on top of
  the same payload — deferred to **Phase 1.5** once the engine is proven.
- Intraday RRG. Phase 1 is daily bars, weekly-sampled tails (standard RRG
  cadence).

## 2. Background — what an RRG measures

Each group is scored on two axes **relative to the benchmark**:

1. **RS-Ratio** — is the group out- or under-performing the benchmark?
2. **RS-Momentum** — is that relative performance accelerating or decelerating?

Both axes are centered at **100** (= in line with the benchmark). The four
quadrants follow directly:

| Quadrant   | RS-Ratio | RS-Momentum | Meaning                              |
|------------|----------|-------------|--------------------------------------|
| Leading    | > 100    | > 100       | Outperforming and still accelerating |
| Weakening  | > 100    | < 100       | Still ahead but losing steam         |
| Lagging    | < 100    | < 100       | Underperforming and still falling    |
| Improving  | < 100    | > 100       | Behind but turning up                |

The healthy rotation cycle runs clockwise:
`Improving → Leading → Weakening → Lagging → Improving`.

## 3. Architecture — the abstraction that makes Phase 2 free

The entire system pivots on one rule: **the RRG engine consumes an abstract
"rotation group" that resolves to a daily close series. It never sees a raw
symbol.**

```
RotationGroup (contract)
 ├─ EtfGroup     → passthrough: daily close series of one ETF     ← Phase 1 (XLK …)
 └─ BasketGroup  → aggregated index of N constituent symbols      ← Phase 2 (themes)
        │            (equal- or cap-weighted, normalized index)
        ▼  both yield a single daily close series
   RRG engine (pure math) → RS-Ratio, RS-Momentum, quadrant, weekly tail
        ▼
   RotationDashboard payload → API → React page
```

Adding a theme in Phase 2 = register a `BasketGroup` definition + implement
`BasketGroup.resolve()` (index construction over constituents). The math, the
route, and the UI are untouched.

This follows the service-module standards: **factories over inheritance**
(group kinds resolved via a registry, not a class hierarchy the UI knows
about), **result objects over raises**, **`from_settings()` construction**, and
**lazy provider imports**.

## 4. Data plan (step 0 — prerequisite)

RRG needs ~1 year of **daily** bars for each ETF and the benchmark. The read
path is **ClickHouse** — the fast single-symbol hot tier — not the cold lake.
RRG covers a fixed set of *streamed* symbols, so this is a single-symbol-read
problem (the lake is the whole-market path and is ~20–40s/symbol; reading the
12 sectors from it per request would take minutes — the wrong tier). CH
resamples `stocks.ohlcv_1m` to `1d` server-side (`toStartOfInterval`, ET
trading day, split-adjusted) and a single-symbol daily read is fast.

The setup is therefore: get the symbols *into* CH once (hot-load from the
lake), then keep them current by streaming. The ordered steps:

Symbols (12 total): benchmark **SPY** (already ingested) + the **11 SPDR
sectors**:

| ETF  | Sector                 |
|------|------------------------|
| XLK  | Technology             |
| XLV  | Health Care            |
| XLF  | Financials             |
| XLY  | Consumer Discretionary |
| XLP  | Consumer Staples       |
| XLE  | Energy                 |
| XLI  | Industrials            |
| XLB  | Materials              |
| XLRE | Real Estate            |
| XLU  | Utilities              |
| XLC  | Communication Services |

### 4.1 Add the 11 ETFs to the streaming universe
Add them to the canonical `stream_universe` so they're streamed (CH stays
current) and recognized by the nightly refresh going forward:

```bash
# idempotent — re-adding an active symbol is a no-op
POST /api/v1/stream/import   { "symbols": ["XLK","XLC","XLY","XLP","XLE",
                                            "XLF","XLV","XLI","XLB","XLRE","XLU"] }
```

SPY is already in the market-banner set; confirm it resolves under the active
universe and add if absent.

### 4.2 Lake history is already there (Polygon, whole-market)
`polygon_history_backfill.py` pulls **whole-market** flat-files, so every
symbol trading on an already-loaded date — including these ETFs — is **already
in `equities.polygon_raw`** up to the Polygon flat-file cutoff. No per-symbol
Polygon pull is required.

### 4.3 Recent gap (Schwab backfill)
Schwab 1-minute history only reaches back ~48 days, which is exactly the recent
window the lake needs topped up (the older history is Polygon's). Fill it for
the new symbols:

```bash
poetry run python scripts/schwab_history_backfill.py \
    --symbols XLK,XLV,XLF,XLY,XLP,XLE,XLI,XLB,XLRE,XLU,XLC,SPY \
    --days 48
```

Lands 1-minute bars into `equities.schwab_universe`.

> **Idempotency caution (from the backfill docstring):** re-running the same
> `(day, symbol)` set appends duplicate rows to `equities.schwab_universe`.
> Run this **once**; if a re-run is needed, delete the affected partitions
> first per `docs/architecture_v2/07_runbook.md`. Do not loop it blindly.
>
> **Token dependency:** Schwab's refresh token (~7-day life) must be valid or
> the backfill lands nothing (the known "frozen lake" failure mode). Re-auth
> via `scripts/schwab_get_refresh_token.py` first if the lake is stale.

### 4.4 Hot-load ClickHouse from the lake (the read tier)
Pull the polygon∪schwab daily history for the 12 symbols into CH
(`stocks.ohlcv_1m`) so reads are fast and complete from day one:

```bash
# bulk loader — loops fill_ch_from_lake_sync (read_arrow union) per symbol:
poetry run python scripts/rebuild_ch_from_lake.py --symbols XLK,XLV,…,XLC,SPY
```

This is the same mechanism bars_gateway AUTO uses to self-warm (the canonical
`fill_ch_from_lake` path); the bulk script just loops it over the universe.

### 4.5 Coverage verification (no silent gaps)
For each of the 12 symbols confirm daily bars span the lookback with no
multi-day holes, via the CH read path (`get_chart_bars(sym, interval="1d",
lookback_days=360, source=AUTO)`). Log per-symbol first/last date + bar count.
A symbol that fails coverage is **excluded with a logged reason** (the service
already does this), never silently rendered as a misleading partial series.

## 5. Backend — `app/services/sectors/`

New service module, standard folder template:

```
app/services/sectors/
  __init__.py
  schemas.py        # Pydantic contracts (below)
  definitions.py    # registry: 11 EtfGroups + SPY benchmark
  resolver.py       # RotationGroup -> daily close series (via bars_gateway)
  rrg.py            # pure math: RS-Ratio, RS-Momentum, quadrant, tail
  service.py        # SectorRotationService.from_settings(); orchestration
  README.md
  tests/
    test_rrg.py            # math correctness, quadrant boundaries (synthetic)
    test_resolver.py       # ETF passthrough; BasketGroup seam raises cleanly
    test_service.py        # dashboard assembly, coverage exclusion, idempotency
```

### 5.1 Contracts (`schemas.py`)

```python
Quadrant = Literal["leading", "weakening", "improving", "lagging"]
GroupKind = Literal["etf", "basket"]

class RotationGroup(BaseModel):
    id: str                 # stable key, e.g. "XLK"
    name: str               # "Technology"
    kind: GroupKind         # "etf" (Phase 1) | "basket" (Phase 2)
    benchmark: str          # "SPY"
    members: list[str]      # ["XLK"] for ETF; constituents for basket
    weights: dict[str, float] | None = None   # basket only

class RotationPoint(BaseModel):
    date: date
    rs_ratio: float
    rs_momentum: float
    quadrant: Quadrant

class SectorRotationState(BaseModel):
    group_id: str
    name: str
    current: RotationPoint
    tail: list[RotationPoint]     # weekly points, oldest → newest
    # convenience for the relative-strength line chart:
    relative_strength: list[tuple[date, float]]   # rel line vs benchmark

class RotationDashboard(BaseModel):
    benchmark: str
    as_of: date
    tail_weeks: int
    sectors: list[SectorRotationState]
    excluded: list[dict]          # [{group_id, reason}] — surfaced, not hidden
```

### 5.2 RRG math (`rrg.py`) — explicit, no magic numbers

Pure functions over a pandas daily close series. *Our flavor* of the JdK
RS-Ratio / RS-Momentum, defined plainly and documented so every constant is
named in `config.py` (not literal in the body):

```
rel(t)        = close_group(t) / close_benchmark(t)
rs_ratio(t)   = 100 * rel(t) / SMA(rel, RRG_RATIO_WINDOW)(t)
rs_momentum(t)= 100 * rs_ratio(t) / SMA(rs_ratio, RRG_MOM_WINDOW)(t)
quadrant(t)   = f(rs_ratio(t) ≷ 100, rs_momentum(t) ≷ 100)   # table in §2
```

Config knobs (defaults; tune later):
- `RRG_RATIO_WINDOW` = 50 (trading days; ~10 weeks of relative-strength base)
- `RRG_MOM_WINDOW`   = 20 (trading days; momentum smoothing)
- `RRG_TAIL_WEEKS`   = 12 (weekly points shown in the scatter tail)
- `RRG_LOOKBACK_DAYS`= 360 (history pulled to make the SMAs warm; ≤365 so the
  ClickHouse hot tier self-heals from the lake on a cold read)

The weekly tail is the last `RRG_TAIL_WEEKS` Friday (or last-session-of-week)
samples of `(rs_ratio, rs_momentum)`. Functions are total: insufficient history
→ returns a typed "insufficient data" result, never NaN-on-the-wire.

### 5.3 Resolver (`resolver.py`)
- `resolve(group) -> pd.Series` (daily close, date-indexed).
- `kind == "etf"` → `get_chart_bars(group.members[0], interval="1d",
  lookback_days=RRG_LOOKBACK_DAYS, source=AUTO)` → close series. **AUTO = read
  from ClickHouse** (fast, single-symbol), self-healing from the lake on a
  cold window. *Not* the cold lake source — that's the whole-market path.
- `kind == "basket"` → `BasketGroup` seam: normalize each constituent to a base
  date, weight, sum → index series. **Phase 1 raises `NotImplementedError`
  with a clear message** (the seam exists and is tested; it is not silently
  stubbed).
- **Bad-tick guard (`_despike`)** — a single reverting spike (e.g. a bad
  after-hours print that lands as the ET-trading-day close) is replaced by the
  neighbour mean and **logged**. A point is a spike only when it deviates
  >40% from both neighbours while the neighbours agree (so a genuine,
  non-reverting gap is preserved). Production financial data has bad prints;
  this keeps one from poisoning every relative-strength reading downstream.

### 5.4 Service (`service.py`)
`SectorRotationService.from_settings()` →
`build_dashboard(benchmark="SPY", tail_weeks=…) -> RotationDashboard`:
1. Resolve benchmark series once.
2. For each registered group: resolve, run `rrg`, collect state; on
   coverage/resolution failure append to `excluded` with a reason (logged).
3. Return the dashboard result object.

## 6. API — `app/api/routes_sector_rotation.py`

```
GET /api/v1/sectors/rotation
      ?benchmark=SPY            (default SPY)
      &tail_weeks=12            (default RRG_TAIL_WEEKS)
  -> RotationDashboard
```

Registered in `app/main_api.py` next to the other `routes_*`. Frontend OpenAPI
types regenerated (`types.gen.ts`). Errors surface as proper HTTP errors with
messages — no empty-200 masking.

## 7. Frontend — `frontend/src/routes/sectors.tsx`

New route + nav entry. TanStack Query hook `useSectorRotation()` → the endpoint.

Phase 1 components:
1. **RRG quadrant scatter** (the centerpiece) — custom SVG (lightweight-charts
   is not a scatter lib). Four labeled/colored quadrants, axes crossing at 100,
   one marker per sector at its current point with a fading **weekly tail**
   polyline. Hover → sector name + ratio/momentum.
2. **Sector list / chips** — each sector with a quadrant badge
   (Leading/Weakening/Improving/Lagging) and current RS-Ratio, colored by
   quadrant. Sorted by quadrant then ratio.
3. **Relative-strength line chart** — reuse lightweight-charts multi-line; the
   `relative_strength` series for selected sectors against a 100 zero-line, so
   you can see the cross of the benchmark over time.

Deferred to Phase 1.5: the **donut** (count per quadrant) and the **rotation
timeline heatmap** (each sector's quadrant week-by-week). Both render from the
same payload.

## 8. Verification (per testing + coding standards)

- **Unit**: `rrg.py` math on synthetic series with known answers; quadrant
  boundary cases (exactly 100); insufficient-history result type; resolver ETF
  passthrough; `BasketGroup` raises the seam error.
- **Coverage check**: per-symbol daily span/holes logged before the page is
  declared working (§4.4).
- **Integration (live data)**: build the dashboard from the real ETF series;
  sanity-assert quadrants are plausible (e.g. not all 11 in one quadrant) and
  every sector has a non-empty tail.
- **Frontend**: `npm run build`, render the page against the live API,
  screenshot the scatter + list before claiming done. Hard-refresh noted to the
  user.

## 9. Phase boundaries

- **Phase 1 (this spec):** data prereq + backend module + API + scatter / list
  / relative-strength chart for the 11 ETFs. ✅ shipped.
- **Phase 1.5:** rotation-table redesign (clean scatter + focus-to-trail +
  per-sector quadrant-journey cells + RS sparklines). ✅ shipped.
- **Phase 2 (shipped 2026-06-28):** `BasketGroup` resolution implemented
  (`resolver._basket_close_series` — equal-weight/weighted composite of
  normalized member closes, missing-member drop, date intersection). First
  theme registered: `MINERS` (Precious Metals Miners, 12 constituents).
  `SectorRotationState` gained `kind` + `members`; the UI shows a per-basket
  holdings expander. **No engine/API/UI rewrite was required — a theme is one
  entry in `definitions._THEMES`.**
- **Tracked-in-universe rule:** `universe_sync.ensure_tracked_in_universe()`
  (scheduled at app startup) reconciles all sector + theme constituents into
  the stream universe and Schwab-tip-fills new ones, so a registered theme is
  streamed/tip-filled with no data gaps and grows `equities.schwab_universe`.
- **Future:** scale the theme catalog (more baskets); optional cap-weighting.
