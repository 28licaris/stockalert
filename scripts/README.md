# scripts/

Operator and research entry points. Nothing here is imported by the running
app — these are run by hand, by a scheduled job, or in CodeBuild.

Procedures that use these scripts live in
[`../docs/operations/runbook.md`](../docs/operations/runbook.md).

## Ingest / backfill — provider → lake

| Script | Source → target |
|---|---|
| `lake_import_athena.py` | existing S3 cache → `equities.polygon_raw` (one-time bulk) |
| `polygon_history_backfill.py` | Polygon flat-files → `equities.polygon_raw` |
| `schwab_history_backfill.py` | Schwab REST 1m → `equities.schwab_universe` |
| `schwab_universe_backfill.py` | Schwab REST → the universe table |
| `schwab_daily_refresh.py` | Schwab REST daily → `equities.schwab_daily_raw` |
| `ensure_daily_history.py` | Fetch + store only the missing daily history for given symbols (lake first, then ClickHouse); `--dry-run` shows coverage. Per-symbol outcome table, exit 1 on any failure |
| `run_corp_actions_backfill.py` | Polygon REST → `equities.market_corp_actions` |
| `backfill_market_splits.py` | corp-actions → `equities.market_splits` |
| `backfill_instrument_names.py` | Polygon reference → CH `instrument_names` |
| `export_dividend_dates.py` | corp-actions → dividend date export |

## Futures

| Script | Role |
|---|---|
| `polygon_futures_mirror.py` | byte-for-byte flat-file mirror → `polygon_flatfiles_mirror/` |
| `polygon_futures_parse_raw.py` | mirror → `futures.polygon_raw` (outright contracts) |
| `polygon_futures_build_continuous.py` | volume roll + ratio back-adjust → `futures.polygon_continuous` |
| `futures_history_backfill.py` / `futures_daily_backfill.py` | Schwab REST → `futures.schwab_futures{,_daily}` |

## Options / volatility

| Script | Role |
|---|---|
| `options_chain_snapshot.py` | one Schwab `/chains` snapshot → options lake |
| `options_reparse.py` | re-parse stored raw payloads (full rebuild, never upsert) |
| `thetadata_backfill.py` | ThetaData EOD → `options_atm_iv_daily` |
| `thetadata_gex_backfill.py` | ThetaData → GEX history |
| `build_atm_iv_history.py` | ATM IV baseline |
| `build_vol_premium_history.py` | `vol_premium_history` (GJR-GARCH, monthly refit) |
| `vol_premium_screen.py` | the vol-premium screen (CLI — the only way to reach it today) |
| `export_gex_features.py` / `export_vanna_iv_features.py` | dealer-flow feature exports |

## ClickHouse / research tables

| Script | Role |
|---|---|
| `rebuild_ch_from_lake.py` | **the** canonical bulk lake → CH loader |
| `build_ohlcv_daily.py` | research daily table (adjusted, delisted-inclusive, segment-trimmed) |
| `build_ohlcv_30m.py` / `build_ohlcv_hourly.py` | research intraday tables |
| `refresh_ohlcv_daily.py` | incremental refresh of the daily research table |
| `build_daily_universe.py` / `rank_liquidity.py` | universe + liquidity ranking |
| `compact_bronze.py` / `compact_bronze_monthly.py` | Iceberg file compaction |
| `dd_resample.py` | drawdown resampling helper |

## Strategy R&D

**Pre-register in [`../docs/research/hypotheses.md`](../docs/research/hypotheses.md)
before any sweep**, and gate return claims on MCPT
([`docs/research/mcpt_methodology.md`](../docs/research/mcpt_methodology.md)).

| Script | Role |
|---|---|
| `run_backtest.py` / `run_portfolio.py` / `run_bakeoff.py` | single, portfolio and comparative runs |
| `strategy_sweep.py` / `oos_search.py` / `walkforward_search.py` | parameter search |
| `mcpt_insample.py` / `mcpt_walkforward.py` / `mcpt_cloud_study.py` / `mcpt_report.py` | the permutation gate |
| `mcpt_ranker_labels.py` / `train_ranker.py` / `train_ranker_gbm.py` | ranker datasets + training |
| `build_trade_dataset.py` / `research_bars.py` | dataset assembly |
| `daytrade_*.py`, `build_daytrade_candidates.py` | the day-trading track (**closed** — see `project_daytrading_track`) |
| `exp50_dev_grid.py`, `exp56_h53_*.py`, `fomc_trade_audit.py` | specific recorded experiments |
| `improve_strategy.py` / `register_strategy.py` | strategy authoring + registration |
| `validate_pipeline.py` | end-to-end pipeline validation |

## Paper trading

`paper_trade_run.py`, `paper_run_all.py`, `paper_daily_pipeline.py` — the
forward paper record. **This is the only honest track record**; backtest
numbers are not.

## Elliott Wave

`ewt_label.py`, `ewt_recompute.py`, `ewt_poc.py`, `ewt_preview_server.py`

## PPL

`ppl_calibrate.py` — the PIT calibration gate.

## Health checks & auth

| Script | Role |
|---|---|
| `schwab_get_refresh_token.py` | OAuth flow → `data/.schwab_refresh_token` (see below) |
| `check_schwab_live.py` / `check_polygon_live.py` / `check_polygon_flatfiles.py` | credential + entitlement probes |
| `check_daily_gaps.py` | coverage gap report |
| `ch_verify.sh` | ClickHouse sanity checks |

`check_*.py` are named that way deliberately so pytest does not collect them.

## Subdirectories

| Dir | Holds |
|---|---|
| [`codebuild/`](codebuild/README.md) | CodeBuild buildspecs for long backfills |
| [`aws/`](aws/README.md) | IAM policies + one-off AWS setup |
| [`data/`](data/README.md) | Small curated CSV seeds (release-date calendars) |
| [`dev/`](dev/README.md) | Local development helpers |
| [`spikes/`](spikes/README.md) | Throwaway investigation scripts |
| `tests/` | Tests for the scripts themselves |

## Writing a new script

Per [`../docs/standards/coding.md`](../docs/standards/coding.md):

- **Pipefail** in any shell; **no bare `except`**.
- **Log every outcome, including zero** — "0 rows written" must be visible.
- **Per-iteration completion markers** for loops, so a stall is distinguishable
  from slow progress.
- **A preflight for anything over 5 minutes**: check credentials, entitlement
  and target-table existence *before* the long part.
- **Verify mutations cross-side** — write to the lake, then read it back and
  assert; exit non-zero on mismatch.
- **Idempotent reruns.** Append + dedup-on-read, never `overwrite(filter)`.

---

## Schwab API (real data)

**Credentials to test the Schwab data provider** — set these in `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATA_PROVIDER` | Yes | Set to `schwab` or `thinkorswim` so the app uses Schwab. |
| `SCHWAB_CLIENT_ID` | Yes | App key (Client ID) from Schwab Developer Portal. |
| `SCHWAB_CLIENT_SECRET` | Yes | App secret from the same app. |
| `SCHWAB_CALLBACK_URL` | Yes | HTTPS callback: **Option A** use a local URL (e.g. `https://127.0.0.1:8080/oauth/callback`) and paste the redirect URL; **Option B** use your ngrok URL + `/callback` for automatic callback. |
| `SCHWAB_REFRESH_TOKEN` | Optional* | From the one-time OAuth flow; or leave unset and use the token file (see below). |
| `SCHWAB_REFRESH_TOKEN_FILE` | No | Where to read/write the refresh token (default `data/.schwab_refresh_token`). App reads token from env first, then from this file. |
| `SCHWAB_BASE_URL` | No | Default `https://api.schwabapi.com`; only set if using a different base. |

\* You need a refresh token **either** in `SCHWAB_REFRESH_TOKEN` **or** in the token file. The get-refresh-token script writes to the file so you don’t have to edit `.env` each time.

### 1. Get a refresh token (one-time, then ~every 7 days)

The Schwab API uses OAuth. You need a **refresh token** before the app can get access tokens and fetch data. Schwab requires an **HTTPS** callback URL. You can use either option below.

**Option A – Paste URL (no ngrok)**

No extra tools. Use a local callback URL; after sign-in you paste the redirect URL into the script.

1. In the [Schwab Developer Portal](https://developer.schwab.com/), add callback URL: **`https://127.0.0.1:8080/oauth/callback`**
2. In `.env` set: `DATA_PROVIDER=schwab`, `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, **`SCHWAB_CALLBACK_URL=https://127.0.0.1:8080/oauth/callback`**
3. Run: **`poetry run python scripts/schwab_get_refresh_token.py`**
4. Sign in when the browser opens. The browser will redirect and show "site can't be reached" — **copy the full URL from the address bar** (it contains `?code=...`).
5. Paste that URL when the script asks. The script will exchange the code and save the token to `data/.schwab_refresh_token`.

**Option B – Ngrok (automatic callback)**

The redirect loads a success page and the script receives the token without pasting.

1. Install [ngrok](https://ngrok.com/) and run: **`ngrok http 8765`**
2. Copy the **HTTPS** URL ngrok shows (e.g. `https://abc123.ngrok-free.app`).
3. In the [Schwab Developer Portal](https://developer.schwab.com/), add: **`https://YOUR_NGROK_URL/callback`**
4. In `.env` set: `DATA_PROVIDER=schwab`, `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, **`SCHWAB_CALLBACK_URL=https://YOUR_NGROK_URL/callback`**
5. Leave ngrok running and run: **`poetry run python scripts/schwab_get_refresh_token.py`**
6. Sign in when the browser opens. You'll see "Authorization successful" and the script will save the token.

### 2. Test the API with real data

After you have a refresh token (in `.env` or in the token file):

```bash
poetry run python scripts/check_schwab_live.py
```

Optional arguments:

- `--symbol AAPL` (default: SPY)
- `--days 2` (default: 1)

This checks token exchange, user principals (streamer), historical bars, all Market Data REST endpoints (quotes, option chains, movers, market hours, instruments), and Trader API account/order data. If user preference fails (e.g. 404), the script continues so Market Data and historical tests still run.

**TOS/Schwab integration scope (data only):** The integration uses the API specs in `api_docs/` (Account Access, Market Data, Streamer, Security). It covers Market Data REST (quotes, price history, chains, movers, markets, instruments), Trader API (user preference, accounts, orders, transactions), and Streamer (real-time bars). Trading is manual; no order entry is implemented in this phase.
