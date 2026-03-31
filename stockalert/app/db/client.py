"""ClickHouse client singleton (clickhouse-connect)."""
from __future__ import annotations

import threading
from typing import Optional

import clickhouse_connect

from app.config import settings

_client: Optional[clickhouse_connect.driver.Client] = None
_lock = threading.Lock()


def get_client() -> clickhouse_connect.driver.Client:
    global _client
    with _lock:
        if _client is None:
            _client = clickhouse_connect.get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password or "",
                database=settings.clickhouse_database,
            )
        return _client


def get_admin_client() -> clickhouse_connect.driver.Client:
    """Connect without target database (for CREATE DATABASE)."""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database="default",
    )


def close_client() -> None:
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def ping() -> bool:
    try:
        get_client().command("SELECT 1")
        return True
    except Exception:
        return False
