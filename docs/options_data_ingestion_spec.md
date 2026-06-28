# Options Data Ingestion Spec

Status: draft for approval

## Goal

Add options market-data ingestion so StockAlert can support option alerts,
market-opportunity scans, backtests, simulated trading, and agent access over
the same hot/cold architecture used by equities and futures.

The first provider is Schwab. The first durable dataset is option-chain
snapshots, not historical option bars. Schwab exposes option-chain and
expiration-chain REST endpoints today through `SchwabProvider`; real-time
option streaming can follow once the snapshot contract is stable.

Gamma exposure (GEX) is a first-class target use case. The default path is to
compute our own GEX from Schwab chain snapshots using contract gamma, open
interest, underlying price, put/call side, strike, expiration, and multiplier.
Unusual Whales can be evaluated later as an enrichment source for proprietary
flow, dark pool, precomputed GEX, and broader market scans, but it is not a
required dependency for the first architecture.

## Non-Goals

- No order placement or execution logic.
- No options strategy engine in the first ingestion phase.
- No assumption that Schwab can provide deep historical option minute bars.
  Historical replay will come from our persisted chain snapshots unless a
  separate provider/source is approved later.
- No direct provider payload dependencies in alerts, simulations, MCP tools,
  or API routes. All consumers use canonical contracts.
- No paid Unusual Whales dependency in the first implementation. If added
  later, it must feed canonical tables/readers instead of becoming a separate
  consumer-facing integration path.

## Current Repo Baseline

Schwab option REST access already exists:

- `SchwabProvider.get_option_chains(symbol, **kwargs)` calls
  `GET /marketdata/v1/chains`.
- `SchwabProvider.get_expiration_chain(symbol, **kwargs)` calls
  `GET /marketdata/v1/expirationchain`.
- `scripts/check_schwab_live.py` already smoke-checks both endpoints.
- Schwab streamer docs and provider constants list `LEVELONE_OPTIONS`,
  `OPTIONS_BOOK`, and `SCREENER_OPTION`, but the repo currently implements
  equity/futures bar streaming only.

Unusual Whales exposes API/MCP surfaces for option flow, dark pool data,
Greek exposure/GEX, WebSocket, Kafka, and MCP. Their public API docs list GEX
and Greek exposure endpoints, including spot GEX per-minute and by
strike/expiry. Their docs also list historical option trades at `$250/month`
for the full market, with a discount for more than one year. This spec treats
that as a future optional provider, not the default ingestion source.

## Coverage Compared With Unusual Whales

The Schwab-first snapshot architecture covers the foundational chain and GEX
surface, but it does not attempt to recreate every Unusual Whales data product
in the first build.

Covered in the Schwab-first path:

- Option chains and expirations.
- Bid, ask, last, mark, volume, open interest, and Greeks from chain snapshots.
- Derived GEX by total, strike, expiry, and strike plus expiry.
- Replayable chain/GEX snapshots for alerts, backtests, simulations, API, and
  MCP.

Missing unless added later through Schwab streaming, our own capture logic, or
an external provider such as Unusual Whales:

- Individual option trade tape, including sweeps, blocks, premium, exchange,
  trade condition, and side/aggressor inference.
- Historical full-market option trades before our own ingestion begins.
- Dark pool and off-lit equity prints.
- Lit-flow aggregates.
- Greek flow from actual trade flow, not just open-interest snapshots.
- Contract-level intraday OHLC/volume profile beyond periodic chain snapshots.
- Market tide, net flow, and sector/ETF tide style sentiment aggregates.
- Hottest chains, unusual activity scans, and flow-alert feeds.
- IV rank, interpolated IV, skew/risk-reversal skew, variance risk premium,
  volatility anomaly scores, and realized-volatility statistics.
- Max pain and richer open-interest/volume breakdowns by expiry and strike.
- Real-time WebSocket/Kafka channels for flow, GEX, lit/off-lit trades, and
  market-wide alerts.

Design implication: the first implementation should not overfit to Unusual
Whales response shapes, but the lake should leave room for optional enrichment
tables with provider-specific raw payloads and canonical derived outputs.

## Architecture

Options become a peer domain beside equities and futures:

```text
app/services/options/
  schemas.py      Pydantic DTOs for chain snapshots, contracts, reads
  contract.py     Reader/ingestor protocols
  tables.py       Iceberg table creation
  sink.py         Iceberg write path
  service.py      Schwab chain snapshot orchestration
  gaps.py         Coverage/gap queries by underlying and snapshot cadence
  README.md       Ownership, contract, test commands
  tests/

app/services/readers/
  options_reader.py  Lake and hot-tier reader returning options DTOs

app/api/
  routes_options.py  Thin HTTP adapter over options reader/service

app/mcp/
  option tools      Thin MCP adapters over the same reader/service
```

This follows the repo service rules:

- Consumers import `schemas.py` / `contract.py`, not `service.py`.
- `from_settings()` is the only global-config entry point.
- Provider-specific payloads stay at the edge.
- Reads for backtests and training work directly from Iceberg.
- Alerts can use ClickHouse hot tables for recent/latest data.
- Agents get the same behavior through MCP and HTTP route adapters.

## Data Model

Use a separate Glue database and S3 prefix:

- Glue database: `options`
- S3 prefix: `iceberg/options/`

### `options.schwab_chain_raw`

Append-only audit table, one row per provider response.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `underlying_symbol` | string | yes | Uppercase equity/index symbol requested. |
| `snapshot_ts` | timestamptz | yes | UTC time the response was captured. |
| `provider` | string | yes | `schwab`. |
| `request_params` | string | yes | JSON query params used for reproducibility. |
| `status` | string | yes | Provider response status/body status when present. |
| `is_delayed` | boolean | no | Schwab chain-level delayed flag. |
| `underlying_price` | double | no | Chain-level underlying price. |
| `raw_payload` | string | yes | Full provider JSON. |
| `ingestion_ts` | timestamptz | yes | Write timestamp. |
| `ingestion_run_id` | string | yes | Batch/run identifier. |

Partitioning: `month(snapshot_ts)`.

Sort order: `underlying_symbol`, `snapshot_ts`.

### `options.schwab_chain_contracts`

Canonical contract snapshot table, one row per option contract per chain
snapshot. This is the primary replay/backtest dataset.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `underlying_symbol` | string | yes | Requested underlying. |
| `option_symbol` | string | yes | Provider/OCC-style option symbol as returned by Schwab. |
| `snapshot_ts` | timestamptz | yes | UTC capture time. |
| `put_call` | string | yes | `CALL` or `PUT`. |
| `expiration_date` | date | yes | Contract expiration. |
| `strike` | double | yes | Strike price. |
| `days_to_expiration` | int | no | Provider value at snapshot time. |
| `bid` | double | no | Bid price. |
| `ask` | double | no | Ask price. |
| `last` | double | no | Last trade price. |
| `mark` | double | no | Provider mark price. |
| `bid_size` | long | no | Contract bid size. |
| `ask_size` | long | no | Contract ask size. |
| `last_size` | long | no | Last trade size. |
| `volume` | long | no | Contract volume. |
| `open_interest` | long | no | Open interest. |
| `quote_time` | timestamptz | no | Provider quote timestamp if present. |
| `trade_time` | timestamptz | no | Provider trade timestamp if present. |
| `delta` | double | no | Greek. |
| `gamma` | double | no | Greek. |
| `theta` | double | no | Greek. |
| `vega` | double | no | Greek. |
| `rho` | double | no | Greek. |
| `volatility` | double | no | Provider volatility/IV field. |
| `theoretical_value` | double | no | Provider theoretical value when present. |
| `intrinsic_value` | double | no | Provider intrinsic value. |
| `time_value` | double | no | Provider time value. |
| `in_the_money` | boolean | no | Provider ITM flag. |
| `mini` | boolean | no | Mini option flag. |
| `non_standard` | boolean | no | Non-standard deliverable flag. |
| `penny_pilot` | boolean | no | Penny pilot flag. |
| `multiplier` | double | no | Contract multiplier. |
| `settlement_type` | string | no | Provider settlement type. |
| `expiration_type` | string | no | Provider expiration type. |
| `source` | string | yes | `schwab-chain`. |
| `ingestion_ts` | timestamptz | yes | Write timestamp. |
| `ingestion_run_id` | string | yes | Batch/run identifier. |

Identifier fields: `underlying_symbol`, `option_symbol`, `snapshot_ts`.

Partitioning: `bucket(16, underlying_symbol)`, `month(snapshot_ts)`.

Sort order: `underlying_symbol`, `expiration_date`, `strike`, `put_call`,
`snapshot_ts`.

Rationale: most reads filter by underlying plus a time range for replay, or by
underlying/expiration/strike for current-chain inspection. Bucketing avoids one
large hot partition if the watch universe grows, while keeping file fanout
lower than whole-market equities.

### `options.schwab_expirations`

Small reference table, one row per underlying expiration observed from Schwab.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `underlying_symbol` | string | yes | Requested underlying. |
| `expiration_date` | date | yes | Expiration date. |
| `days_to_expiration` | int | no | Provider value at observation time. |
| `expiration_type` | string | no | Weekly/monthly/quarterly when supplied. |
| `settlement_type` | string | no | Provider settlement type when supplied. |
| `source` | string | yes | `schwab-expirationchain`. |
| `observed_ts` | timestamptz | yes | UTC observation time. |
| `ingestion_ts` | timestamptz | yes | Write timestamp. |
| `ingestion_run_id` | string | yes | Batch/run identifier. |

Partitioning: `month(expiration_date)`.

Sort order: `underlying_symbol`, `expiration_date`, `observed_ts`.

### `options.gamma_exposure_snapshots`

Derived table computed from canonical chain snapshots. This table supports
GEX charts, alerts, backtests, simulations, and MCP reads without requiring a
third-party GEX provider.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `underlying_symbol` | string | yes | Underlying ticker. |
| `snapshot_ts` | timestamptz | yes | Source chain snapshot time. |
| `expiration_date` | date | no | Null for all-expiration totals. |
| `strike` | double | no | Null for all-strike totals. |
| `put_call` | string | no | `CALL`, `PUT`, or null for net rows. |
| `underlying_price` | double | yes | Price used in the calculation. |
| `gamma_exposure` | double | yes | Signed dollar exposure per 1% underlying move. |
| `call_gamma_exposure` | double | no | Positive call-side exposure total. |
| `put_gamma_exposure` | double | no | Negative put-side exposure total. |
| `net_gamma_exposure` | double | no | Net call plus put exposure. |
| `open_interest` | long | no | OI used for the aggregation. |
| `volume` | long | no | Volume used for volume-weighted views. |
| `contract_count` | long | no | Contracts included in the aggregation. |
| `aggregation_level` | string | yes | `total`, `strike`, `expiry`, or `strike_expiry`. |
| `methodology` | string | yes | Versioned calculation label. |
| `source` | string | yes | `stockalert-schwab-gex` initially. |
| `source_snapshot_id` | string | no | Iceberg snapshot ID for reproducibility. |
| `ingestion_ts` | timestamptz | yes | Write timestamp. |
| `ingestion_run_id` | string | yes | Batch/run identifier. |

Identifier fields: `underlying_symbol`, `snapshot_ts`, `aggregation_level`,
`expiration_date`, `strike`, `put_call`.

Partitioning: `bucket(16, underlying_symbol)`, `month(snapshot_ts)`.

Sort order: `underlying_symbol`, `snapshot_ts`, `aggregation_level`,
`expiration_date`, `strike`.

Initial calculation:

```text
unsigned_contract_gex =
  gamma * open_interest * multiplier * underlying_price * 0.01 * underlying_price

signed_contract_gex =
  unsigned_contract_gex for calls
  -unsigned_contract_gex for puts
```

Then aggregate by total, strike, expiry, and strike plus expiry. The method is
an approximation of spot gamma exposure from available chain data; it is not
intended to replicate Unusual Whales' proprietary methodology exactly.

### Future Optional Enrichment Tables

These tables are not part of O1-O5. They reserve clear landing zones if we add
Schwab option streaming, Unusual Whales, or another flow provider later.

`options.option_flow_trades`

- One row per option trade/print.
- Candidate fields: `trade_ts`, `underlying_symbol`, `option_symbol`,
  `put_call`, `expiration_date`, `strike`, `price`, `size`, `premium`,
  `exchange`, `condition`, `side`, `aggressor`, `bid`, `ask`, `mark`,
  `delta`, `gamma`, `vega`, `theta`, `open_interest`, `volume`, `source`,
  `raw_payload`.
- Enables unusual flow, sweeps/blocks, premium spikes, flow-derived Greek
  exposure, and replay of trade-triggered alerts.

`options.darkpool_trades`

- One row per off-lit/dark-pool equity print.
- Candidate fields: `trade_ts`, `symbol`, `price`, `size`, `notional`,
  `venue`, `condition`, `market_center`, `source`, `raw_payload`.
- Enables dark-pool overlays for option alerts and equity opportunity scans.

`options.option_contract_intraday`

- Intraday contract bars or volume profile by option symbol.
- Candidate fields: `option_symbol`, `timestamp`, `open`, `high`, `low`,
  `close`, `volume`, `vwap`, `trade_count`, `source`.
- Enables contract-level intraday charts and backtests beyond periodic chain
  snapshots.

`options.option_flow_aggregates`

- Derived flow aggregates by ticker, expiry, strike, side, interval, sector, or
  market.
- Candidate metrics: call/put premium, net premium, bullish/bearish premium,
  sweep premium, block premium, contract count, unique contract count, net
  delta/gamma/vega/theta flow.
- Enables market-tide/net-flow style scanners while preserving reproducibility.

`options.volatility_metrics`

- Derived volatility and skew metrics by underlying and timestamp.
- Candidate metrics: IV rank, IV percentile, interpolated IV, term structure,
  risk reversal skew, realized volatility, variance risk premium, volatility
  anomaly score, max pain.
- Enables volatility-aware alerts and simulations.

`options.provider_enrichment_raw`

- Append-only raw payload table for optional paid/third-party providers.
- Required columns: `provider`, `endpoint`, `request_params`, `snapshot_ts`,
  `raw_payload`, `ingestion_ts`, `ingestion_run_id`.
- Keeps third-party integrations auditable and reparseable without exposing
  provider-native shapes to application consumers.

## Hot Tier

ClickHouse is for current/recent option alerting, not long-term replay.

Initial hot tables:

- `option_chain_latest`: latest canonical contract row per
  `(underlying_symbol, option_symbol)`, using `ReplacingMergeTree(version)`.
- `option_chain_snapshots_recent`: optional recent intraday snapshots retained
  for fast alerts and dashboards. Iceberg remains the source for backtests.
- `option_levelone_latest`: latest streamed quote state per option contract.
- `option_book_latest`: latest streamed book state per option contract when
  `OPTIONS_BOOK` is enabled.
- `option_screener_events`: latest/rolling streamed option screener events
  from `SCREENER_OPTION`.

If first-phase scope needs to stay smaller, start with Iceberg-only plus API/MCP
lake reads, then add ClickHouse once alert latency requirements are concrete.

## Schwab Streaming Support

Schwab streaming is a hot-path complement to REST chain snapshots, not a
replacement. The system should support all three Schwab option streamer
services exposed in the repo constants:

- `LEVELONE_OPTIONS` — live level-one quote updates for selected option
  contracts. Use for low-latency bid/ask/last/mark-style monitoring and
  contract-specific alerts.
- `OPTIONS_BOOK` — live option book/depth updates where Schwab entitlement and
  field support allow it. Use for spread/depth/liquidity-sensitive alerts.
- `SCREENER_OPTION` — streamed option screener keys such as call/put/all
  advances/decliners or volume/change views. Use for discovery and watchlist
  expansion.

Streaming requires a contract universe. The first implementation should derive
stream subscriptions from REST snapshots and scanner rules:

- chain snapshot discovers contracts, expirations, strikes, Greeks, and open
  interest;
- GEX/scanner rules select contracts or strikes worth watching;
- streamer subscribes to the selected option symbols;
- ClickHouse stores latest/recent quote/book/screener state for alerts;
- Iceberg chain/GEX snapshots remain the replayable source for simulations.

Key difference from 5-minute snapshots:

- REST snapshots are broad, replayable, and metadata-rich, but periodic.
- Streaming is narrow, low-latency, and event/update driven, but only for
  subscribed contracts/services and may not carry open interest or full-chain
  metadata on each update.

Implementation must empirically validate Schwab option streaming field IDs
before production use, the same way `CHART_EQUITY` fields were validated for
equity bars. Unknown fields must be logged and preserved in raw event payloads
until the canonical mapping is verified.

## Ingestion Behavior

### Universe

The first ingestion universe is configurable:

- Explicit symbols from a CLI argument or config.
- Active equity watchlist/universe for scheduled jobs.
- Future scanner-selected underlyings from the screener service.

Symbols are normalized uppercase before provider calls. Failures are recorded
per underlying and do not abort unrelated symbols.

### Schwab Query Defaults

Default first-phase chain parameters:

- `contractType=ALL`
- `strikeCount=20`
- `includeUnderlyingQuote=true`
- `strategy=SINGLE`
- Optional `fromDate` / `toDate` for near-term expiration windows.

These defaults are intentionally conservative. Broader chains can explode row
counts and provider calls; scans should request only the expirations/strikes
needed for the alert or simulation use case.

### Cadence

Suggested starting cadences:

- Expiration chain: once daily per underlying.
- Chain snapshots: every 5 minutes during regular market hours for configured
  watch symbols.
- High-priority underlyings: optionally every 1 minute after rate-limit and
  storage behavior are measured.

All jobs log zero-row outcomes, per-underlying completion markers, provider
errors, rows parsed, rows written, and run summary. Predictable failures return
result objects with `ok`, `skipped`, or `error`.

## Backtests and Simulated Trading

Backtests and simulation read `options.schwab_chain_contracts` by:

- underlying universe,
- snapshot time range,
- expiration range,
- delta/strike/moneyness filters,
- put/call side,
- minimum volume/open-interest/liquidity thresholds.

Simulation must use the chain snapshot available at or before the simulated
decision timestamp. It must not look ahead to later Greeks, open interest,
quotes, or expiration lists.

Each simulation run records:

- Iceberg table snapshot ID(s),
- option reader parameters,
- underlying price source and snapshot IDs,
- alert/strategy version,
- run timestamp and code version.

This preserves the platform replay requirement.

## Alerts and Opportunity Scans

First alert/scanner use cases the schema must support:

- unusual option volume versus open interest,
- tight spread and liquidity filters,
- delta/expiration-targeted contract discovery,
- IV/volatility change between snapshots,
- positive/negative GEX regime detection,
- largest GEX by strike, expiry, and strike plus expiry,
- gamma wall / volatility amplification zones near spot price,
- call/put skew and put-call activity,
- contract price/underlying divergence,
- upcoming expiration opportunity scans.

Alert services consume canonical reader DTOs, not provider payloads. The alert
path can choose ClickHouse latest/recent for low latency or Iceberg snapshots
for replayable scans.

## Agent, API, and MCP Surfaces

Add thin adapters over a shared options reader/service:

- HTTP:
  - `GET /api/v1/options/chain/{underlying}`
  - `GET /api/v1/options/contracts`
  - `GET /api/v1/options/expirations/{underlying}`
  - `GET /api/v1/options/coverage`
- MCP:
  - `get_option_chain`
  - `search_option_contracts`
  - `get_option_expirations`
  - `get_gamma_exposure`
  - `get_gamma_exposure_levels`
  - `get_options_coverage`

MCP tools return Pydantic-shaped data and expose the same filters as HTTP.
No MCP tool calls Schwab directly in normal operation; direct provider calls
remain diagnostics or ingestion-only.

## Testing

Unit tests:

- Pydantic schema validation and timestamp normalization.
- Schwab chain flattening from fixture payloads.
- GEX calculation from fixture contracts, including call/put sign handling.
- Empty chain and zero-contract outcomes.
- Per-underlying result objects for `ok`, `skipped`, and `error`.
- Idempotent sink behavior against in-memory/fake table boundaries where
  possible.

Contract tests:

- Options reader, HTTP route, and MCP tool return equivalent DTOs for the same
  fixture-backed service.
- Backtest-style reads never return records after the requested as-of time.

Integration tests:

- Live Schwab chain/expiration smoke test, marked `integration`.
- Iceberg table write/read verification with a fresh catalog/client.
- Optional ClickHouse hot-table write/read verification gated by
  `clickhouse_ready`.

## Delivery Phases

### O1 — Spec and Fixture Contract

- Approve this spec.
- Add fixture Schwab chain payloads.
- Add canonical Pydantic DTOs and parser tests.

### O2 — Lake Tables and Sink

- Add `app/services/options/{schemas,tables,sink,README}.py`.
- Create `options.schwab_chain_raw`, `options.schwab_chain_contracts`, and
  `options.schwab_expirations`.
- Verify Iceberg writes by re-reading through a fresh catalog.

### O3 — Schwab Snapshot Ingest

- Add a CLI/scheduled job for option-chain snapshots.
- Add per-underlying result summaries and loud zero-row logging.
- Persist raw and canonical rows in one run.

### O4 — Reader, API, and MCP

- Add options reader contracts.
- Add HTTP routes.
- Add MCP tools with route/tool parity tests.

### O5 — Alerts and Simulation Integration

- Add scanner filters over canonical option snapshots.
- Add derived GEX calculations and reader filters.
- Add backtest/simulation reader adapters with snapshot pinning.
- Add first alert rules after liquidity and cadence are validated.

### O6 — Optional Unusual Whales Provider

- Evaluate Unusual Whales API/MCP only after Schwab-derived GEX is working.
- If approved, ingest into provider-specific raw tables plus canonical
  enrichment tables.
- Prioritize gaps that Schwab snapshots cannot provide: option trade flow,
  dark pool, Greek flow, market tide/net flow, volatility metrics, and
  historical full-market option trades.
- Keep alerts, simulation, API, and MCP on StockAlert canonical readers.

### O7 — Streaming Hot Path

- Implement Schwab `LEVELONE_OPTIONS`, `OPTIONS_BOOK`, and `SCREENER_OPTION`.
- Derive subscriptions from chain snapshots, GEX levels, and scanner rules.
- Validate streamer field IDs against live Schwab payloads before relying on
  canonical mappings.
- Feed ClickHouse latest/recent quote, book, and screener tables while keeping
  Iceberg snapshots as the replay source.

## Open Questions

1. What is the initial options universe: active watchlist, a fixed list, or a
   top-liquidity equity universe?
2. What first cadence is acceptable for storage and provider limits: 1 minute,
   5 minutes, or daily-only snapshots?
3. Should O2 include ClickHouse hot tables immediately, or should O2/O3 be
   Iceberg-only until alert latency requirements are proven?
4. Do we want to include index options such as SPX/NDX in the first phase, or
   equity/ETF underlyings only?
5. Should the first GEX implementation compute exposure from open interest
   only, or store both open-interest-based and volume-based GEX views?
