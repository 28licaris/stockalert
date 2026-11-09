# Spec — un-streamed and stale tickers

Status: **SPEC — approved by the operator in conversation; written 2026-11-09.**
Nothing here is built. Per [`../standards/engagement.md`](../standards/engagement.md)
the work lands as three separate PRs (§7); anything beyond them needs its own
sign-off. Companion specs: [`spec_auth_token_automation.md`](spec_auth_token_automation.md),
[`spec_per_user_schwab_linking.md`](spec_per_user_schwab_linking.md).

## 0. The ask, restated

A user can add any ticker to a watchlist or open its chart. Only 300 symbols
stream (Schwab's account-level `CHART_EQUITY` cap, see
[`../ISSUES.md`](../ISSUES.md) `schwab-stream-300-symbol-account-cap`), and the
lake may not hold the ticker at all. Define **one behaviour for every
ticker**, label how fresh its data is, reject tickers that do not exist, and
fix the stream so the symbols that matter are the ones that deliver.

## 1. What a user sees today

| Surface | Ticker not streamed and not in the lake |
|---|---|
| Watchlist add | Any string accepted (no validation). Written into the shared `stream_universe` with no cap check. No history fetched. Price and P&L show "—" until a bar exists, possibly never. |
| Intraday chart | `get_chart_bars_hydrated` falls through to a direct Schwab REST pull after "Fetching history…". Works, but: 5-minute cache, minute bars clamped to ~48 days, nothing written to lake or ClickHouse. |
| Chart failure | A bad ticker **or a dead Schwab token** returns `[]` with HTTP 200; the UI says "No data available". The two are indistinguishable. |
| Daily chart | No on-demand fetch; empty unless already in `ohlcv_daily`. `ensure_daily_history` is used only by personal sector themes. |
| Quote | No per-symbol route. Watchlist `current_price` reads ClickHouse only. |
| Stream slots | `_apply_subscription_diff` subscribes `sorted(...)`, i.e. alphabetical. Past 300, symbols are accepted and silently deliver nothing: SPY, the `XL*` sector ETFs and S–Z get no live bars. SUBS/ADD acks are not logged after login, so `REACHED_SYMBOL_LIMIT` (code 19) is never seen, and `/stream/status` reports *subscribed*, not *delivered*. |

## 2. Decision

Layered, in this order of value:

- **A. Validate and fetch on watchlist add** (reuse `DailyDemand`).
- **B. Freshness label** per symbol, in the API and the UI.
- **C. REST quote polling** for non-streamed watchlist symbols.
- **D. Stream priority order + diagnostics.**

**Not now:** rotating stream slots toward viewed symbols (churn, lake gaps,
cap is account-wide) and persisting hydrated minute bars to the lake/CH
(overlaps lake-integrity; separate decision).

## 3. A — validation and on-demand daily history

- `POST /api/v1/my/watchlists/{name}/members` calls `DailyDemand.check` first.
- **Rejected if Schwab positively says the ticker does not exist.** Response is
  a 422 with `{symbol, reason: "unknown_ticker"}` and a plain message
  ("We couldn't find XYZ"). The existing 24 h unknown-ticker memory
  (`UNKNOWN_TTL_S`) prevents repeat probes.
- **Source outage or dead token is not "unknown".** The symbol is saved with
  state `pending`, a banner says data is temporarily unavailable, and a
  background retry resolves it. We never reject a real ticker because Schwab
  was down.
- **Per-user cap on new tickers: 25 per day (config
  `WATCHLIST_NEW_TICKERS_PER_DAY`).** Counts only tickers the system does not
  already know (covered tickers are free). Over the cap: 429 with the reset
  time. `DailyDemand`'s own per-request limit (`MAX_NEW_PER_REQUEST=8`) stays.
- Accepted new tickers: `DailyDemand.schedule` fetches daily history in the
  background (lake first, then ClickHouse, as `ensure_daily_history` does).
- Re-adding an existing member is idempotent and costs nothing against the cap.
- `_ensure_streaming` stays, but only for symbols that fit the priority
  budget (§6); overflow symbols are marked, not silently subscribed.

## 4. B — freshness states

Every ticker resolves to exactly one state. The API returns it; the UI never
shows a bare "—" or "No data available" without a reason.

| State | Meaning | UI |
|---|---|---|
| `live` | Streaming and delivering within the last N minutes of a session | none |
| `delayed` | Not streamed; served by REST (hydration / quote poll) | "Delayed" badge + as-of time |
| `daily_only` | Only daily bars exist | "Daily only" badge, intraday disabled with explanation |
| `loading` | Fetch in flight | spinner + "Fetching history…" |
| `pending` | Saved, validation or fetch deferred by an outage | banner, auto-retries |
| `unavailable` | Data source down / token dead | explicit "Data source unavailable", not an empty chart |

- `bars_hydration` stops swallowing auth failures: it returns the
  `unavailable` signal and raises the same alert hook Spec 2 uses.
- State is computed server-side from stream status + coverage; the frontend
  only renders it.

## 5. C — REST quote polling for non-streamed symbols

- Watchlist `current_price` / P&L falls back to a cached **batch** quote via
  `QuoteService` for symbols with no recent ClickHouse bar. Refresh ~30 s while
  a watchlist is open; one batched call, never per symbol.
- Result carries `as_of`, which feeds the `delayed` badge.
- No new write path: this reads, caches in memory, and does not touch the lake.

## 6. D — stream priority and diagnostics

Fill at most `STREAM_MAX_SYMBOLS` (default 300, config; raise only when Schwab
confirms a number) in this order:

1. SPY, sector ETFs, index proxies.
2. Shared-theme members.
3. Watchlist symbols, ranked by number of holders then recency.
4. The rest of the base universe.

- Symbols beyond the budget get `overflow` status and fall back to B and C.
- The ordering replaces `sorted(after - before)` in
  `_apply_subscription_diff`; the resync loop (60 s) re-evaluates it.
- **Diagnostics:**
  - Log post-login SUBS/ADD acks per chunk, including code 19.
  - `/stream/status` adds `delivered_count` (a bar in the last N minutes
    during session), `overflow_count` and `overflow_symbols`.
  - A warning when `subscribed - delivered` exceeds a threshold during
    regular hours.
- No streaming experiments without operator approval (see ISSUES.md).

## 7. PRs and files

1. **Diagnostics + priority:** `app/providers/schwab_provider.py` (ack logging),
   `app/services/stream/service.py`, `app/api/schemas/stream.py`, config.
2. **A + B:** `app/services/watchlists/service.py`,
   `app/api/routes_my_watchlists.py`, `app/services/readers/bars_hydration.py`,
   freshness schema, `MyWatchlists.tsx`, `symbol.tsx`, OpenAPI
   `types.gen.ts` regenerated.
3. **C:** `QuoteService` fallback in the watchlist detail path, cache, UI as-of.

Each PR updates the touched module READMEs and the route-policy test stays green.

## 8. Tests

- Each freshness state; unknown-ticker rejection; outage → `pending`, never
  rejection; the daily cap and its reset; idempotent re-add.
- Priority order with >300 candidates: tier 1 always subscribed, overflow marked.
- Ack parsing including code 19; delivered-vs-subscribed counter.
- Hydration returns `unavailable` on a dead token (not `[]`).
- Every collaborator injected (provider, clock, store) per
  [`../standards/testing.md`](../standards/testing.md); run with
  `CLICKHOUSE_HOST=127.0.0.1 CLICKHOUSE_PORT=1`.

## 9. Open items

- The exact Schwab streaming symbol limit is unconfirmed (the operator
  believes 500; we measured 300 delivering; the streamer guide gives only
  error 19 with no number). Resolve before raising `STREAM_MAX_SYMBOLS`.
- Whether serving Schwab data to customers is allowed under the current
  registration is unconfirmed; see
  [`spec_per_user_schwab_linking.md`](spec_per_user_schwab_linking.md) §2.
