"""Schwab refresh-token AGE health check (regression for two silent expiries).

2026-07-03 and 2026-07-20 the refresh token expired unnoticed: the old
check only asserted the token was *present*, so the Status page stayed
green while the intraday tier froze for days — costing the hourly FOMC
paper strategy its first live meeting. These tests pin the age
thresholds, both token sources, and that the ledger never stores the
token itself.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.api import routes_health
from app.api.routes_health import (
    SCHWAB_TOKEN_ERROR_DAYS,
    SCHWAB_TOKEN_WARN_DAYS,
    _check_schwab,
    _schwab_token_age_days,
)


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    """Point settings at a temp token dir with creds configured."""
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_client_id", "cid", raising=False)
    monkeypatch.setattr(settings, "schwab_client_secret", "csec", raising=False)
    monkeypatch.setattr(
        settings, "schwab_refresh_token_file", str(tmp_path / ".schwab_refresh_token"),
        raising=False,
    )
    return tmp_path


def _age_to(tmp_path, days: float) -> None:
    """Backdate the env-token ledger to `days` old."""
    ledger = tmp_path / ".schwab_token_seen.json"
    state = json.loads(ledger.read_text())
    state["first_seen"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    ledger.write_text(json.dumps(state))


def test_env_token_ages_from_ledger_and_crosses_thresholds(token_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_refresh_token", "secret-token-value", raising=False)

    assert asyncio.run(_check_schwab()).state == "ok"  # first sight: fresh

    _age_to(token_env, SCHWAB_TOKEN_WARN_DAYS + 0.5)
    warn = asyncio.run(_check_schwab())
    assert warn.state == "warn" and "re-auth" in warn.detail

    _age_to(token_env, SCHWAB_TOKEN_ERROR_DAYS + 0.5)
    err = asyncio.run(_check_schwab())
    assert err.state == "error" and "EXPIRED" in err.detail


def test_ledger_never_stores_the_token(token_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_refresh_token", "super-secret-abc123", raising=False)
    asyncio.run(_check_schwab())
    body = (token_env / ".schwab_token_seen.json").read_text()
    assert "super-secret-abc123" not in body
    assert len(json.loads(body)["hash"]) == 16


def test_rotating_the_token_resets_the_clock(token_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_refresh_token", "old-token", raising=False)
    asyncio.run(_check_schwab())
    _age_to(token_env, SCHWAB_TOKEN_ERROR_DAYS + 1)
    assert asyncio.run(_check_schwab()).state == "error"

    monkeypatch.setattr(settings, "schwab_refresh_token", "new-token", raising=False)
    assert asyncio.run(_check_schwab()).state == "ok"


def test_file_token_ages_from_mtime(token_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_refresh_token", "", raising=False)
    path = token_env / ".schwab_refresh_token"
    path.write_text("file-token")
    old = time.time() - (SCHWAB_TOKEN_ERROR_DAYS + 1) * 86400
    os.utime(path, (old, old))

    age = _schwab_token_age_days()
    assert age is not None and age > SCHWAB_TOKEN_ERROR_DAYS
    assert asyncio.run(_check_schwab()).state == "error"


def test_missing_token_warns_not_errors(token_env, monkeypatch):
    from app.config import settings

    # no env token AND no token file -> get_schwab_refresh_token() == ""
    monkeypatch.setattr(settings, "schwab_refresh_token", "", raising=False)
    monkeypatch.setattr(
        settings, "schwab_refresh_token_file", str(token_env / "does-not-exist"),
        raising=False,
    )
    assert settings.get_schwab_refresh_token() == ""
    r = asyncio.run(_check_schwab())
    assert r.state == "warn" and "missing" in r.detail


def test_unwritable_ledger_degrades_to_unknown_age(token_env, monkeypatch, caplog):
    """No silent failure: unwritable ledger logs and reports age-unknown."""
    from app.config import settings

    monkeypatch.setattr(settings, "schwab_refresh_token", "tok", raising=False)
    monkeypatch.setattr(
        routes_health.Path, "write_text",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only fs")),
        raising=False,
    )
    with caplog.at_level("WARNING"):
        assert _schwab_token_age_days() is None
    assert any("ledger unwritable" in m for m in caplog.messages)
