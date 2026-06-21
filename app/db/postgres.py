"""PostgreSQL engine and session-factory wiring for customer identity data.

This module owns infrastructure construction only. Domain services receive a
repository through their public Protocol and never import SQLAlchemy.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_identity_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a PostgreSQL engine with conservative connection health checks."""
    if not database_url.strip():
        raise ValueError("IDENTITY_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("IDENTITY_DATABASE_URL must use PostgreSQL")
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def create_identity_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build the injectable unit-of-work factory used by repositories."""
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@lru_cache(maxsize=1)
def get_identity_engine() -> Engine:
    """Return the process-wide engine, constructed lazily from settings."""
    from app.config import settings

    return create_identity_engine(settings.identity_database_url)


@lru_cache(maxsize=1)
def get_identity_session_factory() -> sessionmaker[Session]:
    return create_identity_session_factory(get_identity_engine())


def close_identity_engine() -> None:
    """Dispose pooled PostgreSQL connections and reset lazy factories."""
    if get_identity_engine.cache_info().currsize:
        get_identity_engine().dispose()
    get_identity_session_factory.cache_clear()
    get_identity_engine.cache_clear()
