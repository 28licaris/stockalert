"""ClickHouse client factory (clickhouse-connect).

clickhouse-connect clients are not thread-safe for concurrent queries; the driver
explicitly requires one client per thread. We keep one client per OS thread
(thread-local) so the FastAPI threadpool (used by `asyncio.to_thread`) can run
multiple synchronous queries in parallel without colliding.
"""
from __future__ import annotations

import threading
from typing import Optional

import clickhouse_connect
from clickhouse_connect.driver import Client

from app.config import settings


_tls = threading.local()
_lock = threading.Lock()
_all_clients: list[Client] = []


def _new_client() -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database=settings.clickhouse_database,
    )


def get_client() -> Client:
    """Return the calling thread's ClickHouse client, creating it on first use."""
    client: Optional[Client] = getattr(_tls, "client", None)
    if client is None:
        client = _new_client()
        _tls.client = client
        with _lock:
            _all_clients.append(client)
    return client


def get_admin_client() -> Client:
    """Connect without target database (for CREATE DATABASE). Caller closes it."""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password or "",
        database="default",
    )


def close_client() -> None:
    """Close every client created in any thread (called from app shutdown)."""
    with _lock:
        clients, _all_clients[:] = list(_all_clients), []
    for c in clients:
        try:
            c.close()
        except Exception:
            pass


def ping() -> bool:
    try:
        get_client().command("SELECT 1")
        return True
    except Exception:
        return False
