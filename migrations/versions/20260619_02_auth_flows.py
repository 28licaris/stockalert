"""Add OAuth login transactions and session CSRF hashes.

Revision ID: 20260619_02
Revises: 20260619_01
Create Date: 2026-06-19
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260619_02"
down_revision: str | None = "20260619_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("csrf_token_hash", sa.String(length=64), nullable=True)
    )
    # No production sessions exist before auth is enabled. The temporary value
    # makes the migration safe if a developer exercised the foundation schema.
    op.execute("UPDATE sessions SET csrf_token_hash = repeat('0', 64)")
    op.alter_column("sessions", "csrf_token_hash", nullable=False)
    op.create_table(
        "auth_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("return_to", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        "ix_auth_transactions_expires_at", "auth_transactions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_transactions_expires_at", table_name="auth_transactions")
    op.drop_table("auth_transactions")
    op.drop_column("sessions", "csrf_token_hash")
