# Elliott Wave Engine v3 — Pro-Grade Spec

**Status:** PROPOSED — the north star for making the engine genuinely useful to
a professional Elliott Wave practitioner. Derived from a real ElliottWaveTrader
(Avi Gilburt) /GC count used as a benchmark. **Every rule here generalizes to
any symbol or future — NO instrument-specific logic, ever.**

## Why v3

v2 (shipped) is causal, deterministic, honest, and — validated against a pro
count on /GC — **already agrees on the core structure** (impulse down, wave 4,
the 4,046 pivot, the ~4,400 invalidation). But a pro would not yet *use* it,
because it is missing the things that turn a label into an analysis:

1. **Nesting** — one coherent count across degrees, where each wave subdivides
   correctly into the next degree down. v2 labels each timeframe independently;
   they can disagree (1d "wave 3" vs 1h "complete"). This is the #1 gap.
2. **The forward plan** — a pro's output is "you are HERE, next target is THERE,
   invalidated at THIS line." v2 reports the current wave but, when in wave 4,
   gives wave-4 *retrace* levels — not the actionable **wave-5 projection** of
   where price goes after the bounce.
3. **Confluence + zones** — pros target *zones* backed by multiple converging
   Fibonacci ratios, not single prices.
4. **The full structure catalog** — diagonals, flats, triangles, truncation,
   and the larger corrective hierarchy (the whole /GC move is wave (c) of a
   January A-B-C). v2 knows only impulse + zigzag.
5. **Guideline evidence** — alternation, depth, channeling, and wave
   personality (volume/momentum divergence) as confidence inputs.

## Requirements (generalized from the benchmark's §11 audit checklist)

- **R1 — Nested coherent count.** Produce a wave TREE, not a flat label. A
  motive wave at degree D subdivides into 5 waves at D-1; a corrective into 3.
  A count is valid only if its subdivisions validate. Degree consistency is
  enforced: all sibling waves carry the same degree (no mixing).
- **R2 — Subdivision validation.** Before accepting "wave 3 of an impulse,"
  verify the move labeled wave 3 *itself* subdivides into 5 at the degree below.
  This is what kills the noisy 1-bar "wave 2" v2 sometimes picks.
- **R3 — Forward projection.** For the wave currently in progress, project the
  target of the wave you are moving INTO (in wave 4 → project wave 5; in wave 2
  → project wave 3), as a **confluence zone**, plus the structural invalidation.
- **R4 — Fibonacci confluence.** Targets/retraces are zones where ≥2 ratios
  cluster (e.g. 50% retrace of A + 1.0× of B). Confluence count feeds confidence.
- **R5 — Labeled alternates with hard gates.** Surface the top counts (primary +
  alternate(s)), EACH with a binary invalidation price. A count flips to the
  alternate exactly when its gate breaks (the benchmark's 4,402 "purple" line).
- **R6 — Guideline scoring.** Score alternation (sharp 2 ⇒ sideways 4), depth
  (2: .5–.62, 4: .38–.78), equality, channeling, and **personality** (wave 3
  strongest internals; wave 4/2 divergence via volume + an oscillator). These
  are soft evidence — they rank counts, they don't gate them.
- **R7 — Structure catalog.** impulse, leading/ending diagonal, zigzag, flat,
  triangle, and simple combinations; plus **truncation** (wave 5 fails to exceed
  wave 3 — valid, flagged, not rejected).
- **R8 — Invariants preserved.** No look-ahead, deterministic, reproducible,
  pure package — unchanged from v2. Nesting must not leak future bars.

## Architecture sketch

- **Pivot hierarchy** (have: `detect_multidegree`). Treat the fractal degrees as
  a ladder; coarse degrees frame structure, fine degrees supply subdivisions.
- **Wave tree** (`WaveNode`): `{degree, structure, direction, label, start, end,
  children[], rules_passed, fib_score, personality_score}`. A `WaveLabeling`
  becomes the root node + its validated subtree, not a flat pivot list.
- **Top-down with bottom-up validation**: hypothesize the high-degree structure
  from coarse pivots, then for each wave attempt to label its subdivision from
  the next finer degree's pivots within that wave's [start,end] window; accept
  the parent only if enough children validate (motive→5, corrective→3).
- **Forward engine**: given the open wave, compute the next wave's anchored
  confluence zone + the count's invalidation. This is the trader-facing output.
- **Scenario output**: primary + alternate(s), each `{count, probability,
  invalidation, next-target-zone, what-confirms, what-invalidates}` — the
  "map" a practitioner actually consumes.

## Output contract upgrade

`WaveStateResponse`/`WaveLabeling` gain: a nested `tree`, a `forward` block
(`next_wave`, `target_zone {low, high, basis[]}`, `invalidation`), per-count
`scenario` text, and confluence-backed zones instead of point targets. The
existing flat fields stay for back-compat.

## Benchmark & validation (NOT instrument-specific)

The /GC Jan–Jun 2026 daily series ([gc-wave-analysis.md](benchmarks/gc-wave-analysis.md))
becomes a **regression fixture** — a fixed OHLCV input with expected STRUCTURAL
features, asserted generically:

- identifies a down impulse with its wave-3 low at the 4,046 bar (±tolerance);
- reports the current state as "in wave 4" (counter-trend bounce);
- projects a wave-5 target **below** the wave-3 low (the actionable forward move);
- flags an invalidation near the wave-1 territory (~4,400).

These are asserted as *relationships* ("wave-3 low is the lowest pivot before
the current bounce", "forward target < wave-3 low"), never as hardcoded gold
prices. The same assertions hold for any down-impulse-in-wave-4 on any symbol.

## Phasing

| Phase | Deliverable |
|---|---|
| **V3-1** | Wave tree + subdivision validation (R1, R2) — the keystone; fixes noisy pivots + gives the larger context |
| **V3-2** | Forward projection + confluence zones (R3, R4) — the trader-facing payoff |
| **V3-3** | Labeled alternates with hard gates + scenario output (R5) |
| **V3-4** | Guideline + personality scoring (R6) |
| **V3-5** | Structure catalog: diagonals, flats, triangles, truncation (R7) |
| **V3-6** | Cross-timeframe coherence (the nested tree spans 1d↔1h↔15m↔5m) |

Each phase keeps the v2 invariants (R8) and is gated by the /GC benchmark plus
synthetic unit tests. **The product wedge — vs every retail EW tool — stays:
honest, deterministic, auditable, no hindsight relabeling.** v3 adds the
practitioner-grade depth on top of that honesty.

## Hard constraint

No symbol-specific or futures-specific branch in the engine. If a rule only
works for gold, it does not go in. The benchmark validates generality, it does
not specialize the code.
