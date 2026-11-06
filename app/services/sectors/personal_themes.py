"""Personal sector themes: a customer's own baskets on the Sector Rotation page.

Storage is the identity PostgreSQL (user-owned state lives with users, like
per-user watchlists). Every query is scoped by ``user_id``, so cross-user
access is structurally impossible here.

Deliberately NOT done for personal themes (unlike the shared, operator-managed
themes in ``service.create_theme``): the tickers are never added to the shared
streaming universe and no backfill is scheduled. Rotation only needs daily
closes, which the resolver reads from ClickHouse with its own lake fill on
demand; a ticker with no data yet surfaces as an ``excluded`` group with a
reason. That keeps the shared Schwab stream and the backfill queue under
operator control.

Limits (``MAX_THEMES_PER_USER`` / ``MAX_MEMBERS``) bound the per-request read
cost, since every page load resolves each member's daily series.
"""
from __future__ import annotations

import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker
from sqlalchemy.types import Uuid

from app.services.identity.models import IdentityBase, TimestampMixin
from app.services.sectors.schemas import RotationGroup

MAX_THEMES_PER_USER = 10
MAX_MEMBERS = 20
PERSONAL_ID_PREFIX = "my:"  # ':' can never appear in a shared theme id (slugify -> [a-z0-9-])

_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


class PersonalThemeError(ValueError):
    """A user-correctable problem (bad name/ticker, limit reached). Message is safe to show."""


# ── contracts ─────────────────────────────────────────────────────────


class PersonalThemeCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # pattern, not a custom validator: a ValueError in the 422 context is not
    # JSON-serialisable and would surface as a 500 (see UpdateDisplayNameRequest).
    name: str = Field(..., min_length=1, max_length=60, pattern=r"^[^\x00-\x1f\x7f]+$")
    members: list[str] = Field(..., min_length=1, max_length=MAX_MEMBERS * 2)
    label: str | None = Field(None, max_length=16, pattern=r"^[^\x00-\x1f\x7f]*$")


class PersonalTheme(BaseModel):
    theme_id: str = Field(..., description="Chart group id: 'my:<slug>'.")
    slug: str = Field(..., description="Use this in DELETE /my/sector-themes/{slug}.")
    name: str
    label: str
    members: list[str]


# ── storage ───────────────────────────────────────────────────────────


class UserSectorThemeModel(TimestampMixin, IdentityBase):
    __tablename__ = "user_sector_themes"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    members: Mapped[list[str]] = mapped_column(JSON(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_user_sector_themes_user_slug"),)


def _to_theme(row: UserSectorThemeModel) -> PersonalTheme:
    return PersonalTheme(
        theme_id=f"{PERSONAL_ID_PREFIX}{row.slug}",
        slug=row.slug,
        name=row.name,
        label=row.label,
        members=list(row.members),
    )


class PersonalThemesRepository:
    """Sessionmaker CRUD, all scoped by ``user_id``."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls) -> "PersonalThemesRepository":
        from app.db.postgres import get_identity_session_factory

        return cls(get_identity_session_factory())

    def list_for_user(self, user_id: UUID) -> list[PersonalTheme]:
        with self._session_factory() as db:
            rows = db.scalars(
                select(UserSectorThemeModel)
                .where(UserSectorThemeModel.user_id == user_id)
                .order_by(UserSectorThemeModel.created_at, UserSectorThemeModel.slug)
            ).all()
            return [_to_theme(r) for r in rows]

    def upsert(self, user_id: UUID, *, slug: str, name: str, label: str, members: list[str]) -> PersonalTheme:
        """Create, or replace the user's theme with the same slug. Enforces the
        per-user cap on *new* themes inside the same transaction."""
        with self._session_factory() as db:
            row = db.scalars(
                select(UserSectorThemeModel).where(
                    UserSectorThemeModel.user_id == user_id,
                    UserSectorThemeModel.slug == slug,
                )
            ).first()
            if row is None:
                count = len(
                    db.scalars(
                        select(UserSectorThemeModel.id).where(UserSectorThemeModel.user_id == user_id)
                    ).all()
                )
                if count >= MAX_THEMES_PER_USER:
                    raise PersonalThemeError(
                        f"You can keep up to {MAX_THEMES_PER_USER} themes. Remove one first."
                    )
                row = UserSectorThemeModel(user_id=user_id, slug=slug)
                db.add(row)
            row.name, row.label, row.members = name, label, list(members)
            db.commit()
            return _to_theme(row)

    def delete(self, user_id: UUID, slug: str) -> bool:
        with self._session_factory() as db:
            row = db.scalars(
                select(UserSectorThemeModel).where(
                    UserSectorThemeModel.user_id == user_id,
                    UserSectorThemeModel.slug == slug,
                )
            ).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True


# ── service ───────────────────────────────────────────────────────────


def normalize_tickers(raw: list[str]) -> list[str]:
    """Upper-case, de-duplicate (order-stable) and validate. Raises PersonalThemeError."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        sym = (item or "").strip().upper()
        if not sym:
            continue
        if not _TICKER.match(sym):
            raise PersonalThemeError(
                f"'{sym[:12]}' is not a valid ticker (letters, digits, '.' or '-', up to 10 characters)."
            )
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    if not out:
        raise PersonalThemeError("Add at least one ticker.")
    if len(out) > MAX_MEMBERS:
        raise PersonalThemeError(f"A theme can hold up to {MAX_MEMBERS} tickers.")
    return out


class PersonalThemesService:
    def __init__(self, repository: PersonalThemesRepository) -> None:
        self._repo = repository

    @classmethod
    def from_settings(cls) -> "PersonalThemesService":
        return cls(PersonalThemesRepository.from_settings())

    def list(self, user_id: UUID) -> list[PersonalTheme]:
        return self._repo.list_for_user(user_id)

    def create(self, user_id: UUID, req: PersonalThemeCreateRequest) -> PersonalTheme:
        from app.services.sectors.theme_store import default_label, slugify

        try:
            slug = slugify(req.name)
        except ValueError as exc:
            raise PersonalThemeError("Give the theme a name with letters or digits.") from exc
        members = normalize_tickers(req.members)
        label = (req.label or "").strip() or default_label(req.name)
        return self._repo.upsert(
            user_id, slug=slug, name=req.name.strip(), label=label[:16], members=members
        )

    def delete(self, user_id: UUID, slug: str) -> bool:
        return self._repo.delete(user_id, slug)

    def groups_for(self, user_id: UUID, benchmark: str) -> list[RotationGroup]:
        """The user's themes as rotation groups (equal-weight baskets)."""
        return [
            RotationGroup(
                id=t.theme_id, name=t.name, label=t.label, kind="basket",
                benchmark=benchmark, members=list(t.members), personal=True,
            )
            for t in self._repo.list_for_user(user_id)
        ]
