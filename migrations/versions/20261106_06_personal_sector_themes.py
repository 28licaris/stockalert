"""Per-user sector-rotation themes.

A customer's own thematic baskets for the Sector Rotation page. Kept in the
identity PostgreSQL next to per-user watchlists (user-owned state lives with
users), NOT in the shared ClickHouse ``sector_themes`` table, and never
onboarded into the shared streaming universe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_06"
down_revision: str | None = "20260703_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sector_themes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "slug", name="uq_user_sector_themes_user_slug"),
    )


def downgrade() -> None:
    op.drop_table("user_sector_themes")
