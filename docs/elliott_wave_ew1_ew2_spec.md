# Elliott Wave — EW-1 + EW-2 Build Spec

**Status:** PROPOSED — awaiting sign-off. No code until approved.
**Scope:** Phase EW-1 (causal pivot foundation) + Phase EW-2 (rule engine).
**Parent:** [elliott_wave_plan.md](elliott_wave_plan.md) (strategy & full phasing).
**Doctrine:** [`.claude/skills/elliott-wave/SKILL.md`](../.claude/skills/elliott-wave/SKILL.md).

This refines the plan's EW-1/EW-2 into an approvable contract: exact types,
signatures, the named test list with assertions, and the go/no-go gates. It
also resolves (or explicitly defers) the open decisions those two phases hit.

---

## 0. Decisions requiring explicit sign-off

These change the contract. Flagged per the lean/explicit-signoff rule — none
are assumed.

| # | Decision | Recommendation | Why it matters |
|---|---|---|---|
| **D1** | **Pivot price source** | Pivot highs from `high`, pivot lows from `low` (wick extremes) — NOT `close`. | EW measures structure on extremes; close-only misses true wave tops/bottoms. Divergence keeps close (unchanged). `PivotDetector` takes a `source` param. |
| **D2** | **Causality model** | Every pivot carries `confirmed_at_index = i + k`. The engine may only "see" a pivot once `as_of_index ≥ confirmed_at_index`. | This is the no-look-ahead guarantee. The existing helpers don't track it — they stamp the pivot at `i`. We add confirmation tracking; we do **not** mutate divergence's behavior. |
| **D3** | **EW-2 structural scope** | Ship **impulse (1-2-3-4-5)** + **zigzag (A-B-C)** only. Defer flats, triangles, diagonals to the plan's backlog. | Keeps the first engine debuggable. Diagonals are the only case where rule 3 (wave-4 overlap) relaxes — excluding them keeps the rule check clean. |
| **D4** | **Degree** | Integer derived purely from `k` (index into `{3,5,8,13,21}` → degree 0..4). No textbook names ("minor"/"intermediate"). | Plan §6 already argues this; spec locks it. |
| **D5** | **Determinism tie-break** | Sort candidates by `(−confidence, tuple_of_pivot_timestamps)`. Floats never decide order alone. | Resolves plan open-decision #2. Guarantees byte-identical output. |
| **D6** | **Confidence calibration** | Punt. EW-1/EW-2 emit a raw composite in [0,1] (`rule_score`×`fib_score` blend, formula below). Isotonic calibration is a later phase. | Plan open-decision #3. We flag the number is *not* a probability yet. |

If you disagree with any recommendation, say so before I write code.

---

## EW-1 — Causal pivot foundation

### Files

```
app/indicators/pivots.py        NEW — PivotDetector + Pivot dataclass
app/indicators/registry.py      EDIT — register "pivots"
app/signals/divergence.py       EDIT — find_pivot_* become thin wrappers (no behavior change)
tests/test_pivots_unit.py       NEW
```

### Types

```python
# app/indicators/pivots.py

@dataclass(frozen=True)
class Pivot:
    index: int          # positional bar index of the extreme
    timestamp: datetime # bar timestamp of the extreme
    price: float        # the extreme price (high for 'high', low for 'low')
    kind: Literal["high", "low"]
    k: int              # fractal half-window that detected it
    degree: int         # index of k within {3,5,8,13,21}  (D4)
    confirmed_at_index: int  # = index + k  (D2) — first bar this pivot is "known"
```

### PivotDetector

```python
class PivotDetector(Indicator):
    """Multi-fractal causal pivot detector.

    Registry-compatible: compute() returns a +1/-1/0 Series (high/low/none)
    stamped at each pivot's true extreme bar. Causal consumers use
    detect() instead, which returns Pivot objects carrying confirmed_at_index.
    """
    def __init__(self, period: int = 5, source: Literal["hl", "close"] = "hl",
                 strict: bool = True): ...

    def compute(self, close, high=None, low=None) -> pd.Series:
        # +1 at pivot highs, -1 at pivot lows, 0 elsewhere. Series indexed
        # like input. For source="hl", needs high & low; raises if missing.

    def detect(self, close, high=None, low=None) -> list[Pivot]:
        # The causal surface. Returns Pivots sorted by index.
```

- `source="hl"` (D1): highs detected on `high`, lows on `low`.
  `source="close"` reproduces the legacy divergence behavior exactly.
- Reuses the centered-window logic already in `find_pivot_*`; the only
  additions are `confirmed_at_index` and the high/low source split.
- `divergence.find_pivot_lows/highs` are rewritten to delegate to
  `PivotDetector(source="close").detect(...)` and project back to the
  `List[Timestamp]` they return today — **identical output**, proven by test.

### Multi-degree helper

```python
def detect_multidegree(close, high, low, ks=(3,5,8,13,21)) -> list[Pivot]:
    # Run PivotDetector once per k; tag degree = ks.index(k); concat.
    # No subset enforcement here — the engine consumes per-degree streams.
```

### EW-1 test list (`tests/test_pivots_unit.py`)

| Test | Asserts |
|---|---|
| `test_pivot_high_on_synthetic` | Known peak in a 30-bar series detected at the right index/price. |
| `test_pivot_low_on_synthetic` | Mirror for troughs. |
| `test_hl_source_vs_close_differ` | On a series with wicks beyond closes, `source="hl"` finds a more extreme price than `source="close"`. |
| `test_confirmed_at_index` | For every pivot, `confirmed_at_index == index + k`. |
| `test_no_pivots_in_warmup_edges` | No pivot in first `k` or last `k` bars. |
| `test_degree_ordering` | Larger `k` yields a subset-ish (fewer, more significant) pivot count than smaller `k` on the same series. |
| `test_divergence_wrapper_unchanged` | `find_pivot_lows/highs` return byte-identical timestamps to the pre-refactor implementation (golden list pinned in the test). |
| `test_registry_roundtrip` | `get_indicator("pivots", period=8)` returns a `PivotDetector` with `period==8`. |
| `test_determinism` | Same input → identical `detect()` output across two calls. |

### EW-1 gate

`get_indicator("pivots", period=5)` returns expected pivots on a 100-bar
synthetic series; full `test_pivots_unit.py` green; `pytest tests/` shows
**no regression** in the existing divergence tests.

---

## EW-2 — Rule engine

### Files

```
app/signals/elliott/__init__.py     NEW
app/signals/elliott/README.md       NEW
app/signals/elliott/schemas.py      NEW — Pivot re-export + WaveCandidate + WaveLabeling
app/signals/elliott/rules.py        NEW — the 3 hard rules (pure predicates)
app/signals/elliott/fib.py          NEW — Fib ratio scoring + retracement/extension levels
app/signals/elliott/engine.py       NEW — WaveEngine.label(...)
tests/test_elliott_rules_unit.py    NEW
tests/test_elliott_engine_unit.py   NEW
tests/test_elliott_no_lookahead.py  NEW  ← gate zero
tests/test_elliott_purity.py        NEW  ← import-purity gate (see below)
```

`app/signals/elliott/` is a **pure** package: no `app.db`, `app.providers`,
`app.services` imports. There is no existing AST purity gate to reuse, so
EW-2 **adds** `tests/test_elliott_purity.py` — an AST-walk over the package
asserting none of those modules are imported (the gate other pure layers
should have had). Cheap, and it locks the lift-out contract from day one.

### Types (refines plan §3.3)

```python
# app/signals/elliott/schemas.py

class WaveCandidate(BaseModel):
    structure: Literal["impulse", "zigzag"]     # D3
    direction: Literal["up", "down"]
    pivots: list[Pivot]                          # 6 pivots (impulse: P0..P5) or 4 (zigzag: P0..A,B,C)
    labels: list[str]                            # ["0","1","2","3","4","5"] or ["0","A","B","C"]
    rules_passed: dict[str, bool]                # {"rule1_w2_retrace": True, "rule2_w3_not_shortest": True, "rule3_w4_no_overlap": True}
    rule_score: float                            # fraction of hard rules satisfied (gate: must be 1.0 to be valid)
    fib_score: float                             # 0..1 Fibonacci-fit (fib.py)
    confidence: float                            # composite (D6): fib_score if rule_score==1.0 else 0.0
    invalidation_price: float                    # level that voids this count
    fib_targets: dict[str, float]                # {"w3_1.618": 198.2, "w5_eq_w1": 205.0, ...}

class WaveLabeling(BaseModel):
    symbol: str
    interval: str
    as_of: datetime
    as_of_index: int
    primary: Optional[WaveCandidate]             # None if no valid count ≥ threshold
    alternates: list[WaveCandidate]              # top-(K-1), default K=3
    current_wave: Optional[str]                  # "3", "A", ... which wave price is IN now
    confidence: float                            # = primary.confidence or 0.0
    engine_ver: str                              # bumped on any engine change (reproducibility)
```

### rules.py (pure predicates over a candidate's pivots)

```python
def rule1_wave2_retrace(p) -> bool   # wave 2 retraces < 100% of wave 1
def rule2_wave3_not_shortest(p) -> bool  # |w3| not the smallest of |w1|,|w3|,|w5|
def rule3_wave4_no_overlap(p) -> bool    # wave 4 low (up-impulse) does not enter wave 1 high territory
HARD_RULES = [rule1..., rule2..., rule3...]
```

### fib.py

```python
FIB = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.618)
def retrace_pct(a, b, c) -> float                  # how far c retraces the a→b move
def nearest_fib(ratio) -> tuple[float, float]      # (level, abs_distance)
def score_impulse(pivots) -> float                 # 0..1: w2 retrace ∈[.5,.786]? w3≈1.618×w1? w4 retrace∈[.236,.382]? w5≈w1?
def score_zigzag(pivots) -> float                  # 0..1: B retrace of A, C≈A or 1.618×A
def targets(pivots, structure) -> dict[str, float] # forward Fib projection levels
```

### engine.py

```python
class WaveEngine:
    version = "ew2.0.0"
    def __init__(self, k_set=(3,5,8,13,21), top_k=3, min_confidence=0.5): ...

    def label(self, pivots: list[Pivot], *, symbol, interval,
              as_of_index: int, as_of: datetime) -> WaveLabeling:
        # 1. Filter to pivots with confirmed_at_index <= as_of_index   (D2 — causality)
        # 2. Per degree, enumerate candidate 6-pivot impulse / 4-pivot zigzag
        #    windows ending at the latest confirmed pivot.
        # 3. Drop any candidate with rule_score < 1.0 (hard-rule gate).
        # 4. Score survivors via fib.py; confidence = fib_score.
        # 5. Sort by (−confidence, pivot-timestamp tuple)  (D5).
        # 6. primary = best if confidence ≥ min_confidence else None.
        #    alternates = next top_k-1. current_wave from primary's open leg.
```

No I/O. Deterministic. `label()` is a pure function of its inputs.

### EW-2 test list

`tests/test_elliott_rules_unit.py`

| Test | Asserts |
|---|---|
| `test_rule1_rejects_deep_w2` | Wave 2 retracing >100% of wave 1 → `rule1` False. |
| `test_rule2_rejects_short_w3` | Wave 3 shortest → `rule2` False. |
| `test_rule3_rejects_w4_overlap` | Wave 4 entering wave 1 territory → `rule3` False. |
| `test_textbook_impulse_passes_all` | Clean 5-wave path → all three True. |

`tests/test_elliott_engine_unit.py`

| Test | Asserts |
|---|---|
| `test_synthetic_impulse_labeled` | Synthetic 5-wave up path → primary `structure=="impulse"`, `confidence>0.8`, labels `0..5`. |
| `test_synthetic_zigzag_labeled` | Synthetic ABC → primary `structure=="zigzag"`. |
| `test_rule_violation_no_primary` | Path violating a hard rule → primary is None or a different valid count (never the violator). |
| `test_topk_alternates` | Ambiguous path → ≥1 alternate returned, sorted by confidence desc. |
| `test_determinism_byte_identical` | Two `label()` calls on identical input → equal serialized output. |
| `test_tie_break_stable` | Two candidates with equal confidence → ordered by pivot-timestamp tuple. |
| `test_current_wave_open_leg` | `current_wave` equals the in-progress leg after the last confirmed pivot. |

`tests/test_elliott_no_lookahead.py` — **gate zero**

| Test | Asserts |
|---|---|
| `test_label_stable_when_future_added` | Run engine over bars `1..N`, then `1..N+10`; the `WaveLabeling` for every `as_of_index ≤ N` is byte-identical between runs. |
| `test_unconfirmed_pivot_invisible` | A pivot whose `confirmed_at_index > as_of_index` never appears in any candidate. |

### EW-2 gate

1. All four test files green; gate-zero (`test_elliott_no_lookahead.py`) and
   the purity gate (`test_elliott_purity.py`) green.
2. CLI spot-check: run the engine over 5y AAPL daily, emit a parquet
   (`symbol, ts, current_wave, confidence, invalidation_price, alternates_n`).
   Eyeball 20 random labels against the chart — primary count "plausible"
   on ~70%+ (soft sanity, not a hard gate).
3. AST purity gate confirms `app/signals/elliott/` imports nothing from
   `app.db` / `app.providers` / `app.services`.

---

## What this spec deliberately does NOT cover

- DTW (EW-2.5), WaveReader/HTTP/MCP exposure (EW-3), screener rules (EW-4),
  strategies (EW-5), the `equities.elliott_wave_labels` store (EW-6), and all
  training-track work (EW-7+). Each gets its own spec when we reach it.
- Flats / triangles / diagonals (D3 defers).
- Confidence calibration (D6 defers).

---

## Estimated effort

EW-1: ~2 days (pivots are mostly a refactor + causality bookkeeping + tests).
EW-2: ~4–5 days (enumeration + scoring + the determinism/no-look-ahead tests
are the bulk). Gate-zero is written **first**, before the engine, and drives
the design.
