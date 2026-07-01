"""
Strategy-library service: register/list/get definitions (owner), serve REDACTED
public cards + actionable alerts (subscribers), and back every save up to S3.

Track record + alerts are read from the linked paper run (same `name`) via the
paper service — so the library is a metadata + redaction + backup layer over the
honest forward record, never a second copy of the strategy logic.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.sim.library.schemas import (
    BackupResult, StrategyAlert, StrategyDefinition, StrategyOwnerStats, StrategyPublic,
)
from app.services.sim.paper.service import build_status, load_state

logger = logging.getLogger(__name__)


def _lib_dir() -> Path:
    d = Path(os.environ.get("STOCKALERT_STRATEGY_DIR", Path.cwd() / "data" / "strategies"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return _lib_dir() / f"{safe}.json"


# ── persistence + S3 backup ──────────────────────────────────────────

def _backup_to_s3(definition: StrategyDefinition, stamp: str) -> tuple[Optional[str], Optional[str]]:
    """Write the FULL definition to S3 for safety. Returns (s3_uri, error)."""
    from app.config import settings

    bucket = getattr(settings, "stock_lake_bucket", "") or ""
    if not bucket:
        return None, "STOCK_LAKE_BUCKET not set — S3 backup skipped"
    key = f"strategies/{definition.name}/v{definition.version}_{stamp}.json"
    try:
        import boto3
        client = boto3.client("s3", region_name=getattr(settings, "stock_lake_region", "us-east-1"))
        client.put_object(
            Bucket=bucket, Key=key,
            Body=definition.model_dump_json(indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        uri = f"s3://{bucket}/{key}"
        logger.info("strategy backup → %s", uri)
        return uri, None
    except Exception as exc:  # noqa: BLE001 — surface, never silently drop a backup
        logger.warning("strategy S3 backup failed for %s: %s", definition.name, exc)
        return None, str(exc)


def _ch_save_definition(definition: StrategyDefinition, now: datetime) -> None:
    """Upsert the definition into ClickHouse (durable source of truth).
    Best-effort: a CH hiccup logs but never fails registration (local + S3
    copies still exist)."""
    try:
        from app.db.client import get_client

        get_client().insert(
            "strategy_definitions",
            [[
                definition.name,
                int(definition.version or 1),
                definition.title or "",
                definition.visibility or "subscribers",
                definition.model_dump_json(),
                now,
                int(now.timestamp() * 1000),   # row_version — latest wins
            ]],
            column_names=[
                "name", "version", "title", "visibility",
                "definition_json", "updated_at", "row_version",
            ],
        )
    except Exception as exc:  # noqa: BLE001 — CH unavailable; local/S3 remain
        logger.warning("strategy CH write failed for %s: %s", definition.name, exc)


def register(definition: StrategyDefinition) -> BackupResult:
    """Persist a definition to ClickHouse (durable) + locally (cache) + S3 (backup)."""
    now = datetime.now(timezone.utc)
    if definition.created_at is None:
        definition.created_at = now
    p = _path(definition.name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(definition.model_dump_json(indent=2))
    tmp.replace(p)  # atomic local cache
    _ch_save_definition(definition, now)  # durable copy
    s3_uri, s3_err = _backup_to_s3(definition, now.strftime("%Y%m%dT%H%M%SZ"))
    return BackupResult(local_path=str(p), s3_uri=s3_uri, s3_error=s3_err)


def _ch_load_definition(name: str) -> Optional[StrategyDefinition]:
    try:
        from app.db.client import get_client

        rows = get_client().query(
            "SELECT definition_json FROM strategy_definitions FINAL "
            "WHERE name = {n:String} LIMIT 1",
            parameters={"n": name},
        ).result_rows
        if rows and rows[0][0]:
            return StrategyDefinition.model_validate_json(rows[0][0])
    except Exception as exc:  # noqa: BLE001 — caller falls back to local file
        logger.warning("strategy CH read failed for %s: %s", name, exc)
    return None


def _ch_list_definitions() -> list[StrategyDefinition]:
    try:
        from app.db.client import get_client

        rows = get_client().query(
            "SELECT definition_json FROM strategy_definitions FINAL ORDER BY name"
        ).result_rows
        out: list[StrategyDefinition] = []
        for (blob,) in rows:
            try:
                out.append(StrategyDefinition.model_validate_json(blob))
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning("skip bad strategy row: %s", exc)
        return out
    except Exception as exc:  # noqa: BLE001 — caller falls back to local files
        logger.warning("strategy CH list failed: %s", exc)
        return []


def load_definition(name: str) -> Optional[StrategyDefinition]:
    # CH is the durable source of truth; fall back to the local cache.
    d = _ch_load_definition(name)
    if d is not None:
        return d
    p = _path(name)
    if not p.exists():
        return None
    return StrategyDefinition.model_validate_json(p.read_text())


def list_definitions() -> list[StrategyDefinition]:
    # Prefer ClickHouse (durable); fall back to local files if CH is
    # unavailable or empty (e.g. first run before any CH write).
    ch_out = _ch_list_definitions()
    if ch_out:
        return ch_out
    out = []
    for f in sorted(_lib_dir().glob("*.json")):
        try:
            out.append(StrategyDefinition.model_validate_json(f.read_text()))
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("skip bad strategy file %s: %s", f, exc)
    return out


# ── subscriber views (REDACTED — no config) ──────────────────────────

def to_public(definition: StrategyDefinition) -> StrategyPublic:
    """Build the redacted card. Reads the live paper track record for results."""
    pub = StrategyPublic(
        name=definition.name, title=definition.title, tagline=definition.tagline,
        description=definition.description, category=definition.category,
        version=definition.version, visibility=definition.visibility,
    )
    state = load_state(definition.name)
    if state is not None:
        s = build_status(state)
        pub.inception = s.go_live
        pub.days_live = s.days_live
        pub.forward_return = s.forward_return
        pub.forward_win_rate = s.forward_win_rate
        pub.forward_n_trades = s.forward_n_trades
        pub.n_open_positions = s.n_open_positions
    return pub


def list_public() -> list[StrategyPublic]:
    return [to_public(d) for d in list_definitions() if d.visibility in ("subscribers", "public")]


def owner_stats(definition: StrategyDefinition) -> StrategyOwnerStats:
    """OWNER/dev view: full backtest metrics + the simulated (paper) summary."""
    out = StrategyOwnerStats(name=definition.name, title=definition.title)
    state = load_state(definition.name)
    if state is not None:
        out.backtest = state.metrics
        out.last_run_at = state.last_run_at
        out.computed_through = state.computed_through
        s = build_status(state)
        out.paper_return = s.forward_return
        out.paper_win_rate = s.forward_win_rate
        out.paper_trades = s.forward_n_trades
        out.paper_days = s.days_live
        out.starting_capital = s.starting_capital
        out.current_balance = s.current_balance
    return out


def get_alerts(name: str, start: Optional[datetime] = None) -> list[StrategyAlert]:
    """Actionable alerts for subscribers: current open positions (with entry/stop/
    target) + recent closed trades (with P&L). No recipe."""
    state = load_state(name)
    if state is None:
        return []
    s = build_status(state, start=start)
    alerts: list[StrategyAlert] = []
    for p in s.open_positions:
        alerts.append(StrategyAlert(
            symbol=p.symbol, direction="long" if p.quantity >= 0 else "short", status="open",
            date=p.entry_time, entry=p.avg_entry_price, stop=p.stop_price,
            target=p.target_price, current=p.current_price,
        ))
    for t in reversed([t for t in s.forward_trades if t.is_closing][-25:]):
        alerts.append(StrategyAlert(
            symbol=t.symbol, direction=("long" if t.side == "sell" else "short"),
            status="closed", date=t.exit_date or t.timestamp,
            entry=t.entry_price, exit=t.exit_price, pnl=t.realized_pnl,
        ))
    return alerts
