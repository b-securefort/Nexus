"""Tests for the per-user cap admin API (DESIGN.md §5 2026-06-14).

Caps are entered/displayed in credits (1 credit = $0.01) and stored in USD.
Architect gating itself is covered by the shared require_architect dep + the
learnings admin tests; here we exercise the cap read/write behaviour.
"""

import pytest
from sqlmodel import Session, select

from app.db.engine import get_engine
from app.db.models import UserRecord
from tests.conftest import AUTH_HEADERS


def _seed_user(oid, email, cap_usd=None):
    with Session(get_engine()) as s:
        existing = s.exec(select(UserRecord).where(UserRecord.oid == oid)).first()
        if existing:
            existing.credit_cap_usd = cap_usd
            s.add(existing)
        else:
            s.add(UserRecord(oid=oid, email=email, display_name=email, credit_cap_usd=cap_usd))
        s.commit()


def _cleanup(oid):
    with Session(get_engine()) as s:
        for u in s.exec(select(UserRecord).where(UserRecord.oid == oid)).all():
            s.delete(u)
        s.commit()


@pytest.fixture
def seeded(client):
    # `client` first so the lifespan migration adds credit_cap_usd before seeding.
    _seed_user("admincap-a", "a@x.com", cap_usd=None)
    _seed_user("admincap-b", "b@x.com", cap_usd=30.0)  # 3000 credits
    yield
    _cleanup("admincap-a")
    _cleanup("admincap-b")


@pytest.mark.asyncio
async def test_list_users_shows_caps_in_credits(client, seeded):
    resp = await client.get("/api/users", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_cap_credits"] == 2000  # $20 default × 100
    rows = {r["oid"]: r for r in body["items"]}
    # No override → cap_credits None, effective falls to the default.
    assert rows["admincap-a"]["cap_credits"] is None
    assert rows["admincap-a"]["effective_cap_credits"] == 2000
    # $30 override → 3000 credits.
    assert rows["admincap-b"]["cap_credits"] == 3000
    assert rows["admincap-b"]["effective_cap_credits"] == 3000


@pytest.mark.asyncio
async def test_set_cap_in_credits(client, seeded):
    resp = await client.patch(
        "/api/users/admincap-a", json={"cap_credits": 5000}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cap_credits"] == 5000
    assert body["effective_cap_credits"] == 5000
    # Persisted as USD on the row.
    with Session(get_engine()) as s:
        u = s.exec(select(UserRecord).where(UserRecord.oid == "admincap-a")).first()
        assert u.credit_cap_usd == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_clear_cap_reverts_to_default(client, seeded):
    resp = await client.patch(
        "/api/users/admincap-b", json={"cap_credits": None}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cap_credits"] is None
    assert body["effective_cap_credits"] == 2000


@pytest.mark.asyncio
async def test_patch_unknown_user_404(client):
    resp = await client.patch(
        "/api/users/does-not-exist", json={"cap_credits": 100}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_negative_cap_rejected(client, seeded):
    resp = await client.patch(
        "/api/users/admincap-a", json={"cap_credits": -5}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 422
