"""Repository-level test for renaming: real SQLAlchemy models on in-memory SQLite.

Proves the UPDATE is keyed by the session's user id only (no way to rename
another user) and that disabled users cannot be renamed.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.identity.models import IdentityBase, UserModel
from app.services.identity.repository import PostgresIdentityRepository
from app.services.identity.schemas import Principal, Role


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    IdentityBase.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _user(factory, email: str, name: str, status: str = "active") -> UserModel:
    with factory() as db:
        user = UserModel(email=email, normalized_email=email, display_name=name, status=status)
        db.add(user)
        db.commit()
        return user


def _principal(user: UserModel) -> Principal:
    return Principal(
        user_id=user.id,
        tenant_id=uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
    )


def _name(factory, user_id) -> str:
    with factory() as db:
        return db.scalar(select(UserModel.display_name).where(UserModel.id == user_id))


def test_rename_updates_only_the_session_user(factory) -> None:
    me = _user(factory, "me@example.com", "e428d408-b0d1-7002-c1a2-000000000000")
    other = _user(factory, "other@example.com", "Other Person")
    repo = PostgresIdentityRepository(session_factory=factory)

    result = repo.update_display_name(_principal(me), "Shane")

    assert result is not None and result.display_name == "Shane"
    assert result.email == "me@example.com"
    assert _name(factory, me.id) == "Shane"
    assert _name(factory, other.id) == "Other Person"


def test_rename_unknown_or_disabled_user_returns_none(factory) -> None:
    gone = _user(factory, "gone@example.com", "Gone", status="disabled")
    repo = PostgresIdentityRepository(session_factory=factory)

    assert repo.update_display_name(_principal(gone), "Nope") is None
    assert _name(factory, gone.id) == "Gone"

    ghost = Principal(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), roles=frozenset({Role.OWNER})
    )
    assert repo.update_display_name(ghost, "Nope") is None
