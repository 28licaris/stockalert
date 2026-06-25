# Symbol Onboarding — Hotload + Gap-Fill

**Status:** hotload-on-add IMPLEMENTED (§3.1); gap-fill still proposal —
needs signoff. Branch `feat/lake-read-followups`.

**Hotload done:** `SYMBOL_HOTLOAD_ENABLED` (default true) /
`SYMBOL_HOTLOAD_DAYS` (default 30, sized for <5s first paint) gate the
on-add quick fill (provider-agnostic; Schwab by default). `enabled=false`
= pure stream-from-now. Reuses the existing latency-first quick backfill
(idempotent + single-flight); independent of the deep lake warmup
(`lake_warmup_enabled`). Per-add override + an explicit per-hotload
provider knob are deferred follow-ups (today it uses the configured
history provider).

What happens, end to end, when a symbol is added and then charted: it
should **stream forward immediately**, **paint a recent window fast**,
**backfill deep history**, and **repair any residual gaps** — all in
the background, none of it blocking the chart request.

Complements:
- [`lake_read_layer_design.md`](lake_read_layer_design.md) — the cold
  read engine (`read_arrow` / `union_arrow`) this builds on. Gap-fill is
  the next stage on the union's output.
- [`standards/data/symbol_lifecycle.md`](standards/data/symbol_lifecycle.md)
  — ingest paths + the nightly/weekly schedule.
- [`standards/data/timezone_et_vs_utc.md`](standards/data/timezone_et_vs_utc.md)
  — the ET trading-day grid the gap detector measures against.

This doc covers the **read/onboarding path**. It does not change the
weekly Spark adjustment job or the live stream wiring.

## 1. The problem

Adding a symbol today already does a lot — it joins `stream_universe`,
subscribes to the live Schwab WebSocket (bars flow forward into CH
within ~100ms), and kicks a background *warmup* (Schwab REST tip-fill +
`lake_to_ch_backfill`) gated by `lake_warmup_enabled`. But:

1. The warmup window/provider/on-off are **hardcoded internals**, not
   tunable per add or per deployment.
2. Charting a brand-new symbol returns `[]` on first paint, then fills
   ~10-15s later — with **no signal** to the UI that history is loading.
3. The whole thing assumes the **lake already has the symbol's deep
   history** (`polygon_adjusted`, built *weekly*). For a fresh IPO, an
   off-universe ticker, or a symbol added between Spark runs, the union
   has **residual gaps** that nothing repairs on demand — you wait for a
   nightly/weekly job or run a backfill script by hand.

## 2. Goals / non-goals

**Goals**
- Three **composable, independently-toggleable** background stages on
  add: **hotload** (fast recent), **deep history** (lake), **gap-fill**
  (residual repair). Each non-blocking.
- Hotload is **configurable**: on/off, lookback days (calibratable),
  provider (default Schwab) — global default + per-add override.
- Gap-fill detects holes on the **union** (calendar-aware) and fills
  only the residual from a selected provider, persisting durably.
- Never block the chart request; surface a "history loading" signal.

**Non-goals**
- No change to the weekly Spark adjustment job or the live stream.
- No new query engine — gap-fill rides the `read_arrow`/`union_arrow`
  seam already built.
- Not a feature store or precomputed-features surface.

## 3. The three stages (composable, all background)

```
add(SYM)
  ├─ subscribe live WS ───────────────► CH ohlcv_1m   (forward, ~immediate)
  └─ background, non-blocking, in parallel:
       (A) HOTLOAD       last N days from provider REST → lake + CH     [fast first paint]
       (B) DEEP HISTORY  polygon_adjusted ∪ schwab_universe → CH        [years behind]
       (C) GAP-FILL      detect residual holes on the union → provider  [repair]
                         → lake → CH
   chart request returns what CH has now; never waits on A/B/C
```

Hotload makes the chart paint *now*; deep history fills the *years*
behind it; gap-fill patches *holes between them*. They are distinct
knobs — a deployment can run any subset.

### 3.1 Hotload-on-add (the fast recent tier)

A bounded provider-REST pull so a freshly-added symbol charts quickly,
independent of whether the lake has deep history yet.

**Config — global default (settings / env) + per-add override:**

| Setting (env) | Default | Meaning |
|---|---|---|
| `SYMBOL_HOTLOAD_ENABLED` | `true` | `false` → pure **stream-from-now** (no backfill) |
| `SYMBOL_HOTLOAD_DAYS` | `30` | calibratable lookback (e.g. 7, 30) |
| `SYMBOL_HOTLOAD_PROVIDER` | `schwab` | provider REST to pull from |

The add endpoint accepts optional overrides
(`hotload`, `hotload_days`, `hotload_provider`) so a single add can
differ from the deployment default. `hotload_enabled=false` is the
"just start streaming from now into the future" mode — clean, no
special path.

Provider is pluggable via the existing `app/providers/` abstraction
(`provider.get_history(symbol, start, end)`); default Schwab
`pricehistory`. This is the same modularity theme as the read layer's
`SourceSpec` registry.

### 3.2 Deep history (the lake tier)

Unchanged from today: read `polygon_adjusted ∪ schwab_universe`
(canonical adjusted deep history) into CH via `lake_to_ch_backfill`, or
serve it lake-direct at read time via `read_arrow`. Runs in parallel
with hotload.

### 3.3 Gap-fill (residual repair)

After the union, detect and repair holes the lake doesn't cover.

- **Detect on the UNION, not "polygon-end → now".** `schwab_universe`
  already carries a rolling ~48-day window, so the union usually covers
  most of the tip. Diff the union against the **expected ET trading-
  minute grid** for `[start, end)` (04:00-20:00 ET, skipping nights /
  weekends / holidays / halts). Naive "last bar → now" diffing treats
  every weekend as a gap and hammers the provider for data that does
  not exist.
- **Fill only the residual** from the selected provider REST.
- **Persist to the lake, then sync CH.** Write fills to
  `schwab_universe` (durable, reproducible — ML/backtest read the lake
  CH-independently), then let the normal lake→CH sync bring them hot.
  CH-only fills are lost on a cache wipe and leave the lake gappy.

**Config:**

| Setting (env) | Default | Meaning |
|---|---|---|
| `SYMBOL_GAPFILL_ENABLED` | `true` | repair residual gaps after union |
| `SYMBOL_GAPFILL_PROVIDER` | `schwab` | provider REST for the fill |

## 4. Cross-cutting guardrails

1. **Clamp to the provider's minute entitlement, loudly.** Schwab
   minute `pricehistory` is entitled for a rolling window (~48 days).
   7/30 are safe; a larger `hotload_days` (or a deep gap) cannot be
   served at minute granularity — validate and **log/raise explicitly**,
   never silently return a truncated window (no-silent-failures).
2. **Idempotent + single-flight.** Re-adding a symbol or a user
   refreshing the chart must not double-write bars or enqueue duplicate
   fills. Dedup on `(symbol, timestamp)`; one in-flight job per
   `(symbol, stage, window)`.
3. **Precedence at the seam.** Provider tip/gap fills are a *temporary
   patch superseded by canonical history*: `polygon_adjusted`
   (precedence 2) > provider fill (1) — the same rule already in the
   `SourceSpec` registry. Watch the adjustment boundary: Schwab is
   pre-adjusted (`adj_factor=1.0`), polygon carries the real factor; a
   split inside the fill window can leave a one-tick discontinuity until
   the weekly Spark run re-adjusts. Acceptable as a transient; document
   it.
4. **Non-blocking + a loading signal.** The chart response returns what
   CH has now and carries a "history loading" flag (or pushes via WS)
   so the UI refreshes when a stage lands, instead of showing a silent
   empty chart.

## 5. What already exists vs new

| Piece | Status |
|---|---|
| Live WS subscribe on add | exists (`stream/service.py`) |
| Warmup: Schwab tip-fill + lake→CH backfill, background | exists (`_enqueue_warmup`, `schwab_tip_fill`, `lake_to_ch_backfill`) |
| Bars gateway CH-first + `schedule_gap_fill` (CH cache fill from lake) | exists (`bars_gateway.py`) |
| **Hotload config** (enabled/days/provider, global + per-add) | **new** (thin config over the existing tip-fill) |
| **Residual gap detection on the union (calendar-aware)** | **new** |
| **Provider-REST fill of residual → lake → CH** | **new** |
| **First-paint "history loading" signal** | **new** |

Mostly a config-ification of the warmup + one new stage (residual
gap-fill) on the `union_arrow` output. Low new surface, high value.

## 6. Migration (each step shippable)

1. Lift hotload config into settings (`SYMBOL_HOTLOAD_*`) + per-add
   override; route the existing tip-fill through it. `enabled=false`
   gives stream-only. No new fetch code — just make the window/provider/
   on-off tunable.
2. Add the calendar-aware gap detector (pure function over the union
   Arrow + the ET trading grid). Unit-testable offline.
3. Add the residual provider-REST fill → lake (`schwab_universe`) →
   CH sync, behind `SYMBOL_GAPFILL_ENABLED`. Idempotent + single-flight.
4. Add the "history loading" signal to the bars response / a WS push.

## 7. Risks & mitigations

- **Provider rate limits / thundering herd.** Mitigate: single-flight
  per (symbol, stage, window); clamp `hotload_days`.
- **Phantom gaps (halts/holidays).** Mitigate: detector measures
  against the ET trading-grid, not wall-clock.
- **Adjustment discontinuity at the fill/canonical seam.** Mitigate:
  precedence (polygon wins); transient until the weekly Spark run.
- **Durability.** Mitigate: fills land in the lake, not CH-only.

## 8. Open questions (for signoff)

1. **Per-add override surface:** add the three hotload params to the
   watchlist/seed add endpoints, or settings-only for v1?
2. **Gap-fill trigger:** on add only, or also on the read path (the
   bars gateway notices a hole and enqueues a fill)?
3. **Loading signal mechanism:** a field on the bars response (client
   polls) vs a WS push when a stage completes?
4. **Default `hotload_days`:** 30 (more context) vs 7 (cheaper, faster)
   as the shipped default?
