"""Per-user watchlists with pretend positions.

Each member row carries its own pretend position (quantity + entry price
stamped at add time). Returns are computed at READ time against latest
prices — no derived/stale columns stored (lean-silver rule).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_05"
down_revision: str | None = "20260621_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_watchlists_user_name"),
    )
    op.create_table(
        "watchlist_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("watchlist_id", sa.Uuid(),
                  sa.ForeignKey("watchlists.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="100"),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_members_symbol"),
    )


def downgrade() -> None:
    op.drop_table("watchlist_members")
    op.drop_table("watchlists")
