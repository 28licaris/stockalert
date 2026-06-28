# `app/services/sectors` — Sector Rotation (RRG)

Classifies market groups into the four RRG quadrants — **Leading / Weakening
/ Improving / Lagging** — relative to a benchmark (SPY). Phase 1 covers the 11
SPDR sector ETFs. Spec: [`docs/sector_rotation_spec.md`](../../../docs/sector_rotation_spec.md).

## The one abstraction

Everything consumes a **`RotationGroup`**, never a raw symbol. A group resolves
to a single daily close series:

- `kind="etf"` → passthrough of one ETF's close (Phase 1).
- `kind="basket"` → weighted index of N constituents (Phase 2 theme catalog).

The RRG math, the API route, and the frontend are all kind-agnostic, so adding
the ~50-theme catalog later means: register `basket` groups in `definitions.py`
and implement the basket branch in `resolver.py`. Nothing else changes.

## Files

| File | Role |
|------|------|
| `schemas.py` | Pydantic contracts (`RotationGroup`, `RotationPoint`, `SectorRotationState`, `RotationDashboard`). The API/UI boundary. |
| `definitions.py` | Registry of groups (11 SPDR ETFs) + benchmark. Phase-2 seam. |
| `resolver.py` | `RotationGroup → daily close series` via `bars_gateway` (lake, `1d`, split-adjusted). ETF passthrough; basket raises until Phase 2. |
| `rrg.py` | Pure math: RS-Ratio, RS-Momentum, quadrant, weekly tail, rebased RS line. Total functions — thin data → typed `SectorScore(sufficient=False)`. |
| `service.py` | `SectorRotationService.from_settings()` → `build_dashboard()`. Resolves benchmark once, scores each group, surfaces failures in `excluded`. |

## The math (our flavor of JdK RS-Ratio / RS-Momentum)

```
rel(t)         = close_group(t) / close_benchmark(t)
rs_ratio(t)    = 100 · rel(t) / SMA(rel, RRG_RATIO_WINDOW)(t)
rs_momentum(t) = 100 · rs_ratio(t) / SMA(rs_ratio, RRG_MOM_WINDOW)(t)
```

Both axes center on **100** (in line with the benchmark). Quadrant = which side
of 100 each axis sits on; the 100 line is inclusive on the leading/strong side
(`classify`). Windows are config (`config.py: rrg_*`), never magic literals.

> **Intuition that trips people up:** a *linearly* outperforming sector is
> **weakening**, not leading — its percentage lead shrinks as its own base
> grows, so RS-Momentum dips below 100. "Leading" requires the relative
> strength to still be *accelerating*. The unit tests encode this.

## Data

Daily, split-adjusted closes come from the lake reader
(`get_chart_bars(sym, interval="1d", source=LAKE)`) — the lake reaches back the
full ~1yr the SMAs need, where the ClickHouse hot cache only holds ~48 days.
Symbols must be present in the lake: Polygon flat-files (whole-market) cover
history; `scripts/schwab_history_backfill.py` fills the recent ~48-day gap. See
the spec §4 for the one-time ops steps.

## Tests

`pytest app/services/sectors/tests/` — quadrant boundaries, the
accelerating/decelerating quadrant scenarios, insufficient-history totality,
ETF passthrough + basket seam, dashboard assembly + exclusion surfacing.
