"""ClickHouse store for sector-rotation themes — the data layer that makes
themes editable at runtime (API / MCP / UI) instead of hardcoded.

Pure data layer (no business logic; onboarding/universe-sync lives in the
service). Mirrors `app/db/watchlist_repo.py`: `ReplacingMergeTree(version)`,
soft-delete via `is_active=0`, reads use `FINAL`. The table is tiny so `FINAL`
is cheap and correct.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from app.db.client import get_client
from app.services.sectors.schemas import ThemeRecord

_TABLE = "sector_themes"


def _now_version() -> int:
    return time.time_ns() // 1_000_000


def slugify(name: str) -> str:
    """'Copper Miners' → 'copper-miners' (stable theme_id)."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not s:
        raise ValueError("theme name must contain alphanumerics")
    return s


def default_label(name: str) -> str:
    """Short chart label from a name (≤10 chars). 'Copper Miners' → 'Copper Mnr'."""
    n = (name or "").strip()
    if len(n) <= 10:
        return n
    return n.replace("Miners", "Mnr").replace("miners", "Mnr")[:12].strip()


def normalize_members(members) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in members or []:
        mm = (m or "").strip().upper()
        if mm and mm not in seen:
            seen.add(mm)
            out.append(mm)
    return out


def _row_to_record(r) -> ThemeRecord:
    weights = {}
    if r[4]:
        try:
            weights = {k: float(v) for k, v in json.loads(r[4]).items()}
        except (ValueError, TypeError):
            weights = {}
    return ThemeRecord(
        theme_id=r[0], name=r[1], label=r[2], members=list(r[3]),
        weights=weights, benchmark=r[5], is_active=bool(r[6]),
    )


def list_themes(include_inactive: bool = False) -> list[ThemeRecord]:
    client = get_client()
    where = "" if include_inactive else "WHERE is_active = 1"
    res = client.query(
        f"""
        SELECT theme_id, name, label, members, weights, benchmark, is_active
        FROM {_TABLE} FINAL
        {where}
        ORDER BY name
        """
    )
    return [_row_to_record(r) for r in res.result_rows]


def get_theme(theme_id: str) -> Optional[ThemeRecord]:
    client = get_client()
    res = client.query(
        f"""
        SELECT theme_id, name, label, members, weights, benchmark, is_active
        FROM {_TABLE} FINAL
        WHERE theme_id = {{tid:String}}
        """,
        parameters={"tid": theme_id},
    )
    if not res.result_rows:
        return None
    return _row_to_record(res.result_rows[0])


def upsert_theme(
    *,
    name: str,
    members: list[str],
    label: str | None = None,
    weights: dict | None = None,
    benchmark: str = "SPY",
    created_by: str = "",
    theme_id: str | None = None,
) -> ThemeRecord:
    """Create or replace a theme (idempotent on theme_id). Returns the row."""
    tid = (theme_id or slugify(name)).strip()
    syms = normalize_members(members)
    if not syms:
        raise ValueError("theme must have at least one member")
    lbl = (label or default_label(name)).strip()
    wjson = json.dumps({k.upper(): float(v) for k, v in (weights or {}).items()}) if weights else ""

    client = get_client()
    client.insert(
        _TABLE,
        [[tid, name.strip(), lbl, syms, wjson, (benchmark or "SPY").strip().upper(),
          created_by, 1, _now_version()]],
        column_names=["theme_id", "name", "label", "members", "weights",
                      "benchmark", "created_by", "is_active", "version"],
    )
    rec = get_theme(tid)
    assert rec is not None  # just inserted
    return rec


def delete_theme(theme_id: str) -> bool:
    """Soft-delete (is_active=0). Does NOT touch stream_universe — constituents
    stay tracked. Returns True if the theme existed."""
    existing = get_theme(theme_id)
    if existing is None:
        return False
    wjson = json.dumps(existing.weights) if existing.weights else ""
    client = get_client()
    client.insert(
        _TABLE,
        [[existing.theme_id, existing.name, existing.label, existing.members,
          wjson, existing.benchmark, "", 0, _now_version()]],
        column_names=["theme_id", "name", "label", "members", "weights",
                      "benchmark", "created_by", "is_active", "version"],
    )
    return True
