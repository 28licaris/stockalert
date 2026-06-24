# Elliott Wave Theory — System Spec (backend + frontend)

**Status:** PROPOSED — awaiting sign-off. No code until approved.
**Scope:** The full EWT product surface: wave-count tracking for equities &
futures, a daily recompute pipeline, probability-ranked primary/secondary
counts, trade alerts with targets and day/swing horizon, a dedicated EWT
page, and agent (LLM/MCP) interaction.

**Companion docs**
- [elliott_wave_plan.md](elliott_wave_plan.md) — strategy, rationale, phasing.
- [elliott_wave_ew1_ew2_spec.md](elliott_wave_ew1_ew2_spec.md) — the engine
  build contract (pivots + rule engine). This doc is the layer **above** it.
- Doctrine: [`.claude/skills/elliott-wave/SKILL.md`](../.claude/skills/elliott-wave/SKILL.md).

---

## 1. Goals & non-negotiables

**Goal.** Maintain a continuously-updated, falsifiable Elliott Wave count for
every symbol in the universe (equities + futures), expose it to humans (a
dedicated page + chart overlay) and to agents (MCP), and turn high-confidence
wave setups into actionable trade alerts with a defined entry, stop, and
profit target — tagged day vs swing.

**Non-negotiables** (inherited from doctrine, enforced here):
1. **No look-ahead.** A count stamped for date `D` uses only bars `≤ D`. Daily
   recompute appends; it never rewrites a past day's stored count.
2. **Primary + secondary, both with probability.** Never a single forced
   count. Probabilities are normalized across the surviving candidates.
3. **Every count carries an invalidation price.** That price is the alert's
   stop and the count's falsifier.
4. **Reproducible.** Every stored row carries `engine_ver + git_sha`;
   re-running the same engine on the same bars yields identical counts.

---

## 2. Scope: equities & futures, one engine

The wave engine is asset-agnostic — it consumes OHLC bars and emits counts.
Routing reuses what already exists:

| Concern | Equities | Futures |
|---|---|---|
| Bars source | `BarsGateway` → `equities.*` lake / ClickHouse | `BarsGateway` `/`-prefix routing → `futures.*` |
| Universe | equities universe (alpha-sorted) | futures roots catalog (`/ES`, `/NQ`, …) |
| Adjustment | split-adjusted reader (`AdjustedOhlcvReader`) | continuous roots, no adjustment tier |
| Count store | `equities.elliott_wave_labels` | `futures.elliott_wave_labels` (mirror schema) |
| "After trading" | equities session close (ET) | futures settlement (per-root session) — see §4 |

One `WaveEngine`, one `WaveReader`; the only branch is which lake/universe/
session calendar the symbol routes to (the `/`-prefix convention the gateway
already uses).

---

## 3. Data model — the wave-count store

Two mirrored Iceberg tables (`equities.elliott_wave_labels`,
`futures.elliott_wave_labels`). One row per `(symbol, interval, as_of_date,
engine_ver)` — the **as-known-on-that-date** count (append-only).

```
elliott_wave_labels
────────────────────────────────────────────────────────────────
symbol           STRING     partition (bucket)
as_of_date       DATE       partition (month)  — the trading day the count is "as of"
interval         STRING     1d | 1h | 15m | 5m   (degree-bearing timeframe)
as_of_ts         TIMESTAMP  last bar used
asset_class      STRING     'equity' | 'future'

-- primary count
p_structure      STRING     'impulse' | 'zigzag'
p_direction      STRING     'up' | 'down'
p_current_wave   STRING     '1'..'5' | 'A'|'B'|'C' | null
p_degree         INT        fractal degree (0..4)
p_probability    FLOAT      normalized over surviving candidates (Σ over rows = 1)
p_invalidation   FLOAT      price that voids the primary count
p_pivots         STRING     JSON [{ts, price, kind, label}]  (the labeled swing points)
p_targets        STRING     JSON {label: price}  (Fib projection targets)

-- secondary count (same shape, the runner-up)
s_structure ... s_targets   (nullable — present when a credible alternate exists)

-- bookkeeping
fib_score        FLOAT
rule_score       FLOAT
engine_ver       STRING     partition-stable; bump on engine change
git_sha          STRING
computed_at       TIMESTAMP
```

Notes
- **Primary + secondary are first-class columns** (not a generic top-K blob)
  because the product surfaces exactly two paths. Any further alternates the
  engine produced are dropped at store time (kept only in the live
  `WaveReader` response for agents who want them).
- `p_probability + s_probability ≤ 1`; remainder = "other/no-clear-count"
  mass. This is the honest uncertainty signal the UI shows.
- `interval` is the **degree-bearing timeframe**. A symbol has up to 4 rows
  per day (one per tracked interval). The daily horizon view reads `1d`;
  day-trade setups read `15m`/`5m`.

---

## 4. Daily recompute pipeline ("update counts once a day after trading")

A registered job in the existing `app/services/jobs/` framework. The
`JobRegistry` **does not schedule** — it catalogs jobs for the Status page,
provides locked `run_now` manual triggering, and joins last-run data from the
CH `ingestion_runs` table. **Scheduling is a background loop in `main_api`
lifespan** (the pattern every existing job follows), gated by a `setting_key`.

```
register(name="ewt_recompute", schedule="nightly",
         setting_key="ewt_recompute_enabled",
         run_now=audit_run("ewt_recompute")(recompute_cycle))
# main_api lifespan: nightly loop → registry.run_now("ewt_recompute")
```
The `audit_run(...)` wrapper records each cycle to `ingestion_runs`, so the
Status page gets last-success + row-count for free, like every other job.

**Trigger.** Once per trading day, after the session the count is "as of":
- Equities: after the regular session settles (run ~20:30 ET to include the
  full 04:00–20:00 ET extended day; the trading-day math uses `yesterday_et()`
  / NY-tz date — never UTC date).
- Futures: per-root settlement. Roots share a nightly window; run after the
  CME daily settlement for the root's session. (Decision **OD-1** below: one
  global nightly run vs per-session — recommend one nightly run keyed off the
  equities close for v1, widen later.)

**What it does, per symbol × tracked interval:**
1. Pull bars from `BarsGateway` (lake-backed, ground truth) up to the session
   close. Lake is the source; ClickHouse is the fillable cache.
2. `PivotDetector.detect_multidegree(...)` → causal pivots.
3. `WaveEngine.label(...)` → primary + secondary + probabilities + targets +
   invalidation.
4. **Append** one row per interval to `elliott_wave_labels`. Never
   overwrite/delete a prior `as_of_date` (bronze-idempotency model: append
   only; maintenance via Athena).
5. Evaluate alert rules (§6) against the fresh counts → emit/refresh alerts.

**Operational discipline** (coding standards): pipefail/structured logging,
per-symbol completion markers (log zero-count symbols explicitly, not
silently), a preflight that checks bar availability before the full run (it's
a >5-min job over the universe), and cross-side verification that the row
count appended matches symbols processed.

**Cost control.** Universe × 4 intervals × 1/day is bounded and cheap (daily,
not per-bar). Intraday day-trade refresh (§6.2) is the only higher-frequency
path and reuses the cached daily structure.

---

## 5. Engine & exposure layer

### 5.1 Engine
Per [elliott_wave_ew1_ew2_spec.md](elliott_wave_ew1_ew2_spec.md): pure
`app/signals/elliott/` package. This spec adds the **probability
normalization** on top of the engine's raw composite scores:

```
p_i = score_i / Σ_j score_j      over candidates passing all hard rules
primary   = argmax p_i
secondary = 2nd-highest p_i (if ≥ OD-2 threshold, default 0.15)
"other" mass = 1 − p_primary − p_secondary   (shown as uncertainty)
```

Probabilities are **relative likelihoods, not calibrated**, until the
calibration phase (plan EW-9 territory). The UI and agents are told this.

### 5.2 Reader
Mirrors `IndicatorReader` exactly: stateless, `from_settings()` factory,
`lru_cache`'d underlying bar readers, and a **`backend` knob** —
`'store'` (read the stored daily `elliott_wave_labels` rows) vs `'compute'`
(recompute live for the edge bar). Consumers see only the Pydantic shape, not
the backend — the same contract-not-strategy rule `IndicatorReader` uses for
its `'compute'|'gold'` knob.
```
app/services/readers/wave_reader.py
  WaveReader.from_settings() -> WaveReader
  WaveReader.get_state(symbol, interval='1d', *, as_of=None, backend='store') -> WaveLabeling
  WaveReader.get_history(symbol, interval, start, end)                        -> list[WaveLabeling]
```
Routes equities vs futures by the `/`-prefix, same as `BarsGateway`.

### 5.3 HTTP
```
app/api/routes_wave.py
  GET /api/wave/{symbol}?interval=1d         → current WaveLabeling
  GET /api/wave/{symbol}/history?interval=1d → labeled history (for the overlay)
  GET /api/wave/alerts                        → active EWT alerts (§6)
```

### 5.4 MCP (agent surface) — §7
```
app/mcp/tools/wave.py   (@mcp.tool() fns; added to register_all_tools() in server.py)
  get_wave_state(symbol, interval)        → primary/secondary + probabilities + invalidation
  evaluate_wave_targets(symbol, interval) → entry / stop / Fib targets, numeric
  list_wave_alerts(filter)                → active high-probability setups
```
Each tool is a thin adapter over `WaveReader` / the alerts service, wrapped in
the `tool_call(...)` middleware with an `@lru_cache(maxsize=1)` reader —
identical to `tools/indicators.py`. The module must be **read-only** (no
write-side imports) and wired into `register_all_tools()`; both are enforced
by `app/mcp/tests/test_mcp_discovery.py` (which also locks a tool count — bump it by 3).

---

## 6. Alerts — high-probability trade setups

An alert is a **complete trade plan** derived from a wave count, not a bare
signal. New surface (no alerts service exists today; closest precedent is
`live/monitor_service._persist_signal`).

```
app/services/alerts/   NEW
  schemas.py   — WaveAlert
  rules.py     — setup detectors (pure, over a WaveLabeling)
  service.py   — evaluate + persist + dedupe
  store.py     — alert persistence (ClickHouse table wave_alerts)
```

### 6.1 WaveAlert shape
```
WaveAlert
─────────────────────────────────────────────
symbol, asset_class, interval, degree
setup            'wave2_entry' | 'wave4_entry' | 'wave5_exit' | 'impulse_complete'
direction        'long' | 'short'
trade_type       'day' | 'swing'              ← §6.3
probability      FLOAT   (primary count prob)
entry            FLOAT   (trigger price/zone)
stop             FLOAT   (= count invalidation)
target_1         FLOAT   (nearest Fib extension)
target_2         FLOAT   (next Fib extension)
risk_reward      FLOAT   ((target_1 − entry)/(entry − stop))
as_of_date, created_at, status ('active'|'triggered'|'invalidated'|'expired')
rationale        STRING  (human-readable: "Wave 2 .618 retrace, R:R 3.1, stop below wave-1 origin")
```

### 6.2 When alerts fire
- **Daily (swing):** the recompute job (§4) evaluates `rules.py` against fresh
  `1d`/`1h` counts. A setup with `probability ≥ OD-3 (default 0.6)` and
  `risk_reward ≥ OD-4 (default 2.0)` becomes an active alert for the next
  session.
- **Intraday (day):** the live monitor (`monitor_service`) evaluates `5m`/`15m`
  counts against the **pre-computed daily context** (e.g. "we're in a daily
  wave 3 → take intraday wave-2 longs only"). Reuses the cached daily
  structure; recomputes only the intraday edge. This is the one >daily path —
  gated behind OD-1/phase EWT-6.

### 6.3 Day vs swing classification
Derived from the **degree-bearing interval** of the setup:

| Setup interval | trade_type | Typical hold |
|---|---|---|
| `5m`, `15m` | `day` | intraday, flat by close |
| `1h`, `1d` | `swing` | days–weeks |

The target is always the count's Fib **extension** (e.g. wave 3 = 1.618×wave 1,
or wave 5 = wave 1). The stop is always the count's **invalidation** price.
This makes every alert self-defining: entry, stop, target, horizon — no extra
knobs.

### 6.4 Alert delivery
Active alerts surface in three places: the EWT page (§8), `GET /api/wave/alerts`,
and the `list_wave_alerts` MCP tool. (Push/email delivery is out of scope for
v1 — OD-5.)

---

## 7. Agent interaction ("an agent should interact with EWT on a security")

The existing assistant gains EWT via the MCP tools in §5.4. Dispatch is the
assistant's in-process `MCPToolRunner` (`assistant/runner.py`), which calls
`mcp.call_tool()` for any **allow-listed** tool name. Wiring EWT in = add the
three tool names to the assistant allowlist (`assistant/policy.py`) + the
prompt context block below. No new agent infra.

**Per-symbol agent flow:**
1. Agent calls `get_wave_state(symbol, interval)` → gets primary/secondary
   counts, probabilities, current wave, invalidation.
2. Agent calls `evaluate_wave_targets(symbol, interval)` → numeric entry/stop/
   targets to reason over.
3. Agent can call `list_wave_alerts({symbol})` → existing setups.

**Context block** injected into the assistant prompt when a symbol is in scope:
```
ELLIOTT WAVE STATE — AAPL (1d)
  Primary  (P=0.64): impulse up, in wave 3 (degree 2)
    invalidation 184.50 · target 198.20 (1.618×w1) · 205.0 (2.618×w1)
  Secondary(P=0.21): wave A of zigzag down — invalidation 191.0
  Uncertainty: 0.15 (no clear count mass)
```

The agent is instructed (skill doctrine): prefer `get_wave_state` over
hand-deriving counts; treat `P<0.5` as "no clear count"; never invent a count
when the tool returns none.

---

## 8. Frontend — dedicated EWT page + overlay

### 8.1 New route
```
frontend/src/routes/ewt.tsx        NEW — the EWT analysis page
frontend/src/api/wave.ts           NEW — typed client for /api/wave/*
frontend/src/components/wave/      NEW — overlay + panels
```

### 8.2 EWT page layout
A symbol-centric analysis page (the "separate page just for EWT"):

- **Header:** symbol search (equities + futures, `/`-prefix aware), interval
  selector (`5m/15m/1h/1d`), asset-class tab (equities | futures — reuse the
  repo's `role=tablist` pattern, per UI convention).
- **Chart (primary panel):** candlesticks with the **wave overlay** —
  - primary count: solid labeled swing lines (0-1-2-3-4-5 / A-B-C),
  - secondary count: dashed/ghosted,
  - invalidation as a horizontal stop line,
  - Fib target lines projected forward.
- **Count panel (side):** primary vs secondary as two cards with probability
  bars, current wave, invalidation, targets, and the "uncertainty" remainder.
- **Alerts panel:** active `WaveAlert`s for this symbol — entry/stop/target/
  R:R/day-or-swing badge.
- **Agent panel (optional):** "Ask about this count" → routes the symbol +
  wave state into the assistant.

### 8.3 Universe view (secondary)
A scan tab on the same page: all symbols with an active high-probability setup
(reads `GET /api/wave/alerts`), sortable by probability / R:R, filterable by
day vs swing and asset class. This is the operator's daily worklist after the
overnight recompute.

### 8.4 Reuse
- Chart: extend `OhlcvChart` (`@/components/charts/OhlcvChart`, built on
  **lightweight-charts v4**). Wave paths = line series; invalidation + Fib
  targets = `createPriceLine`; wave labels = series markers. No new chart lib.
- Overlay is a data layer fed by `GET /api/wave/{symbol}/history`.

---

## 9. Phasing (EWT-N — extends the plan)

| Phase | Deliverable | Depends on |
|---|---|---|
| EWT-1 | Causal pivots (EW-1 build spec) | — |
| EWT-2 | Rule engine + primary/secondary + probability (EW-2 build spec) | EWT-1 |
| EWT-3 | `elliott_wave_labels` store (equities + futures) + daily recompute job | EWT-2 |
| EWT-4 | `WaveReader` + HTTP routes + MCP tools | EWT-3 |
| EWT-5 | EWT frontend page + chart overlay + count/alert panels | EWT-4 |
| EWT-6 | Alerts service (daily swing alerts) + universe scan tab | EWT-4 |
| EWT-7 | Intraday day-trade alerts via live monitor | EWT-6 |
| EWT-8 | Agent context block + assistant integration | EWT-4 |
| EWT-9 *(future/optional)* | Analyst-content ingestion agent (§12) | EWT-5 |

EWT-1→EWT-5 is the visible MVP (a working EWT page over daily counts). EWT-6+
add the alerting/agent edge. EWT-9 is a purely-additive future option (§12) —
it depends on nothing in the core path. Each phase gets its own build spec at
the EW-1/EW-2 level of detail before code.

---

## 10. Open decisions (need a call before the dependent phase)

| # | Decision | Recommendation |
|---|---|---|
| **OD-1** | Futures recompute cadence: one global nightly run vs per-session | One nightly run (keyed off equities close) for v1; per-session later. |
| **OD-2** | Secondary-count threshold (min probability to store/show) | 0.15. Below it, show "primary + uncertainty" only. |
| **OD-3** | Alert probability floor | 0.60 for swing, tune day-trade separately. |
| **OD-4** | Alert min risk:reward | 2.0. |
| **OD-5** | Push/email alert delivery | Out of v1 scope; surface in-app only. |
| **OD-6** | Tracked intervals | `5m, 15m, 1h, 1d` (4 degrees). Add `1w` only if swing demand appears. |
| **OD-7** | Probability calibration | Defer (relative likelihoods in v1); revisit when forward-return data accrues. |
| **OD-8** | Analyst-scrape source scope (§12) | Allow-listed, ToS-permitting sources only; settle before any fetch code. Future phase. |

---

## 11. Out of scope for v1 (explicit backlog)

- Calibrated probabilities (isotonic against forward wave persistence).
- Flats / triangles / diagonals / extended subtypes (impulse + zigzag only).
- Multi-symbol wave correlation.
- Push/email/SMS alert delivery.
- RL/CNN training tracks (plan EW-8/EW-9) — gated on EWT showing edge first.
- Analyst-content ingestion (§12) — a named future option, not v1.

---

## 12. Future option — analyst-content ingestion agent (EWT-9)

A backlog/optional capability: an agent that ingests public EWT analysis for
both equities and futures — analysts who publish counts/commentary — and folds
it in as a **secondary, clearly-labeled sentiment overlay**, never as a source
of the system's own count.

**Why overlay, not input** (this is what keeps it from poisoning the product):
- **Honesty doctrine preserved.** Our deterministic engine count stays the
  source of truth. External counts show side-by-side ("analyst X also reads
  wave 3 here") and may flag agreement/divergence, but never silently rewrite
  our count.
- **No look-ahead by construction.** Each item is stamped with its
  `published_at`; it attaches only to bars ≥ publish time. Backtests ignore it
  unless it predates the bar — same invariant as every other feature.

**Sketch** (slots into the existing ingest layer):
```
app/services/ingest/ewt_analyst_scrape/   (future)
  sources.py   — allow-listed source registry (RSS / blogs / video transcripts / X)
  fetch.py     — polite fetch: robots.txt + ToS + rate-limit respected
  extract.py   — LLM extraction → {symbol, asset_class, structure, wave, bias,
                 target, invalidation?, published_at, source_url}
  store.py     — append-only CH `analyst_wave_views`; dedupe by (source, url)
```
- **Surfaces:** an "Analyst views" panel on the EWT page (§8) beside our count,
  and a `get_analyst_wave_views(symbol)` MCP tool so the agent can compare its
  read to the community's.
- **Hard constraints / risks:** legal/ToS is the gating decision — only
  allow-listed sources that permit it; respect robots.txt, rate limits, and
  paywalls; never scrape behind auth. Analyst counts are noisier and more
  subjective than ours — weight as sentiment, always show provenance, never
  auto-trade off them. LLM extraction is per-item — batch nightly, cache by URL.
- **Dependency:** none in the core path. Build only after EWT-1→8 prove the
  core product. Legal/ToS scope (OD-8) must be settled before any fetch code.
