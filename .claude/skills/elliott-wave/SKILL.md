---
name: elliott-wave
description: Elliott Wave structural analysis for equities & futures — the inviolable rules, Fibonacci wave relationships, and StockAlert's deterministic no-look-ahead doctrine. Use when labeling wave counts, reasoning about a symbol's wave position/invalidation, or building/using the EW detector pipeline (signals/elliott, WaveReader, get_wave_state).
---

# Elliott Wave Analysis

Structural model of price: trends unfold in **5 impulse waves (1-2-3-4-5)**,
corrections in **3 waves (A-B-C)**. The pattern is **fractal** — each wave
decomposes into the same structure one degree lower. Wave relationships
track **Fibonacci ratios** (.236 .382 .50 .618 .786 1.0 1.272 1.618 2.618).

Full build plan & rationale: [docs/elliott_wave_plan.md](../../../docs/elliott_wave_plan.md).

## The three inviolable rules (a count breaking any is invalid)

1. **Wave 2 never retraces more than 100% of wave 1.** (Else it wasn't a wave 1.)
2. **Wave 3 is never the shortest of waves 1/3/5.** Usually the longest.
3. **Wave 4 never enters wave 1's price territory** (except diagonals).

## Guidelines (soft — score, don't gate)

- **Alternation**: sharp wave 2 ⇒ sideways wave 4 (and vice versa).
- **Wave 3 extension**: commonly 1.618× wave 1.
- **Wave 5 equality**: commonly = wave 1, or 0.618×(wave 1→3).
- **Wave 2 retrace**: typically .50–.786 of wave 1.
- **Wave 4 retrace**: typically .236–.382 of wave 3.

## StockAlert doctrine — this is what makes us different

EW is famously subjective. We do **not** try to be "philosophically correct"
about counts. We want **consistent, falsifiable, forward-predictive** state.

- **No look-ahead — the prime invariant.** A label at bar `t` uses ONLY bars
  `≤ t`. A pivot at bar `i` is confirmed only at `i + k`; never use a pivot
  whose confirmation is in the future of the as-of bar. Labelings that
  retroactively change when new data arrives are the #1 source of fake EW
  backtest alpha. Pinned by `tests/test_elliott_no_lookahead.py`.
- **No ego.** Ship labels that improve forward-return prediction even if
  textbook-wrong; kill textbook-correct ones that don't predict.
- **No top-1 dogma.** Multiple valid counts coexist. Expose **top-K**
  (primary + alternates), each with a `confidence` and an
  `invalidation_price`. Treat confidence < 0.5 as "no clear count," not a
  forced label.
- **Determinism.** Same input bars ⇒ byte-identical output. Sort tied
  candidates by a stable tuple key (pivot timestamps), never by float score.
- **Append-only labels.** Every `WaveLabeling` carries `engine_ver + git_sha`.
  Old labels stay; engine changes bump the version. Reproducibility is a
  contract, not a nicety.

## How EW pays off for our AI/ML tracks

- Compresses dozens of bars into one **discrete state** ("in wave 3 of
  impulse") — RL thrives on discrete state, LLMs reason over named structures.
- Every count yields an **explicit invalidation level** = natural stop + a
  falsifiable hypothesis.
- **Multi-timeframe-native**: a daily wave-3 IS the hourly impulse. Exploits
  the existing MTF `Context`.

## Where it lives in the system (as built out)

| Layer | Module | Role |
|---|---|---|
| Pivots | `app/indicators/pivots.py` | Multi-fractal (k∈{3,5,8,13,21}) pivot detector, registry-registered |
| Engine | `app/signals/elliott/` | Pure rule engine → `WaveLabeling`; no I/O imports |
| Exposure | `app/services/readers/` + `WaveReader` | `get_state(symbol, interval, as_of=)` |
| HTTP | `app/api/routes_wave.py` | `GET /api/wave/{symbol}?interval=1d` |
| Agent | `app/mcp/tools/wave.py` | `get_wave_state` / `evaluate_wave_targets` MCP tools |
| Screener | `app/services/screener/` | rule kinds: `in_wave`, `at_fib_retracement`, `wave_invalidated_recently`, `impulse_complete` |
| Store | `equities.elliott_wave_labels` (v2 lake) | pre-computed history for training tracks |

When reasoning about a live symbol, prefer reading `get_wave_state` over
re-deriving counts by hand — it enforces the no-look-ahead and confidence
rules above. When those tools don't exist yet, say so rather than inventing
a count.
