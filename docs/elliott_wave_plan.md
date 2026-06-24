# Elliott Wave — Investigation & Build Plan

How we integrate Elliott Wave (EW) structural analysis into the
StockAlert platform — as deterministic detectors agents can read,
as screener rules, as trading strategies, and as inputs for the
RL/LLM agent training tracks.

**Status:** plan only. No code written yet. Phasing below; checkpoint
each phase against the journal before advancing.

**Companion docs:**
- [trading_subsystem_design.md](trading_subsystem_design.md) — strategy
  framework + Context + Phasing table this plan will feed.
- [indicator_exposure_design.md](indicator_exposure_design.md) — how
  indicator-shaped data is served to dashboards, MCP, training.
- [architecture_v2/](architecture_v2/README.md) — the v2 `equities.*`
  Iceberg lake. Labeled wave history will need a new feature surface
  (the v1 medallion's gold tier was retired with CV14).
- [`app/signals/README.md`](../app/signals/README.md) — folder this
  work expands; divergence is the closest existing precedent.

---

## 1. What Elliott Wave is (and isn't) for a machine

Elliott Wave Theory (R.N. Elliott, 1930s; refined by Frost & Prechter,
1978) is a structural description of price movement:

- Trending moves unfold in **5 impulse waves** (labeled 1, 2, 3, 4, 5)
- Counter-trend corrections unfold in **3 corrective waves** (A, B, C)
- The pattern is **fractal** — every wave of a higher degree decomposes
  into a 5-wave or 3-wave structure at the next lower degree
- Wave relationships frequently track **Fibonacci ratios**
  (.236, .382, .50, .618, .786, 1.0, 1.272, 1.618, 2.618)

### Elliott's three hard rules

These are inviolable; a labeling that breaks any of them is invalid:

1. **Wave 2 never retraces more than 100% of wave 1.** (If it does,
   you weren't in a wave 1.)
2. **Wave 3 is never the shortest of the three impulse waves (1, 3, 5).**
   It's most often the longest.
3. **Wave 4 never enters the price territory of wave 1** (except in
   diagonal patterns).

### Elliott's guidelines (soft tendencies)

- **Alternation** — if wave 2 is a sharp correction, wave 4 tends to
  be sideways, and vice versa.
- **Wave 3 extension** — wave 3 commonly hits 1.618× the length of
  wave 1.
- **Wave 5 equality** — wave 5 commonly equals wave 1 (or 0.618× the
  length of wave 1 through 3).
- **Fibonacci retracements for wave 2** — typically 50%–78.6% of
  wave 1.
- **Fibonacci retracements for wave 4** — typically 23.6%–38.2% of
  wave 3.

### Why this is useful even though humans disagree about counts

EW is famously subjective — two analysts can label the same chart
differently. **That's a feature, not a bug, for our setup.** A machine
doesn't need to be philosophically correct; it needs to be
*consistent*. If a deterministic labeler agrees with itself across
runs, and the labels correlate with forward returns, the system
works. We let the data settle the "what's a correct wave count"
debate rather than asking a human.

The other reason EW pays off for AI/ML specifically:

- It compresses **dozens of bars of context into a single discrete
  state** (e.g. "in wave 3 of an impulse"). RL agents thrive on
  discrete state; LLMs are good at reasoning over named structures.
- It produces **explicit invalidation levels** — Elliott's rules
  tell you exactly when a count is wrong (price piercing a specific
  level). That's a natural stop-loss and a falsifiable hypothesis.
- It's **multi-timeframe-native** — the fractal property means a
  wave-3 on the daily IS the impulse pattern on the hourly. We
  already have a multi-timeframe Context (TA-4.1); EW exploits it.

### What we are NOT trying to do

- **No ego.** We are not trying to "be right" about wave counts.
  We are looking for state features that improve forward-return
  prediction. If the labels are wrong but predictive, ship them. If
  they're textbook-correct but unpredictive, kill them.
- **No retrospective relabeling.** Every wave count produced by the
  system at bar `t` uses ONLY bars `≤ t`. The "no look-ahead"
  invariant we enforce for indicators applies doubly here — EW
  labelings that retrospectively change when new data arrives are
  the single biggest source of fake backtest alpha in the wild.
- **No top-1 dogma.** Multiple valid wave counts often coexist
  ("primary" + "alternate" counts). We expose the top-K most likely
  and let downstream consumers compose.

---

## 2. Algorithmic approaches we considered

| Approach | What it does | Verdict |
|---|---|---|
| **A. ZigZag + threshold** | Detect % swings, label every 5 swings as 1-2-3-4-5 | Too crude — every pullback gets labeled, ignores Elliott's rules |
| **B. Pivot-fractal + rule engine** | Detect pivots at multiple fractal lengths; enumerate candidate 5-wave sequences; filter by Elliott's hard rules; score by Fibonacci-fit | **Primary track.** Deterministic, testable, explainable. |
| **C. DTW against canonical templates** | Dynamic time warping price segments against hand-crafted 5-wave shapes | Useful as a secondary signal / confidence boost |
| **D. CNN / Transformer on price tensors** | Train a deep classifier on (window, label) pairs | Need labeled data first → bootstrap from approach B |
| **E. RL with EW state features** | Standard PPO; state vector includes the rule-engine's wave label + confidence | The training-tracks payoff; depends on A→D |
| **F. LLM with EW as Context input** | LLM strategy gets `{ current_wave: 3, confidence: 0.72, invalidation: 184.50 }` per bar | Cheapest first agent integration |

**We will land B + C + F first** (rule engine + DTW confidence + LLM
integration), then bootstrap D from B's labels, then plug into the
RL track once it lands (TA-RL).

### Reference implementations & literature

External references (read before implementing the rule engine):

- **Frost & Prechter, *Elliott Wave Principle* (1978).** Canonical.
  The hard rules + guideline catalog come from here.
- **Magazinik et al., *Neural-network–based identification of Elliott
  Wave patterns* (2019, J. Computational Finance).** Architecture
  ideas for approach D.
- **Bill Williams' Fractals indicator** (5-bar high/low) — the
  building block of approach B. Already partly implemented in
  [`app/signals/divergence.py::find_pivot_highs`](../app/signals/divergence.py).
- **TA-Lib's CDLFRACTAL pattern.** Reference impl for pivot detection.
- **`ewavesm` (Python), `elliotwave` (R).** Open-source rule-engine
  precedents — useful for cross-validation, not for vendoring (both
  have heavy dependencies + GPL-ish licenses).

We will not vendor any external EW library; both available options
do too much (force a labeling philosophy) and are hard to test
deterministically. We write our own pivot + rule engine to match
the deterministic / lift-out-friendly contract the rest of the
platform follows.

---

## 3. Where EW slots into the existing architecture

```
                                                        ┌─ Screener rules ──────┐
                                                        │  in_wave_3            │
   bars ─► indicators/ ─► pivots ─► wave-engine ─►  ─►─┤  at_wave2_pullback    │
                          (new)    (new)               │  impulse_invalidated  │
                                                        └───────────────────────┘
                                                                  │
                                                                  ▼
                                                        ┌─ Strategies ─────────┐
                                                        │  WaveTwoPullback     │
                                                        │  WaveFiveExit        │
                                                        │  LLM (via Context)   │
                                                        └──────────────────────┘
                                                                  │
                                                                  ▼
                                                        ┌─ Training tracks ────┐
                                                        │  RL state features    │
                                                        │  LLM prompt context   │
                                                        │  CNN classifier       │
                                                        └───────────────────────┘
```

### 3.1 New folders & files (proposed)

```
app/
├── indicators/
│   └── pivots.py                # NEW — multi-fractal pivot detector
├── signals/
│   └── elliott/
│       ├── __init__.py
│       ├── README.md
│       ├── schemas.py           # Pivot, WaveCandidate, WaveLabeling Pydantic
│       ├── rules.py             # Elliott's hard rules (pure functions)
│       ├── fib.py               # Fibonacci ratio scoring + retracement levels
│       ├── engine.py            # WaveEngine.label(pivots) -> list[WaveLabeling]
│       └── dtw.py               # DTW against canonical 5-wave templates (Phase EW-2.5)
└── services/
    └── wave_reader/
        ├── __init__.py
        ├── README.md
        ├── schemas.py           # WaveState, WaveStateResponse
        └── wave_reader.py       # WaveReader.get_state(symbol, interval)
```

### 3.2 Integration points

| Existing module | EW-side change | Why |
|---|---|---|
| [`app/services/sim/context.py`](../app/services/sim/context.py) | Add `ctx.wave_state(interval='1d')` method | Strategies & LLM agents query wave state per bar |
| [`app/services/screener/schemas.py`](../app/services/screener/schemas.py) | New `RuleKind` literals: `in_wave_n`, `at_fib_retracement`, `wave_invalidated` | Universe filter by wave position |
| [`app/services/screener/rules.py`](../app/services/screener/rules.py) | Evaluator functions for new rule kinds | Screener calls `WaveReader.get_state(...)` |
| [`app/services/readers/`](../app/services/readers/) | Add `WaveReader` alongside `IndicatorReader` | Shared by HTTP route + MCP tool |
| [`app/api/routes_indicators.py`](../app/api/routes_indicators.py) | Mirror `routes_wave.py` | `GET /api/wave/{symbol}?interval=1d` |
| [`app/mcp/tools/`](../app/mcp/tools/) | New `wave.py` tool module → `get_wave_state` MCP tool | LLM agents can see wave state |
| [`docs/architecture_v2/`](architecture_v2/README.md) | Add an `equities.elliott_wave_labels` (or sibling feature table) — v2 has no separate gold layer | Pre-computed historical labels for training |

### 3.3 The core types (sketch)

```python
# app/signals/elliott/schemas.py

class Pivot(BaseModel):
    timestamp: datetime
    price: float
    kind: Literal["high", "low"]
    fractal_length: int          # the `k` used to detect this pivot
    degree: int                  # 0=minute, 1=minor, 2=intermediate, ...

class WaveCandidate(BaseModel):
    """One candidate labeling of 5 consecutive pivots as a 1-2-3-4-5
    impulse, or 3 pivots as an A-B-C correction."""
    structure: Literal["impulse", "correction", "diagonal"]
    pivots: list[Pivot]
    rules_passed: dict[str, bool]    # 'rule_1_w2_no_overcount': True, ...
    fib_score: float                 # 0..1 — how well ratios match guidelines
    confidence: float                # composite score
    invalidation_price: float        # the level where this count is wrong

class WaveLabeling(BaseModel):
    """The full picture for one symbol at one timestamp."""
    symbol: str
    interval: str
    as_of: datetime
    primary: WaveCandidate
    alternates: list[WaveCandidate]  # top-K-1 alternates
    current_wave: Optional[int]      # which wave we're IN right now (1..5 or A,B,C)
    confidence: float                # of primary
```

The `WaveLabeling` object is what the LLM gets in its Context, what
the screener filters on, and what the RL state vector encodes.

---

## 4. Phasing

Follows the same `Phase EW-N` cadence as the trading subsystem
phases (TA-N) but in a parallel namespace so we can interleave with
the silver/gold work.

### Phase EW-1: Pivot detection foundation (3–4 days)

- Promote `find_pivot_highs`/`find_pivot_lows` from `signals/divergence.py`
  into `app/indicators/pivots.py` as a proper Indicator class:
  - `PivotDetector(period: int)` returning a Series of `+1` (high),
    `-1` (low), `0` (no pivot).
- Add multi-degree support: compute pivots at fractal lengths
  `k ∈ {3, 5, 8, 13, 21}` (Fibonacci). Each fractal length defines
  a **degree** of wave (subminuette → minor → intermediate → ...).
- Register with `INDICATOR_REGISTRY` so `Context.indicator("pivots", period=8)`
  works inside strategies.
- Tests: hand-crafted price series with known pivots; warmup behavior
  (no pivots in the first/last `k` bars); fractal-degree ordering
  (higher `k` produces subset of lower `k`'s pivots).
- README + journal entry.

**Gate:** `Context.indicator("pivots", period=5)` returns the
expected pivots on a 100-bar synthetic series; no regressions in
`divergence.py` (its inline pivot helpers become thin wrappers).

### Phase EW-2: Rule engine (5–7 days)

- `app/signals/elliott/` package per §3.1.
- `engine.WaveEngine.label(pivots) -> list[WaveLabeling]`:
  - Enumerate candidate 5-pivot windows ending at the latest pivot.
  - Apply the **three hard rules** (`rules.py`) — discard violators.
  - Score remaining candidates by Fibonacci-ratio fit (`fib.py`):
    wave 3 = 1.618×w1? Bonus. Wave 2 retrace ∈ [.382, .786]? Bonus.
    Etc.
  - Return top-K (default K=3) `WaveLabeling`s, primary + alternates.
- Walk-forward correctness: at any bar `t`, only pivots with
  `pivot.timestamp + look_ahead <= t` are visible. Pinned by
  `app/signals/elliott/tests/test_elliott_no_lookahead.py::test_label_does_not_change_when_future_data_added`.
- Pure module — no I/O, no `app.db`, no `app.providers` imports.
  Enforced by the same AST-walk gate that polices `app/services/sim/strategies/`.
- Tests:
  - Synthetic 5-wave price paths → labeled correctly with confidence > 0.8.
  - Synthetic ABC correction → labeled correctly.
  - Rule-violation cases → primary count has the violation rule flag
    set to `False` and a lower confidence.
  - **Determinism:** same input → byte-identical output (sort
    candidates by tuple key, not by float score timestamp order).
- README + journal entry.

**Gate:** Run the engine over 5 years of AAPL daily data via a CLI
script; produce a `gold.elliott_wave_labels` parquet (one row per
bar with `(symbol, ts, current_wave, confidence, invalidation_price,
alternates_json)`). Spot-check 20 random labels against the price
chart by eye — primary count should look "plausible" on at least
~70% (rough sanity, not a hard gate).

### Phase EW-2.5: DTW confidence boost (2 days, optional)

- `dtw.py` — dynamic time warping of the labeled price segment
  against 5 hand-crafted canonical templates (impulse-bull,
  impulse-bear, zigzag-correction, flat-correction, triangle-correction).
- DTW distance becomes a confidence multiplier on the rule-engine
  output.
- Tests verify DTW distances against known synthetic curves.

Skip this phase if EW-2 gate produces "good enough" labels alone.
We re-evaluate after EW-2 ships.

### Phase EW-3: Exposure layer (3 days)

- `WaveReader` per §3.1 — same pattern as `IndicatorReader`:
  - `WaveReader.get_state(symbol, interval, *, as_of=None) -> WaveLabeling`
  - Reads bars from `BarReader`/`BronzeReader` per interval (same
    routing as the screener), runs `PivotDetector` + `WaveEngine`,
    returns the typed Pydantic response.
- `GET /api/wave/{symbol}?interval=1d` HTTP route.
- `get_wave_state` MCP tool.
- Cache `WaveLabeling` per `(symbol, interval, latest_bar_ts)` to
  avoid recomputation. Same TTL pattern as `IndicatorReader`.
- Tests for both surfaces.

**Gate:** `curl /api/wave/AAPL?interval=1d` returns a `WaveLabeling`
with at least primary + 1 alternate. MCP `list_tools` includes
`get_wave_state`.

### Phase EW-4: Screener integration (1 day)

Add 4 new `RuleKind`s to the screener:

| Kind | Params | Semantics |
|---|---|---|
| `in_wave` | `{wave: int, min_confidence: float}` | Symbol is currently in the given wave with ≥ confidence |
| `at_fib_retracement` | `{level: float, tolerance: float}` | Latest price is within `tolerance` of the given Fib level of the current wave |
| `wave_invalidated_recently` | `{lookback_bars: int}` | Primary count flipped within the last N bars (a regime change signal) |
| `impulse_complete` | `{}` | The current count just finished wave 5 (potential top) |

One evaluator per rule in `screener/rules.py`. Pattern matches the
existing 13 rules.

### Phase EW-5: Wave-aware baseline strategies (3–4 days)

Two rule-based strategies that test whether wave structure provides
real edge:

1. **`WaveTwoPullbackStrategy`** —
   - Universe: filtered by screener (`in_wave: 2, min_confidence: 0.6`)
   - Entry: at the close of a bar where price retraces 50–78% of
     wave 1 AND price is rising again
   - Exit: at wave 3 = 1.618×wave 1 (Fib extension target) OR
     invalidation (price pierces wave 1 origin)
   - Stop: invalidation price from `WaveLabeling`

2. **`WaveFiveExitStrategy`** —
   - Universe: any holdings flagged as `current_wave == 5`
   - Action: exit on completion (wave 5 = wave 1 length OR DTW
     score for "impulse complete" exceeds threshold)
   - This is a *companion* strategy, not standalone — it's a
     better-exit overlay for existing trend-following strategies.

Bake-off vs the existing baselines (`sma_crossover`, `ema_crossover`,
`rsi_reversion`, `bollinger_mean_revert`, `mtf_ema_trend_filtered`)
on the same AAPL 2023-2024 window. If Sharpe and trade count both
beat `ema_crossover` (current best baseline: Sharpe 0.933), promote
the wave-aware strategies. If not, keep them as agent-training inputs
only.

### Phase EW-6: Gold-tier wave-label store (2–3 days, contingent on silver)

Once the silver layer lands (the immediate next gap in the data
platform), build `gold.elliott_wave_labels`:

```
gold.elliott_wave_labels
─────────────────────────
symbol        STRING        partition
date          DATE          partition
ts_minute     TIMESTAMP
interval      STRING        (1m | 5m | 15m | 1h | 1d)
current_wave  INT           (1..5 or 1..3 for ABC; null if no count)
degree        INT           (fractal degree)
confidence    FLOAT         (primary)
invalidation  FLOAT         (price level)
alternates_n  INT           (count of alternate labelings)
alternates    STRING        (JSON of top-K-1 alternates)
fib_score     FLOAT         (Fibonacci-fit subscore)
rule_score    FLOAT         (hard-rule subscore)
engine_ver    STRING        (label engine version — required for reproducibility)
git_sha       STRING        (label engine git SHA at compute time)
```

- Daily build job: read `silver.ohlcv_1m`, materialize per (symbol,
  interval) wave labels for the full history, append to gold.
- Snapshot-pinned: every `WaveLabeling` carries `engine_ver +
  git_sha`. Reproducibility contract from
  [trading_subsystem_design.md §7](trading_subsystem_design.md)
  applies — re-running the same engine version on the same silver
  snapshot must produce identical labels.
- Backfill once for all of silver history; incremental from then on.

This is what feeds the training tracks (EW-7, EW-8).

### Phase EW-7: LLM agent integration (1–2 days)

- LLM strategy's per-bar `Context` block extended with the current
  `WaveLabeling`:

  ```
  ELLIOTT WAVE STATE
    Primary count: in wave 3 of impulse (degree=minor, conf=0.74)
    Invalidation: 184.50 (wave 1 high)
    Fib target: 198.20 (1.618× wave 1)
    Alternates:
      - in wave A of correction (conf=0.21)
      - impulse complete (conf=0.05)
  ```

- New MCP tool: `evaluate_wave_targets(symbol, interval)` — returns
  current Fib targets and invalidation, lets the agent reason
  numerically.
- Re-run the LLM smoke test (45 trading days AAPL) with EW state
  vs without. Same prompt, same model, same window. Measure:
  - Did trade frequency change?
  - Did Sharpe change?
  - Did the agent reference EW state in its reasoning text?

The third question is the most informative — if the agent ignores
the EW state in its rationale, the state isn't being usefully
conditioned on, regardless of metrics.

### Phase EW-8: RL agent integration (depends on TA-RL landing)

- RL state vector gets an additional block of features encoding the
  primary wave labeling: one-hot `current_wave`, scalar `confidence`,
  signed-distance-to-invalidation, scalar `fib_score`.
- A/B: train two PPO agents on the same `(silver, gold.features_*)`
  data, one with EW features and one without. Compare on Sharpe +
  max drawdown over a held-out window.
- If EW features improve metrics, promote. If not, retain the EW
  state for the LLM track and keep it out of RL.

### Phase EW-9 (stretch): CNN/Transformer wave classifier

- Train a sequence classifier on `(price-window-256-bars, wave_label)`
  pairs sourced from `gold.elliott_wave_labels`.
- Output: per-bar probability distribution over wave labels.
- Use case: replace the rule-engine output as the source of `WaveLabeling`
  on intervals where it produces better forward-return correlation.
- Only if EW-7 or EW-8 demonstrates EW features have real edge.

---

## 5. Reproducibility & no-look-ahead contract

The same invariants we enforce for indicators apply more strictly here:

1. **Pivot detection is causal.** Pivot at bar `i` is only "known"
   from bar `i + k` onward (you need `k` future bars to confirm the
   pivot). The `WaveEngine` must never use a pivot whose
   confirmation timestamp is in the future of the as-of bar.

2. **WaveLabelings are append-only.** The label at bar `t` is what
   the engine produced with bars `≤ t`. Storing this in `gold.elliott_wave_labels`
   means each row is *historical truth as known at the time of that
   row* — not the latest revisionist count. The DB schema's
   `(symbol, ts_minute, engine_ver, git_sha)` is the primary key.

3. **Engine versioning is mandatory.** Every change to `WaveEngine`
   bumps `engine_ver`. Old labels stay. New labels are computed for
   forward-only or by explicit re-backfill. Backtests must pin the
   engine version they were trained against.

4. **Test gate.** `app/signals/elliott/tests/test_elliott_no_lookahead.py` runs the
   engine on bars `1..N`, then again on bars `1..N+10`, and asserts
   that the labelings for bars `1..N` are byte-identical between
   the two runs.

If a labeling produced at bar `t` mutates when more data arrives,
the system is leaking future information. This is the #1 way EW
backtests fake alpha in the wild — we will not.

---

## 6. Risks & open questions

### Subjectivity → over-fitting

EW gives the engine many degrees of freedom (which pivots to use,
which fractal degree, weight on each Fib level). Easy to tune the
scoring function to make in-sample backtests look brilliant.

**Mitigation:** all hyperparameter tuning runs walk-forward (rolling
train/validate split). The bake-off in EW-5 uses an out-of-sample
year. Hyperparameter choices land *before* the bake-off, not after.

### Counts flip frequently on choppy/sideways markets

A pivot that looked like the end of wave 2 can re-label as the start
of wave A of a correction if price keeps pulling back. The "primary
count" stream can be noisy.

**Mitigation:** expose `confidence` prominently; treat counts below
a threshold (default 0.5) as "no clear count" rather than forcing a
top-1. Strategies are written to require `min_confidence` per rule.

### Fractal degree assignment is non-canonical

There's no objective way to say "this pivot is at minor degree, not
intermediate." Different choices of fractal length `k` produce
different degree assignments.

**Mitigation:** we don't claim a canonical degree per pivot. The
engine reports `degree` as an integer derived purely from `k` (the
fractal length), not as the textbook "intermediate / minor" name.
Downstream consumers (strategies, agents) read the integer.

### Multi-symbol scaling

Computing all alternates for every symbol × every interval × every
bar is expensive. AAPL alone × 1m × 5 years × 5 fractal degrees =
~6.5M label rows.

**Mitigation:** EW-6's `gold.elliott_wave_labels` is daily-build,
not on-demand. The `WaveReader` is cache-first. Agents read
pre-computed labels; only the live bar's WaveState recomputes.

### "Just use a NN" temptation

It's tempting to skip the rule engine entirely and train a CNN on
hand-labeled charts. We are deliberately not doing that:

1. No agreed-upon labeled dataset exists (every EW author labels
   differently).
2. Pre-training a classifier from rule-engine labels (Phase EW-9) is
   strictly more powerful — we get a deterministic baseline first,
   then learn refinements from data.
3. A rule engine is debuggable; a CNN is not. When the agent makes
   a wave-count-based trade and loses money, we need to know why.

---

## 7. Backlog: what NOT to build initially

- **Multi-symbol pair-wise wave correlation** ("does AAPL's wave 3
  lead MSFT's wave 1?"). Interesting research, zero MVP value.
- **Custom Elliott patterns** (running flats, expanded flats, ending
  diagonals). Eight subtypes total. Start with the three structural
  forms (impulse, zigzag, flat); add subtypes only if EW-5's
  strategies generate signal.
- **Manual wave-count UI for the dashboard.** Visualizing the
  current primary count over a candlestick chart is genuinely
  useful, but it's a frontend concern — defer until the backend has
  delivered tradeable edge.
- **EW-on-the-fly inside the live monitor.** Live monitor runs
  every-tick or every-bar; computing wave state per tick is too
  expensive. Live runs every N seconds at most, reads from the
  `WaveReader` cache.

---

## 8. Open decisions (defer until we hit them)

1. **Storage format for alternates** — JSON column, separate
   `gold.elliott_wave_alternates` table, or stored as Iceberg-nested
   struct? Decide during EW-6.
2. **Engine determinism on tied scores** — when two candidate
   countings have identical confidence, sort by what? Decide during
   EW-2 (probably by tuple of pivot timestamps for stability).
3. **Confidence calibration** — raw rule-fit + Fib-fit scores
   produce numbers in [0, 1]-ish, but they're not calibrated
   probabilities. Decide whether to fit an isotonic regression
   against forward-N-bar wave persistence during EW-2 or punt.
4. **Whether to expose Wave-degree as a screener axis** — letting
   the screener filter by "in wave 3 of intermediate degree only"
   may be too fiddly. Likely defer; ship integer-degree filter
   first.

---

## 9. Where this fits in the overall roadmap

Insertion in [trading_subsystem_design.md §10 Phasing](trading_subsystem_design.md):

```
TA-4.1  Multi-TF foundation               LANDED 2026-05-17
TA-4.2  First MTF strategy                LANDED 2026-05-17
TA-4.3  Screener                          LANDED 2026-05-17
TA-5    Silver layer (next)               [data-platform Phase 3]
TA-6    TA coverage gap-fill              [ADX, OBV, VWAP, Donchian, …]
TA-7    Gold feature store                [data-platform Phase 5]
TA-8    Universe history                  [survivorship-bias fix]

EW-1   Pivot foundation                   (parallel with TA-6; light)
EW-2   Rule engine                        (parallel with TA-7; heavy)
EW-3   Exposure layer                     (after EW-2)
EW-4   Screener rules                     (after EW-3; 1 day)
EW-5   Baseline EW strategies             (parallel with TA-8)
EW-6   Gold-tier wave labels              (after silver = TA-5)
EW-7   LLM agent integration              (after TA-2-live unblocks)
EW-8   RL agent integration               (after TA-RL lands)
EW-9   Neural wave classifier (stretch)   (only if EW-7/EW-8 show edge)

TA-RL   RL agent + paper trading          [after data/TA dial-in]
TA-Live Paper → live                      [after kill-switch infra]
```

EW-1 through EW-5 deliver immediate operator value (a wave-aware
screener and two backtest strategies) and are independent of the
silver-layer work. EW-6 onward is the training-track payoff and
gates on the data-quality phases. **The full chain only matters if
EW-5's bake-off shows wave-aware strategies competing with the
existing baselines** — that's the go/no-go signal for the whole
training-side investment.
