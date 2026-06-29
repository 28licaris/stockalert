"""Idempotent ClickHouse DDL (safe to run on every startup)."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.config import settings
from app.db.client import get_admin_client, get_client

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_NAME = "default"
LEGACY_WATCHLIST_JSON = "data/watchlist.json"


def _migrate_seed_to_stream_universe(client) -> None:
    """One-shot: rename `seed_universe` -> `stream_universe` if needed.

    Pre-FE-CONTRACTS-4-final the table was named `seed_universe`. The
    sticky-universe model now owns the streaming subscription set, so
    the name was updated to reflect its actual role.

    Idempotent:
      - if `stream_universe` already exists, no-op.
      - if `seed_universe` exists and `stream_universe` does not,
        rename in place (data preserved).
      - if neither exists, no-op (the CREATE TABLE below will create
        `stream_universe` fresh).
    """
    stream_exists = client.command("EXISTS TABLE stream_universe") == 1
    if stream_exists:
        return
    seed_exists = client.command("EXISTS TABLE seed_universe") == 1
    if not seed_exists:
        return
    client.command("RENAME TABLE seed_universe TO stream_universe")
    logger.info("Renamed CH table seed_universe -> stream_universe")


def init_schema() -> None:
    db = settings.clickhouse_database
    admin = get_admin_client()
    admin.command(f"CREATE DATABASE IF NOT EXISTS `{db}`")
    admin.close()

    client = get_client()

    _migrate_seed_to_stream_universe(client)

    client.command(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_1m (
            symbol        LowCardinality(String),
            timestamp     DateTime64(3, 'UTC'),
            open          Float64,
            high          Float64,
            low           Float64,
            close         Float64,
            volume        Float64,
            vwap          Float64 DEFAULT 0,
            trade_count   UInt32 DEFAULT 0,
            source        LowCardinality(String) DEFAULT '',
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (symbol, timestamp)
        SETTINGS index_granularity = 8192
        """
    )

    # NOTE: ohlcv_5m and ohlcv_daily were retired — all chart timeframes
    # (5m/15m/30m/1h/1d) are resampled on read from ohlcv_1m, the single CH
    # source of truth. See docs/architecture_v2/02_schema.md.

    client.command(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id                UUID DEFAULT generateUUIDv4(),
            symbol            LowCardinality(String),
            signal_type       LowCardinality(String),
            indicator         LowCardinality(String),
            ts_signal         DateTime64(3, 'UTC'),
            price_at_signal   Float64,
            indicator_value   Float64,
            p1_ts             DateTime64(3, 'UTC'),
            p2_ts             DateTime64(3, 'UTC')
        )
        ENGINE = MergeTree()
        PARTITION BY toYYYYMM(ts_signal)
        ORDER BY (symbol, ts_signal, id)
        SETTINGS index_granularity = 8192
        """
    )

    # Watchlists: soft-deleted via `is_active`. We never DROP rows so an LLM/agent
    # can later query "what was in this watchlist on 2026-05-01?". `kind` lets us
    # distinguish user-created lists from the always-streaming 'baseline' and
    # the auto-managed 'adhoc' list of symbol-page visits.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS watchlists (
            name        LowCardinality(String),
            kind        LowCardinality(String) DEFAULT 'user',
            description String DEFAULT '',
            is_active   UInt8 DEFAULT 1,
            updated_at  DateTime64(3, 'UTC') DEFAULT now64(3),
            version     UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (name)
        SETTINGS index_granularity = 8192
        """
    )

    client.command(
        """
        CREATE TABLE IF NOT EXISTS watchlist_members (
            watchlist_name LowCardinality(String),
            symbol         LowCardinality(String),
            is_active      UInt8 DEFAULT 1,
            updated_at     DateTime64(3, 'UTC') DEFAULT now64(3),
            version        UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (watchlist_name, symbol)
        SETTINGS index_granularity = 8192
        """
    )

    # ─────────────────────────────────────────────────────────────────
    # Stream universe (FE-CONTRACTS-4) — operator-curated "always
    # streaming into ClickHouse 24/7" set. Per the locked sticky-
    # universe model in docs/frontend_api_contracts.md §10.1:
    #   - StreamService owns this table.
    #   - StreamService.add subscribes to Schwab + writes a row.
    #   - StreamService.remove unsubscribes + marks is_active=0.
    #   - Watchlist add auto-extends via StreamService.ensure_streaming.
    #   - Watchlist remove does NOT affect this table (sticky).
    # owner_id stamps every row so multi-tenant SaaS day is a pure
    # backfill of the column, not a schema rewrite.
    # ─────────────────────────────────────────────────────────────────
    client.command(
        """
        CREATE TABLE IF NOT EXISTS stream_universe (
            symbol      LowCardinality(String),
            owner_id    LowCardinality(String) DEFAULT 'default-tenant',
            asset_type  LowCardinality(String) DEFAULT '',
            added_at    DateTime64(3, 'UTC') DEFAULT now64(3),
            added_by    LowCardinality(String) DEFAULT '',
            notes       String DEFAULT '',
            is_active   UInt8 DEFAULT 1,
            version     UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (owner_id, symbol)
        SETTINGS index_granularity = 8192
        """
    )

    # ─────────────────────────────────────────────────────────────────
    # Sector-rotation themes — data-driven thematic baskets shown on the
    # Sectors page. Editable at runtime (API / MCP / UI) instead of in
    # code, so an agent or operator can add a theme without a deploy.
    # ThemeStore owns this table; creating a theme also reconciles its
    # constituents into stream_universe (never prunes). Soft-delete via
    # is_active=0 keeps history. `members` is the holdings list; `weights`
    # is a JSON map (empty ⇒ equal weight).
    # ─────────────────────────────────────────────────────────────────
    client.command(
        """
        CREATE TABLE IF NOT EXISTS sector_themes (
            theme_id    LowCardinality(String),
            name        String DEFAULT '',
            label       String DEFAULT '',
            members     Array(LowCardinality(String)),
            weights     String DEFAULT '',
            benchmark   LowCardinality(String) DEFAULT 'SPY',
            created_by  LowCardinality(String) DEFAULT '',
            is_active   UInt8 DEFAULT 1,
            updated_at  DateTime64(3, 'UTC') DEFAULT now64(3),
            version     UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (theme_id)
        SETTINGS index_granularity = 8192
        """
    )

    # ---------- Futures (separate hot table; CME futures, continuous roots) ----------
    # Same shape as ohlcv_1m so the bar reader / resampler / gateway work
    # unchanged when pointed here. Kept separate from equities because
    # futures have different session/symbology semantics.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS futures_ohlcv_1m (
            symbol        LowCardinality(String),
            timestamp     DateTime64(3, 'UTC'),
            open          Float64,
            high          Float64,
            low           Float64,
            close         Float64,
            volume        Float64,
            vwap          Float64 DEFAULT 0,
            trade_count   UInt32 DEFAULT 0,
            source        LowCardinality(String) DEFAULT '',
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (symbol, timestamp)
        SETTINGS index_granularity = 8192
        """
    )

    # Futures universe — the continuous roots we stream + monitor. Mirrors
    # stream_universe so the same add/remove/list plumbing applies.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS futures_universe (
            symbol      LowCardinality(String),
            owner_id    LowCardinality(String) DEFAULT 'default-tenant',
            asset_type  LowCardinality(String) DEFAULT 'FUTURE',
            added_at    DateTime64(3, 'UTC') DEFAULT now64(3),
            added_by    LowCardinality(String) DEFAULT '',
            notes       String DEFAULT '',
            is_active   UInt8 DEFAULT 1,
            version     UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (owner_id, symbol)
        SETTINGS index_granularity = 8192
        """
    )

    # ---------- Options hot tier ----------
    # Cache-only latest projections of the canonical options lake rows.
    # Iceberg remains the source of truth for replay/backtests; these
    # tables serve low-latency alerts, UI screens, and MCP "latest"
    # context reads.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS options_contracts_latest (
            underlying_symbol LowCardinality(String),
            option_symbol     String,
            snapshot_ts       DateTime64(3, 'UTC'),
            put_call          LowCardinality(String),
            expiration_date   Date,
            strike            Float64,
            underlying_price  Nullable(Float64),
            days_to_expiration Nullable(Int32),
            bid               Nullable(Float64),
            ask               Nullable(Float64),
            last              Nullable(Float64),
            mark              Nullable(Float64),
            volume            Nullable(UInt64),
            open_interest     Nullable(UInt64),
            delta             Nullable(Float64),
            gamma             Nullable(Float64),
            theta             Nullable(Float64),
            vega              Nullable(Float64),
            rho               Nullable(Float64),
            volatility        Nullable(Float64),
            in_the_money      Nullable(UInt8),
            multiplier        Nullable(Float64),
            source            LowCardinality(String) DEFAULT 'schwab-chain',
            ingestion_ts      DateTime64(3, 'UTC') DEFAULT now64(3),
            ingestion_run_id  String DEFAULT '',
            version           UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (underlying_symbol, option_symbol)
        SETTINGS index_granularity = 8192
        """
    )

    client.command(
        """
        CREATE TABLE IF NOT EXISTS options_gex_latest (
            underlying_symbol      LowCardinality(String),
            snapshot_ts            DateTime64(3, 'UTC'),
            aggregation_level      LowCardinality(String),
            level_key              String,
            expiration_date        Nullable(Date),
            strike                 Nullable(Float64),
            put_call               Nullable(String),
            underlying_price       Float64,
            gamma_exposure         Float64,
            call_gamma_exposure    Nullable(Float64),
            put_gamma_exposure     Nullable(Float64),
            net_gamma_exposure     Nullable(Float64),
            open_interest          Nullable(UInt64),
            volume                 Nullable(UInt64),
            contract_count         Nullable(UInt64),
            methodology            LowCardinality(String),
            source                 LowCardinality(String),
            source_snapshot_id     Nullable(String),
            ingestion_ts           DateTime64(3, 'UTC') DEFAULT now64(3),
            ingestion_run_id       String DEFAULT '',
            version                UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (underlying_symbol, aggregation_level, level_key)
        SETTINGS index_granularity = 8192
        """
    )

    # ---------- Journal (Phase 3) ----------
    # account_snapshots: timestamped balance snapshots from /accounts. One row
    # per (account_hash, snapshot_time). Useful for an equity curve later.
    # Keyed on account_hash (not the real account number) so we can publish
    # MCP queries without leaking account identifiers.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
            account_hash       LowCardinality(String),
            snapshot_time      DateTime64(3, 'UTC'),
            account_type       LowCardinality(String) DEFAULT '',
            is_day_trader      UInt8 DEFAULT 0,
            round_trips        Int32 DEFAULT 0,
            cash_balance       Float64 DEFAULT 0,
            liquidation_value  Float64 DEFAULT 0,
            long_market_value  Float64 DEFAULT 0,
            short_market_value Float64 DEFAULT 0,
            buying_power       Float64 DEFAULT 0,
            pending_deposits   Float64 DEFAULT 0,
            raw_json           String DEFAULT '',
            version            UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(snapshot_time)
        ORDER BY (account_hash, snapshot_time)
        SETTINGS index_granularity = 8192
        """
    )

    # trades: one row per Schwab `activityId` (single fill). `net_amount` is
    # signed: + for sell proceeds, - for buy outlay. `quantity` is always abs
    # share/contract count. `side` and `position_effect` are normalized.
    # Idempotency: ReplacingMergeTree on (account_hash, activity_id) so
    # resyncing the same Schwab payload is a safe no-op.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS trades (
            account_hash      LowCardinality(String),
            activity_id       UInt64,
            order_id          UInt64 DEFAULT 0,
            position_id       UInt64 DEFAULT 0,
            trade_time        DateTime64(3, 'UTC'),
            symbol            LowCardinality(String),
            asset_type        LowCardinality(String) DEFAULT 'EQUITY',
            side              LowCardinality(String) DEFAULT '',
            position_effect   LowCardinality(String) DEFAULT '',
            quantity          Float64 DEFAULT 0,
            price             Float64 DEFAULT 0,
            gross_amount      Float64 DEFAULT 0,
            fees              Float64 DEFAULT 0,
            net_amount        Float64 DEFAULT 0,
            status            LowCardinality(String) DEFAULT '',
            raw_json          String DEFAULT '',
            version           UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(trade_time)
        ORDER BY (account_hash, activity_id)
        SETTINGS index_granularity = 8192
        """
    )

    # trade_notes: user-authored annotations. Decoupled from the `trades`
    # table so a re-sync NEVER clobbers notes. Keyed the same way as `trades`
    # so a JOIN is cheap. `strategy` is LowCardinality for fast group-bys
    # in the per-strategy summary later.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS trade_notes (
            account_hash  LowCardinality(String),
            activity_id   UInt64,
            strategy      LowCardinality(String) DEFAULT '',
            tags          Array(LowCardinality(String)) DEFAULT [],
            note          String DEFAULT '',
            updated_at    DateTime64(3, 'UTC') DEFAULT now64(3),
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (account_hash, activity_id)
        SETTINGS index_granularity = 8192
        """
    )

    # CV23: lake_archive_watermarks table removed. Was the idempotency
    # ledger for LakeArchiveService (v1 raw-Parquet writer); both the
    # service and its WatermarkRepo consumer were deleted in CV19. The
    # existing prod table is a no-op orphan — the cutover-completion
    # checklist in docs/architecture_v2/07_runbook.md drops it.

    # Ingestion-run audit log (TA-5.7 + future ingest jobs).
    # One row per cycle / run of any ingest job (live_lake_writer,
    # nightly_equities_polygon_refresh, corp_actions_backfill, ...). The
    # operational-layer ledger: Iceberg MERGE INTO is the correctness
    # layer, this is "did the job actually run, when, with what scope,
    # did it succeed."
    #
    # Schema is intentionally generic across job_name so a single table
    # serves every ingest job. JSON columns hold per-job-specific detail
    # (per_provider_rows_written etc.).
    #
    # ReplacingMergeTree on `finished_at` so a retry/re-run on the same
    # run_id (extremely unlikely with UUIDs but defensive) cleanly
    # overwrites the prior row.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id                          String,
            job_name                        LowCardinality(String),
            started_at                      DateTime64(3, 'UTC'),
            finished_at                     DateTime64(3, 'UTC'),
            window_start                    DateTime64(3, 'UTC') DEFAULT toDateTime64(0, 3, 'UTC'),
            window_end                      DateTime64(3, 'UTC') DEFAULT toDateTime64(0, 3, 'UTC'),
            rows_written                    UInt64 DEFAULT 0,
            per_provider_rows_written_json  String DEFAULT '{}',
            per_provider_errors_json        String DEFAULT '',
            status                          LowCardinality(String) DEFAULT 'ok',
            version                         UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(started_at)
        ORDER BY (job_name, started_at, run_id)
        SETTINGS index_granularity = 8192
        """
    )

    # Backtest / agent run registry — one row per completed run.
    # Reproducibility-enabling fields: snapshot_id pins the Iceberg
    # data version; git_sha pins the code; strategy_params + config
    # pin the inputs. Same triple -> same metrics (verified by
    # test_backtester_is_deterministic).
    client.command(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id              UUID,
            started_at          DateTime64(3, 'UTC'),
            finished_at         DateTime64(3, 'UTC'),
            strategy_name       LowCardinality(String),
            strategy_version    String,
            strategy_params     String DEFAULT '{}',   -- JSON
            config              String DEFAULT '{}',   -- JSON
            snapshot_id         String DEFAULT '',     -- Iceberg snapshot, '' if CH-only path
            symbols             Array(String),
            interval            LowCardinality(String),
            start_date          Date,
            end_date            Date,
            starting_cash       Float64,
            total_return        Float64,
            annualized_return   Float64 DEFAULT 0,
            sharpe_ratio        Float64 DEFAULT 0,
            sortino_ratio       Float64 DEFAULT 0,
            max_drawdown        Float64 DEFAULT 0,
            win_rate            Float64 DEFAULT 0,
            profit_factor       Float64 DEFAULT 0,
            n_trades            UInt32,
            final_equity        Float64,
            metrics_full        String DEFAULT '{}',   -- JSON: full RunMetrics
            git_sha             String DEFAULT '',
            inserted_at         DateTime64(3, 'UTC') DEFAULT now64(3)
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(started_at)
        ORDER BY (started_at, strategy_name)
        SETTINGS index_granularity = 8192
        """
    )

    # ── Assistant conversations + turns ───────────────────────────────
    # ReplacingMergeTree on `version` for conversations so we can update
    # `updated_at`, `turn_count`, and `total_cost_usd` in place.
    # Turns are append-only (MergeTree), ordered by sequence for fast
    # ordered reads. `tool_calls_json` stores the serialised list of
    # ToolCall objects so the assistant can reconstruct full turn history.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS assistant_conversations (
            id              UUID,
            owner_id        LowCardinality(String),
            user_id         String,
            title           String DEFAULT '',
            created_at      DateTime64(3, 'UTC'),
            updated_at      DateTime64(3, 'UTC'),
            turn_count      UInt32 DEFAULT 0,
            total_cost_usd  Float64 DEFAULT 0,
            deleted_at      Nullable(DateTime64(3, 'UTC')) DEFAULT NULL,
            version         UInt64
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (owner_id, id)
        SETTINGS index_granularity = 8192
        """
    )

    client.command(
        """
        CREATE TABLE IF NOT EXISTS assistant_turns (
            id              UUID,
            conversation_id UUID,
            owner_id        LowCardinality(String),
            sequence        UInt32,
            role            LowCardinality(String),
            content         String DEFAULT '',
            tool_calls_json String DEFAULT '[]',
            model           String DEFAULT '',
            tokens_in       UInt32 DEFAULT 0,
            tokens_out      UInt32 DEFAULT 0,
            cost_usd        Float64 DEFAULT 0,
            cache_hit       Bool DEFAULT 0,
            created_at      DateTime64(3, 'UTC')
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(created_at)
        ORDER BY (owner_id, conversation_id, sequence)
        SETTINGS index_granularity = 8192
        """
    )

    # Market calendar events (FOMC, econ releases, OPEX, dividend/split
    # ex-dates). Unified across event types: macro events have symbol=''.
    # `external_id` dedups within a source; ReplacingMergeTree(version) lets
    # re-syncs overwrite cleanly. ORDER BY leads with event_date so the
    # calendar's "events on date D" read is index-pruned; symbol next so the
    # symbol page's "events for X" is also fast. See market_calendar_spec §12a.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            event_date     Date,
            event_time_et  String DEFAULT '',
            symbol         LowCardinality(String) DEFAULT '',
            event_type     LowCardinality(String),
            title          String DEFAULT '',
            importance     LowCardinality(String) DEFAULT 'medium',
            source         LowCardinality(String) DEFAULT '',
            external_id    String DEFAULT '',
            payload        String DEFAULT '{}',
            version        UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(event_date)
        ORDER BY (event_date, symbol, event_type, external_id)
        SETTINGS index_granularity = 8192
        """
    )

    # News feed — official-record items (SEC EDGAR filings, govt releases),
    # AI-summarized with a link to the source. `id` = EDGAR accession (or
    # source uid), the dedup key. `summary`/`why_it_matters`/`materiality` are
    # filled by LLM enrichment (enriched=1); '' until then. We never store the
    # source body — only our summary + the `url` link. See docs/news_alerts_spec.md.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS news_items (
            id              String,
            published_at    DateTime64(3, 'UTC'),
            ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3),
            source          LowCardinality(String) DEFAULT 'edgar',
            event_type      LowCardinality(String) DEFAULT '',
            symbol          LowCardinality(String) DEFAULT '',
            cik             String DEFAULT '',
            title           String DEFAULT '',
            url             String DEFAULT '',
            summary         String DEFAULT '',
            why_it_matters  String DEFAULT '',
            materiality     LowCardinality(String) DEFAULT 'unrated',
            sentiment       LowCardinality(String) DEFAULT '',
            enriched        UInt8 DEFAULT 0,
            version         UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(published_at)
        ORDER BY (published_at, source, id)
        SETTINGS index_granularity = 8192
        """
    )

    # Economic indicators — raw time series of government releases (BLS now;
    # BEA later). Source of truth for the Economic page + the AI; derived
    # figures (YoY, MoM change) are computed at read time (kept lean — not
    # stored). `period` = '2026-05' (monthly) / '2026-Q1' (quarterly); dedup on
    # (series_id, period). See docs/news_alerts_spec.md §14.
    client.command(
        """
        CREATE TABLE IF NOT EXISTS economic_data (
            series_id     LowCardinality(String),
            period        String,
            period_label  String DEFAULT '',
            value         Float64,
            source        LowCardinality(String) DEFAULT 'bls',
            ingested_at   DateTime64(3, 'UTC') DEFAULT now64(3),
            version       UInt64 DEFAULT 0
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (series_id, period)
        SETTINGS index_granularity = 8192
        """
    )


def _read_legacy_watchlist(path: str) -> Optional[list[str]]:
    """Best-effort read of the old `data/watchlist.json` file. Returns None on any error."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        raw = data.get("symbols", []) if isinstance(data, dict) else data
        return [str(s).strip().upper() for s in raw if str(s).strip()]
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("migrate_default_watchlist: could not read %s: %s", path, e)
        return None


def migrate_default_watchlist(json_path: str = LEGACY_WATCHLIST_JSON) -> dict:
    """
    One-shot migration: if `watchlists` is empty and `data/watchlist.json` exists,
    seed a `default` user watchlist with those symbols. Idempotent: a second call
    is a no-op because `watchlists` will no longer be empty.

    Returns a small audit dict so callers/tests can verify what happened.
    """
    # Import inside the function to avoid a circular import at module load time
    # (watchlist_repo imports from app.db.client which is imported above).
    from app.db import watchlist_repo

    existing = watchlist_repo.list_watchlists(include_inactive=True)
    if existing:
        return {"migrated": False, "reason": "watchlists table not empty", "count": len(existing)}

    symbols = _read_legacy_watchlist(json_path) or []
    watchlist_repo.create_watchlist(
        DEFAULT_WATCHLIST_NAME,
        kind="user",
        description="Migrated from data/watchlist.json on first startup.",
    )
    if symbols:
        watchlist_repo.add_members(DEFAULT_WATCHLIST_NAME, symbols)
        logger.info(
            "migrate_default_watchlist: seeded '%s' with %d symbols from %s",
            DEFAULT_WATCHLIST_NAME, len(symbols), json_path,
        )
    else:
        logger.info(
            "migrate_default_watchlist: created empty '%s' (no legacy file at %s)",
            DEFAULT_WATCHLIST_NAME, json_path,
        )
    return {
        "migrated": True,
        "watchlist": DEFAULT_WATCHLIST_NAME,
        "symbols": symbols,
        "source": json_path if symbols else None,
    }
