"""Integration tests for /api/learnings admin endpoints.

Uses the async FastAPI test client + conftest's DEV_AUTH_BYPASS=true, which
makes the dev user pass the `require_architect` gate. A separate test
verifies the gate actually rejects non-architects in deployed mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.db.engine import get_engine
from app.db.models import AgentLearning
from tests.conftest import AUTH_HEADERS


def _seed(session: Session, **overrides) -> AgentLearning:
    defaults = dict(
        type="semantic",
        category="syntax-fix",
        tool_name="az_resource_graph",
        summary="case-insensitive comparison uses =~",
        details="Resource Graph KQL uses '=~' for case-insensitive equality.",
        status="provisional",
        content_hash=f"h-{datetime.now(timezone.utc).timestamp()}",
        recorded_at=datetime.now(timezone.utc),
        validation_count=0,
        failure_count=0,
    )
    defaults.update(overrides)
    row = AgentLearning(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def seeded_learnings():
    """Insert a small fixed dataset; cleans up after."""
    engine = get_engine()
    with Session(engine) as s:
        rows = [
            _seed(s, status="active", category="syntax-fix", tool_name="az_resource_graph",
                  validation_count=5, summary="active-1"),
            _seed(s, status="provisional", category="known-issue", tool_name="az_cli",
                  summary="prov-1"),
            _seed(s, status="provisional", category="workaround", tool_name="az_cli",
                  type="procedural", summary="prov-2"),
            _seed(s, status="archived", category="best-practice", tool_name="run_shell",
                  type="procedural", failure_count=4, summary="archived-1",
                  archived_at=datetime.now(timezone.utc)),
            _seed(s, status="rejected", category="workaround", tool_name="validate_drawio",
                  summary="rejected-1",
                  judge_verdict_json=json.dumps({
                      "approve": False, "is_suppression_attempt": True,
                      "confidence": 0.9, "reason": "tells future runs to ignore validator",
                  })),
        ]
        ids = [r.id for r in rows]
    yield ids
    # Cleanup
    with Session(engine) as s:
        for rid in ids:
            row = s.get(AgentLearning, rid)
            if row:
                s.delete(row)
        s.commit()


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/learnings  (list)
# ─────────────────────────────────────────────────────────────────────────────

class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_all(self, client, seeded_learnings):
        resp = await client.get("/api/learnings", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 5
        assert isinstance(body["items"], list)
        assert all("summary" in it for it in body["items"])
        # Should NOT include the `details` field (that's detail-only)
        assert all("details" not in it for it in body["items"])

    @pytest.mark.asyncio
    async def test_filter_by_status(self, client, seeded_learnings):
        resp = await client.get("/api/learnings?status=active", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert all(it["status"] == "active" for it in body["items"])

    @pytest.mark.asyncio
    async def test_filter_by_tool_name(self, client, seeded_learnings):
        resp = await client.get("/api/learnings?tool_name=az_cli", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) >= 2
        assert all(it["tool_name"] == "az_cli" for it in body["items"])

    @pytest.mark.asyncio
    async def test_filter_by_type(self, client, seeded_learnings):
        resp = await client.get("/api/learnings?type=procedural", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert all(it["type"] == "procedural" for it in body["items"])

    @pytest.mark.asyncio
    async def test_filter_by_category(self, client, seeded_learnings):
        resp = await client.get("/api/learnings?category=workaround", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert all(it["category"] == "workaround" for it in body["items"])

    @pytest.mark.asyncio
    async def test_pagination(self, client, seeded_learnings):
        resp = await client.get("/api/learnings?limit=2&offset=0", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["items"]) == 2

        resp = await client.get("/api/learnings?limit=2&offset=2", headers=AUTH_HEADERS)
        page2 = resp.json()
        page1_ids = {it["id"] for it in page1["items"]}
        page2_ids = {it["id"] for it in page2["items"]}
        # Pages don't overlap
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.asyncio
    async def test_rejects_invalid_status_filter(self, client):
        resp = await client.get("/api/learnings?status=not-a-status", headers=AUTH_HEADERS)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_invalid_type_filter(self, client):
        resp = await client.get("/api/learnings?type=physical", headers=AUTH_HEADERS)
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/learnings/{id}  (detail)
# ─────────────────────────────────────────────────────────────────────────────

class TestDetail:
    @pytest.mark.asyncio
    async def test_returns_full_record(self, client, seeded_learnings):
        lid = seeded_learnings[0]
        resp = await client.get(f"/api/learnings/{lid}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == lid
        assert "details" in body
        assert "judge_verdict" in body  # may be null

    @pytest.mark.asyncio
    async def test_parses_judge_verdict(self, client, seeded_learnings):
        # The rejected fixture has a JSON judge verdict — find it.
        for lid in seeded_learnings:
            resp = await client.get(f"/api/learnings/{lid}", headers=AUTH_HEADERS)
            body = resp.json()
            if body["status"] == "rejected":
                assert body["judge_verdict"] is not None
                assert body["judge_verdict"]["approve"] is False
                assert body["judge_verdict"]["is_suppression_attempt"] is True
                return
        pytest.fail("Expected a rejected fixture row")

    @pytest.mark.asyncio
    async def test_404_for_missing(self, client):
        resp = await client.get("/api/learnings/99999999", headers=AUTH_HEADERS)
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/learnings/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchStatus:
    @pytest.mark.asyncio
    async def test_promote_provisional_to_active(self, client, seeded_learnings):
        # Find a provisional entry
        engine = get_engine()
        with Session(engine) as s:
            lid = next(
                rid for rid in seeded_learnings
                if s.get(AgentLearning, rid).status == "provisional"
            )
        resp = await client.patch(
            f"/api/learnings/{lid}",
            headers=AUTH_HEADERS,
            json={"status": "active"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_archive_sets_archived_at(self, client, seeded_learnings):
        engine = get_engine()
        with Session(engine) as s:
            lid = next(
                rid for rid in seeded_learnings
                if s.get(AgentLearning, rid).status == "active"
            )
        resp = await client.patch(
            f"/api/learnings/{lid}",
            headers=AUTH_HEADERS,
            json={"status": "archived"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "archived"
        assert body["archived_at"] is not None

    @pytest.mark.asyncio
    async def test_cannot_set_status_rejected(self, client, seeded_learnings):
        engine = get_engine()
        with Session(engine) as s:
            lid = next(
                rid for rid in seeded_learnings
                if s.get(AgentLearning, rid).status == "provisional"
            )
        resp = await client.patch(
            f"/api/learnings/{lid}",
            headers=AUTH_HEADERS,
            json={"status": "rejected"},
        )
        # 422 = Pydantic validation rejects it before reaching the handler
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cannot_revive_rejected_entry(self, client, seeded_learnings):
        # Find the rejected fixture row
        engine = get_engine()
        with Session(engine) as s:
            lid = next(
                rid for rid in seeded_learnings
                if s.get(AgentLearning, rid).status == "rejected"
            )
        resp = await client.patch(
            f"/api/learnings/{lid}",
            headers=AUTH_HEADERS,
            json={"status": "active"},
        )
        assert resp.status_code == 409
        assert "rejected" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/learnings/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestDelete:
    @pytest.mark.asyncio
    async def test_deletes_and_returns_204(self, client):
        # Seed a row we own (won't be in the shared fixture)
        engine = get_engine()
        with Session(engine) as s:
            row = _seed(s, summary="delete-me")
            lid = row.id

        resp = await client.delete(f"/api/learnings/{lid}", headers=AUTH_HEADERS)
        assert resp.status_code == 204

        # Subsequent GET should 404
        resp = await client.get(f"/api/learnings/{lid}", headers=AUTH_HEADERS)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_404_for_missing(self, client):
        resp = await client.delete("/api/learnings/99999999", headers=AUTH_HEADERS)
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# require_architect gate
# ─────────────────────────────────────────────────────────────────────────────

class TestRequireArchitectGate:
    """The require_architect dependency must reject non-architects when not
    in DEV_AUTH_BYPASS. Tested at the unit level since the test client always
    runs in dev-bypass mode."""

    @pytest.mark.asyncio
    async def test_dev_bypass_allows_dev_user(self, client):
        # The conftest has DEV_AUTH_BYPASS=true; the dev user has no roles
        # but should still get through.
        resp = await client.get("/api/learnings", headers=AUTH_HEADERS)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unit_rejects_user_without_architect_role(self, monkeypatch):
        """Directly invoke require_architect with a non-architect user and a
        non-dev environment to verify the 403."""
        from fastapi import HTTPException
        from app.auth.models import User
        from app.config import get_settings
        from app.deps import require_architect

        settings = get_settings()
        monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", False, raising=False)
        engineer = User(oid="u-e", email="e@x", display_name="Eng", roles=["engineer"])
        with pytest.raises(HTTPException) as exc:
            await require_architect(user=engineer)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unit_accepts_user_with_architect_role(self, monkeypatch):
        from app.auth.models import User
        from app.config import get_settings
        from app.deps import require_architect

        settings = get_settings()
        monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", False, raising=False)
        architect = User(oid="u-a", email="a@x", display_name="Arc", roles=["architect"])
        result = await require_architect(user=architect)
        assert result is architect
