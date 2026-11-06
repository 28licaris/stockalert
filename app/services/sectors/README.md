# `app/services/sectors` — Sector Rotation (RRG)

Classifies market groups into the four RRG quadrants — **Leading / Weakening
/ Improving / Lagging** — relative to a benchmark (SPY). Phase 1 covers the 11
SPDR sector ETFs. Spec: [`docs/trading/sector_rotation.md`](../../../docs/trading/sector_rotation.md).

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
| `resolver.py` | `load_daily_closes()` reads every needed symbol's split-adjusted daily closes from CH `ohlcv_daily` in **one query**; `resolve(group, closes)` turns that into one series per group (ETF passthrough, weighted basket). No fallback to minute bars: a missing symbol is excluded with a named reason. |
| `rrg.py` | Pure math: RS-Ratio, RS-Momentum, quadrant, weekly tail, rebased RS line. Total functions — thin data → typed `SectorScore(sufficient=False)`. |
| `personal_themes.py` | A customer's **own** themes: model + user-scoped repository + service on the identity PostgreSQL (`user_sector_themes`, migration `20261106_06`). Ids are `my:<slug>`; flagged `personal` on the dashboard. **Never** onboarded into the shared streaming universe (daily closes only; a ticker without data shows up as `excluded`). Caps: 10 themes/user, 20 tickers/theme. |
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

Daily, split-adjusted closes come from ClickHouse **`ohlcv_daily`**, read in one
query per page load (about 0.03 s for the whole dashboard). RRG is a daily/weekly
technique, so it never touches minute bars; that also keeps it independent of the
300-symbol Schwab streaming cap (`docs/ISSUES.md#schwab-stream-300-symbol-account-cap`),
which left SPY and the sector ETFs without live minute bars.

`ohlcv_daily` is refreshed nightly (`scripts/refresh_ohlcv_daily.py`, run by
`paper_daily_pipeline.py`). Its universe is `configs/liquid_universe.txt` plus the
active stream universe and every ticker in a user's personal theme
(`app/services/ingest/daily_universe.py`), so shared and personal themes stay current.
The data is one trading day behind by design; the dashboard's `as_of` says so.

**A symbol with too little history** (the RRG math needs ~70 sessions) is excluded
with the reason, not served stale. To fill history for a symbol:

```bash
docker compose run --rm --no-deps worker python scripts/ensure_daily_history.py --symbols SCCO,ERO --dry-run
docker compose run --rm --no-deps worker python scripts/ensure_daily_history.py --symbols SCCO,ERO
```

Personal themes do this automatically: a ticker we do not hold is checked against
Schwab when the theme is created (typos are rejected), saved, and its history is
fetched in the background into the lake (`schwab_daily_raw`) and ClickHouse
(`app/services/ingest/daily_on_demand.py`). The chart says "loading price history"
until it lands. Limits: 8 brand-new tickers per request, 500 distinct personal tickers
overall, and a 24 h memory of tickers Schwab does not know.

## Tests

`pytest app/services/sectors/tests/` — quadrant boundaries, the
accelerating/decelerating quadrant scenarios, insufficient-history totality,
ETF passthrough + basket seam, dashboard assembly + exclusion surfacing.
