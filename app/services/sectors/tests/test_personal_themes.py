"""Personal sector themes: validation, per-user isolation, caps, rotation merge.

Real SQLAlchemy models on in-memory SQLite; no PostgreSQL, ClickHouse or lake.
"""
from __future__ import annotations

from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.identity.models import IdentityBase, UserModel
from app.services.sectors import service as service_mod
from app.services.sectors.personal_themes import (
    MAX_MEMBERS,
    MAX_THEMES_PER_USER,
    PERSONAL_ID_PREFIX,
    PersonalThemeCreateRequest,
    PersonalThemeError,
    PersonalThemesRepository,
    PersonalThemesService,
    normalize_tickers,
)
from app.services.sectors.resolver import GroupResolutionError
from app.services.sectors.schemas import RotationGroup
from app.services.sectors.service import SectorRotationService


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    IdentityBase.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _user(factory, email: str):
    with factory() as db:
        u = UserModel(email=email, normalized_email=email, display_name=email, status="active")
        db.add(u)
        db.commit()
        return u.id


@pytest.fixture
def svc(factory):
    return PersonalThemesService(PersonalThemesRepository(factory))


def _req(name="Copper Miners", members=("fcx", "SCCO"), label=None):
    return PersonalThemeCreateRequest(name=name, members=list(members), label=label)


# ── ticker validation ─────────────────────────────────────────────────


def test_normalize_uppercases_dedupes_and_keeps_order():
    assert normalize_tickers(["fcx", " scco ", "FCX", "BRK.B", "RDS-A"]) == ["FCX", "SCCO", "BRK.B", "RDS-A"]


@pytest.mark.parametrize("bad", ["", "  "])
def test_normalize_requires_at_least_one(bad):
    with pytest.raises(PersonalThemeError, match="at least one"):
        normalize_tickers([bad])


@pytest.mark.parametrize("bad", ["/ES", "A B", "AAPL;DROP", "<script>", "TOOLONGTICKER1", "$AAPL", "-X"])
def test_normalize_rejects_non_tickers(bad):
    with pytest.raises(PersonalThemeError, match="not a valid ticker"):
        normalize_tickers(["AAPL", bad])


def test_normalize_caps_members():
    assert len(normalize_tickers([f"S{i}" for i in range(MAX_MEMBERS)])) == MAX_MEMBERS
    with pytest.raises(PersonalThemeError, match=str(MAX_MEMBERS)):
        normalize_tickers([f"S{i}" for i in range(MAX_MEMBERS + 1)])


# ── service + repository ──────────────────────────────────────────────


def test_create_list_replace_delete(svc, factory):
    uid = _user(factory, "a@x.com")
    t = svc.create(uid, _req())
    assert t.theme_id == f"{PERSONAL_ID_PREFIX}copper-miners"
    assert t.slug == "copper-miners" and t.members == ["FCX", "SCCO"]
    assert t.label == "Copper Mnr"  # default label from the shared helper

    # same name replaces, doesn't duplicate
    svc.create(uid, _req(members=["FCX", "TECK", "HBM"], label="Cu"))
    mine = svc.list(uid)
    assert [(m.slug, m.members, m.label) for m in mine] == [("copper-miners", ["FCX", "TECK", "HBM"], "Cu")]

    assert svc.delete(uid, "copper-miners") is True
    assert svc.delete(uid, "copper-miners") is False
    assert svc.list(uid) == []


def test_names_without_letters_or_digits_are_rejected(svc, factory):
    uid = _user(factory, "a@x.com")
    with pytest.raises(PersonalThemeError, match="name"):
        svc.create(uid, _req(name="!!!"))


def test_users_cannot_see_or_delete_each_others_themes(svc, factory):
    alice, bob = _user(factory, "a@x.com"), _user(factory, "b@x.com")
    svc.create(alice, _req(name="Alice Only"))
    svc.create(bob, _req(name="Alice Only", members=["XOM"]))  # same slug, different owner: independent rows

    assert [t.members for t in svc.list(alice)] == [["FCX", "SCCO"]]
    assert [t.members for t in svc.list(bob)] == [["XOM"]]

    assert svc.delete(bob, "alice-only") is True      # deletes Bob's own
    assert [t.slug for t in svc.list(alice)] == ["alice-only"]  # Alice's untouched
    assert svc.delete(bob, "alice-only") is False     # cannot reach Alice's row


def test_per_user_theme_cap_allows_replace_but_not_new(svc, factory):
    uid, other = _user(factory, "a@x.com"), _user(factory, "b@x.com")
    for i in range(MAX_THEMES_PER_USER):
        svc.create(uid, _req(name=f"Theme {i}"))
    with pytest.raises(PersonalThemeError, match=str(MAX_THEMES_PER_USER)):
        svc.create(uid, _req(name="One too many"))
    svc.create(uid, _req(name="Theme 3", members=["AAPL"]))  # replacing an existing one still works
    svc.create(other, _req(name="One too many"))             # cap is per user
    assert len(svc.list(uid)) == MAX_THEMES_PER_USER


def test_groups_for_builds_personal_basket_groups(svc, factory):
    uid = _user(factory, "a@x.com")
    svc.create(uid, _req())
    (g,) = svc.groups_for(uid, "SPY")
    assert (g.id, g.kind, g.benchmark, g.personal) == ("my:copper-miners", "basket", "SPY", True)
    assert g.members == ["FCX", "SCCO"]


def test_personal_ids_cannot_collide_with_shared_theme_ids():
    from app.services.sectors.theme_store import slugify

    assert ":" not in slugify("my:copper miners")  # shared ids never contain the prefix separator


# ── rotation merge ────────────────────────────────────────────────────


def _bdates(n):
    return pd.Index([d.date() for d in pd.bdate_range("2024-01-01", periods=n)])


def _accel(n, k):
    return pd.Series(100.0 * np.exp(k * np.arange(n) ** 2), index=_bdates(n), dtype="float64")


def test_dashboard_includes_personal_groups_flagged_and_surfaces_missing_data(monkeypatch):
    n = 80
    bench = pd.Series([100.0] * n, index=_bdates(n), dtype="float64")
    up = _accel(n, 0.0006)

    shared = [RotationGroup(id="XLK", name="Tech", benchmark="SPY", members=["XLK"])]
    mine = [
        RotationGroup(id="my:hot", name="Hot", label="Hot", kind="basket", benchmark="SPY",
                      members=["AAA"], personal=True),
        RotationGroup(id="my:nodata", name="No data", kind="basket", benchmark="SPY",
                      members=["ZZZZ"], personal=True),
    ]

    def fake_resolve(group, *, lookback_days=None):
        if group.id == "SPY":
            return bench
        if group.id in ("XLK", "my:hot"):
            return up
        raise GroupResolutionError("no daily bars for ZZZZ yet")

    monkeypatch.setattr(service_mod, "resolve", fake_resolve)
    monkeypatch.setattr(service_mod.definitions, "default_groups", lambda bench=None: shared)

    svc = SectorRotationService.from_settings(benchmark="SPY", extra_groups=mine)
    svc.ratio_window, svc.mom_window, svc.tail_weeks = 5, 3, 4
    dash = svc.build_dashboard()

    by_id = {s.group_id: s for s in dash.sectors}
    assert by_id["XLK"].personal is False
    assert by_id["my:hot"].personal is True
    assert [e.group_id for e in dash.excluded] == ["my:nodata"]
    assert "no daily bars" in dash.excluded[0].reason
