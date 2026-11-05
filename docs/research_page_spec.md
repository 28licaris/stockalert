# Research page — spec

Status: **building P1** (2026-07-05). Confirmed via product review.

A **premium, customer-facing** research/screener: find top stocks by momentum,
gainers/losers, activity, and streaks — one-click **presets** backed by a
**composable filter builder**.

## Confirmed decisions
| Decision | Choice |
|---|---|
| Audience | Premium customer feature (gated by subscription; open in dev) |
| Universe | Top-1000 liquid names in `ohlcv_daily` |
| Filter model | Preset views on top of a composable builder |
| Freshness | EOD rankings (daily) + a Live Movers tab (intraday) |
| Presets (v1) | Momentum Leaders · Top Gainers · Top Losers · Most Active · On a Streak |
| Builder criteria (v1) | return over lookback · min $-volume · streak ≥N · RSI / % from 52w high / above-below SMA |
| Live source | streamed `ohlcv_1m` (real-time) + `/movers` provider fallback |
| Row actions | open chart · add to watchlist · ask AI assistant · save screen |

## Definitions
- **Momentum** = trailing total return; default **60 trading days** (≈84 cal), selector 20/60/120.
- **Streak** = consecutive daily closes up/down vs prior close; filter `streak ≥ N` (default 3).
- **Gainers/Losers** = %chg; EOD = 1-day since prior close (1W/1M options); Live = since the open.

## Data-foundation realities (found while prototyping — important)
1. **Ranking queries MUST be gap/liquidity/sanity-guarded.** `ohlcv_daily` holds
   20 yr of history including reused/relisted tickers (NE, JAVA, Q…) with
   multi-year gaps. A naïve N-row-back return returns garbage (`NE +22,506%`).
   Guards (all rankings):
   - **Recent-window anchor**: only consider bars in the last ~180 calendar days.
   - **No-gap lookback**: the "N days ago" price must itself be recent (within
     ~1.5× the lookback window) — else the symbol is excluded.
   - **Liquidity floor**: min avg dollar-volume (default $10M/day).
   - **Currently trading**: latest bar within a few days of the table max.
   - **Sanity cap** on |return| for display (data-error guard).
   Verified: with guards the momentum leaders are sane (MU/AMD/SOXL/ARM…).
2. **Freshness caveat.** `ohlcv_daily` is currently a snapshot (last close
   2026-06-30) — the top-1000 daily feed (Polygon grouped-daily → ohlcv_daily)
   is not currently refreshing. **v1 labels results "as of {last close}".**
   Keeping the top-1000 daily current is an **ops task** (re-enable/verify the
   grouped-daily nightly), tracked separately — it is NOT a blocker for the
   feature, which reads whatever the latest close in the table is.

## Architecture
- **Rankings (EOD)**: `app/services/research/rankings.py` — guarded ClickHouse
  SQL over `ohlcv_daily`; `GET /api/v1/research/rankings?preset=…` +
  `POST /api/v1/research/screen` (builder). Read-only, on-demand (top-1000 is
  small → fast). No precompute.
- **Builder**: extend the existing `app/services/screener` engine where it fits.
- **Live movers**: `ohlcv_1m` resample + `/movers` fallback (P3).
- **Save screens**: per-user `saved_screens` table in identity Postgres (P4).
- **Gating**: route + API behind the existing `require_subscription` dependency
  (no-op in dev where auth is off).

## Phases
- **P1 — EOD presets**: rankings service + `/research/rankings` + research page
  (preset tabs, guarded table, row actions chart/watchlist/assistant). ← now
- **P2 — Custom builder**: criteria UI + engine extensions + `/research/screen`.
- **P3 — Live Movers tab**: intraday + provider fallback.
- **P4 — Save screens**: `saved_screens` table + save/load/manage.
