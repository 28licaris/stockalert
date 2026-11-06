"""HTTP contract for /api/v1/my/sector-themes and the rotation merge.

Two users on in-memory SQLite; the principal dependency is overridden, so this
tests the route wiring + isolation, not the session machinery (covered in
test_route_policy / test_auth_middleware).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_my_sector_themes as routes
from app.api.auth_dependencies import get_principal_or_dev
from app.config import settings
from app.main_api import app
from app.services.identity.models import IdentityBase, UserModel
from app.services.identity.schemas import Principal, Role
from app.services.sectors.personal_themes import PersonalThemesRepository, PersonalThemesService


class FakeDemand:
    """Stands in for DailyDemand: no ClickHouse, no Schwab, no threads."""

    def __init__(self, unknown=(), raises=None):
        self.unknown, self.raises = set(unknown), raises
        self.scheduled: list[list[str]] = []

    def check(self, symbols):
        from app.services.ingest.daily_on_demand import DemandCheck

        if self.raises:
            raise self.raises
        bad = [s for s in symbols if s in self.unknown]
        return DemandCheck(to_fetch=tuple(s for s in symbols if s not in self.unknown), unknown=tuple(bad))

    def schedule(self, symbols):
        self.scheduled.append(list(symbols))


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    IdentityBase.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = PersonalThemesService(PersonalThemesRepository(factory), daily=FakeDemand(unknown=["ZZZZ"]))
    monkeypatch.setattr(routes, "_service", lambda: service)
    monkeypatch.setattr(settings, "auth_enabled", False)  # no session gate; principal is overridden below
    monkeypatch.setattr(routes, "_user_id", lambda principal: principal.user_id)  # skip dev-user ensure

    def make_user(email):
        with factory() as db:
            u = UserModel(email=email, normalized_email=email, display_name=email, status="active")
            db.add(u)
            db.commit()
            return Principal(user_id=u.id, tenant_id=uuid4(), session_id=uuid4(), roles=frozenset({Role.OWNER}))

    current = {}
    app.dependency_overrides[get_principal_or_dev] = lambda: current["p"]
    yield make_user, current, service
    app.dependency_overrides.clear()
    engine.dispose()


def test_create_list_delete_roundtrip(world):
    make_user, current, _ = world
    current["p"] = make_user("a@x.com")
    c = TestClient(app)

    created = c.post("/api/v1/my/sector-themes", json={"name": "Copper Miners", "members": ["fcx", "scco"]})
    assert created.status_code == 201, created.text
    assert created.json()["theme_id"] == "my:copper-miners"
    assert created.json()["members"] == ["FCX", "SCCO"]

    assert [t["slug"] for t in c.get("/api/v1/my/sector-themes").json()] == ["copper-miners"]
    assert c.delete("/api/v1/my/sector-themes/copper-miners").status_code == 204
    assert c.get("/api/v1/my/sector-themes").json() == []
    assert c.delete("/api/v1/my/sector-themes/copper-miners").status_code == 404


def test_one_user_cannot_read_or_delete_anothers_theme(world):
    make_user, current, _ = world
    alice, bob = make_user("a@x.com"), make_user("b@x.com")
    c = TestClient(app)

    current["p"] = alice
    assert c.post("/api/v1/my/sector-themes", json={"name": "Secret Basket", "members": ["AAPL"]}).status_code == 201

    current["p"] = bob
    assert c.get("/api/v1/my/sector-themes").json() == []
    assert c.delete("/api/v1/my/sector-themes/secret-basket").status_code == 404

    current["p"] = alice
    assert [t["slug"] for t in c.get("/api/v1/my/sector-themes").json()] == ["secret-basket"]


@pytest.mark.parametrize(
    "body, status",
    [
        ({"name": "X", "members": ["/ES"]}, 400),                 # not an equity ticker
        ({"name": "X", "members": ["AAPL;DROP TABLE"]}, 400),
        ({"name": "!!!", "members": ["AAPL"]}, 400),              # no letters/digits
        ({"name": "X", "members": []}, 422),
        ({"name": "", "members": ["AAPL"]}, 422),
        ({"name": "Bad\nName", "members": ["AAPL"]}, 422),        # control char: 422, not 500
        ({"name": "X" * 61, "members": ["AAPL"]}, 422),
    ],
)
def test_invalid_input_is_rejected_cleanly(world, body, status):
    make_user, current, service = world
    current["p"] = make_user("a@x.com")
    r = TestClient(app).post("/api/v1/my/sector-themes", json=body)
    assert r.status_code == status, r.text
    assert service.list(current["p"].user_id) == []


def test_theme_cap_surfaces_as_400(world):
    make_user, current, _ = world
    current["p"] = make_user("a@x.com")
    c = TestClient(app)
    for i in range(10):
        assert c.post("/api/v1/my/sector-themes", json={"name": f"T{i}", "members": ["AAPL"]}).status_code == 201
    r = c.post("/api/v1/my/sector-themes", json={"name": "Eleventh", "members": ["AAPL"]})
    assert r.status_code == 400 and "10 themes" in r.json()["message"]


def test_creating_a_personal_theme_never_touches_the_shared_universe(world, monkeypatch):
    """Unlike the shared theme path, no stream-universe onboarding is scheduled."""
    from app.services.sectors import universe_sync

    def boom(*a, **k):
        raise AssertionError("personal themes must not onboard into the shared universe")

    monkeypatch.setattr(universe_sync, "schedule_onboard", boom)
    monkeypatch.setattr(universe_sync, "_onboard", boom)
    make_user, current, _ = world
    current["p"] = make_user("a@x.com")
    r = TestClient(app).post("/api/v1/my/sector-themes", json={"name": "Anything", "members": ["NEWCO"]})
    assert r.status_code == 201


# ── rotation route: personal themes are additive and can never break the chart ──


def _fake_dashboard_service(captured):
    from app.services.sectors.schemas import RotationDashboard
    from datetime import date

    class _Svc:
        def build_dashboard(self, tail_weeks=None):
            return RotationDashboard(benchmark="SPY", as_of=date(2026, 11, 5), tail_weeks=12)

    def from_settings(benchmark=None, extra_groups=None):
        captured["extra"] = extra_groups
        return _Svc()

    return from_settings


def test_rotation_adds_the_users_personal_groups(monkeypatch):
    from app.api import routes_sector_rotation as rot
    from app.services.sectors.schemas import RotationGroup

    captured = {}
    uid = uuid4()
    group = RotationGroup(id="my:x", name="X", kind="basket", benchmark="SPY", members=["AAA"], personal=True)

    class _P:
        @classmethod
        def from_settings(cls):
            return cls()

        def groups_for(self, user_id, bench):
            assert user_id == uid and bench == "SPY"
            return [group]

    monkeypatch.setattr(rot, "_session_user_id", lambda request: uid)
    monkeypatch.setattr(rot, "PersonalThemesService", _P)
    monkeypatch.setattr(rot.SectorRotationService, "from_settings", _fake_dashboard_service(captured))

    assert TestClient(app).get("/api/v1/sectors/rotation").status_code == 200
    assert captured["extra"] == [group]


def test_rotation_still_renders_when_personal_themes_fail(monkeypatch):
    from app.api import routes_sector_rotation as rot

    captured = {}

    class _Broken:
        @classmethod
        def from_settings(cls):
            raise RuntimeError("identity db down")

    monkeypatch.setattr(rot, "_session_user_id", lambda request: uuid4())
    monkeypatch.setattr(rot, "PersonalThemesService", _Broken)
    monkeypatch.setattr(rot.SectorRotationService, "from_settings", _fake_dashboard_service(captured))

    assert TestClient(app).get("/api/v1/sectors/rotation").status_code == 200
    assert captured["extra"] == []  # shared chart unaffected


def test_rotation_with_auth_off_has_no_personal_themes(monkeypatch):
    from app.api import routes_sector_rotation as rot

    captured = {}
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(rot.SectorRotationService, "from_settings", _fake_dashboard_service(captured))
    assert TestClient(app).get("/api/v1/sectors/rotation").status_code == 200
    assert captured["extra"] == []


def test_unknown_ticker_is_a_400_with_a_clear_message(world):
    make_user, current, service = world
    current["p"] = make_user("a@x.com")
    r = TestClient(app).post("/api/v1/my/sector-themes", json={"name": "Typo", "members": ["AAPL", "ZZZZ"]})
    assert r.status_code == 400 and "Unknown ticker: ZZZZ" in r.json()["message"]
    assert service.list(current["p"].user_id) == []


def test_price_source_outage_is_a_503_not_a_400(world, monkeypatch):
    from app.services.ingest.daily_on_demand import DailyDataUnavailable

    make_user, current, service = world
    current["p"] = make_user("a@x.com")
    monkeypatch.setattr(service, "_daily", FakeDemand(raises=DailyDataUnavailable("token expired")))
    r = TestClient(app).post("/api/v1/my/sector-themes", json={"name": "Any", "members": ["AAPL"]})
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["message"]
    assert "token" not in r.text, "internal reason must not leak to the user"
    assert service.list(current["p"].user_id) == []
