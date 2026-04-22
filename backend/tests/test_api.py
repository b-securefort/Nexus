"""Tests for API endpoints — comprehensive suite."""

import pytest
from conftest import AUTH_HEADERS


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_healthz(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "kb_last_sync" in data

    @pytest.mark.asyncio
    async def test_readyz(self, client):
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_ok" in data


# ── Skills ────────────────────────────────────────────────────────────────────

class TestSkillsAPI:
    @pytest.mark.asyncio
    async def test_list_skills(self, client):
        resp = await client.get("/api/skills", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        skills = resp.json()
        assert isinstance(skills, list)
        assert len(skills) >= 1
        # Each skill must have required fields
        for s in skills:
            assert "id" in s
            assert "display_name" in s
            assert "source" in s

    @pytest.mark.asyncio
    async def test_list_skills_no_auth_dev_bypass(self, client):
        """In dev mode with DEV_AUTH_BYPASS, requests without headers still succeed."""
        resp = await client.get("/api/skills")
        assert resp.status_code == 200  # Dev bypass provides a fake user

    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        resp = await client.get("/api/tools", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        tools = resp.json()
        assert isinstance(tools, list)
        assert len(tools) == 18
        tool_names = [t["name"] for t in tools]
        assert "generate_file" in tool_names
        assert "read_kb_file" in tool_names
        assert "search_kb" in tool_names
        assert "fetch_ms_docs" in tool_names
        assert "run_shell" in tool_names
        assert "az_cli" in tool_names
        assert "az_resource_graph" in tool_names
        assert "az_cost_query" in tool_names
        assert "az_monitor_logs" in tool_names
        assert "az_rest_api" in tool_names
        assert "read_learnings" in tool_names
        assert "update_learnings" in tool_names
        # Approval-required tools
        for t in tools:
            if t["name"] in ("run_shell", "az_cli"):
                assert t["requires_approval"] is True
            else:
                assert t["requires_approval"] is False

    @pytest.mark.asyncio
    async def test_list_tools_no_auth_dev_bypass(self, client):
        """In dev mode with DEV_AUTH_BYPASS, requests without headers still succeed."""
        resp = await client.get("/api/tools")
        assert resp.status_code == 200


# ── Personal Skills CRUD ─────────────────────────────────────────────────────

class TestPersonalSkillsAPI:
    @pytest.mark.asyncio
    async def test_create_personal_skill(self, client):
        import uuid
        unique = uuid.uuid4().hex[:8]
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={
                "name": f"api-test-{unique}",
                "display_name": "API Test Skill",
                "description": "A test",
                "system_prompt": "You are helpful",
                "tools": ["read_kb_file"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == f"api-test-{unique}"
        assert data["source"] == "personal"
        assert data["id"] == f"personal:api-test-{unique}"

    @pytest.mark.asyncio
    async def test_get_personal_skill(self, client):
        import uuid
        u = uuid.uuid4().hex[:8]
        # Create first
        await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={
                "name": f"get-{u}",
                "display_name": "Get Test",
                "system_prompt": "Hello",
                "tools": [],
            },
        )
        resp = await client.get(f"/api/skills/personal/get-{u}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == f"get-{u}"
        assert data["system_prompt"] == "Hello"

    @pytest.mark.asyncio
    async def test_get_nonexistent_skill_404(self, client):
        resp = await client.get("/api/skills/personal/nope", headers=AUTH_HEADERS)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_personal_skill(self, client):
        import uuid
        u = uuid.uuid4().hex[:8]
        await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={
                "name": f"upd-{u}",
                "display_name": "Old Name",
                "system_prompt": "old",
                "tools": [],
            },
        )
        resp = await client.put(
            f"/api/skills/personal/upd-{u}",
            headers=AUTH_HEADERS,
            json={"display_name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "New Name"

    @pytest.mark.asyncio
    async def test_delete_personal_skill(self, client):
        import uuid
        u = uuid.uuid4().hex[:8]
        await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={
                "name": f"del-{u}",
                "display_name": "Del",
                "system_prompt": "x",
                "tools": [],
            },
        )
        resp = await client.delete(f"/api/skills/personal/del-{u}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        # Should be gone now
        resp2 = await client.get(f"/api/skills/personal/del-{u}", headers=AUTH_HEADERS)
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_skill_name_uppercase(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "INVALID", "display_name": "T", "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_skill_name_spaces(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "has spaces", "display_name": "T", "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_skill_name_too_long(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "a" * 65, "display_name": "T", "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_system_prompt_rejected(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "empty-prompt", "display_name": "T", "system_prompt": "", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_tool_rejected(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "bad-tool", "display_name": "T", "system_prompt": "p", "tools": ["nonexistent"]},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_display_name_too_long(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "long-dn", "display_name": "x" * 101, "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_description_too_long(self, client):
        resp = await client.post(
            "/api/skills/personal",
            headers=AUTH_HEADERS,
            json={"name": "long-desc", "display_name": "T", "description": "d" * 501, "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_no_auth_dev_bypass(self, client):
        """In dev mode, requests without Auth header still succeed via bypass."""
        import uuid
        unique = uuid.uuid4().hex[:8]
        resp = await client.post(
            "/api/skills/personal",
            json={"name": f"no-auth-{unique}", "display_name": "T", "system_prompt": "p", "tools": []},
        )
        assert resp.status_code == 201


# ── Conversations ─────────────────────────────────────────────────────────────

class TestConversationsAPI:
    @pytest.mark.asyncio
    async def test_list_conversations_empty(self, client):
        resp = await client.get("/api/conversations", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_get_nonexistent_conversation(self, client):
        resp = await client.get("/api/conversations/999999", headers=AUTH_HEADERS)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_conversation(self, client):
        resp = await client.delete("/api/conversations/999999", headers=AUTH_HEADERS)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_nonexistent_conversation(self, client):
        resp = await client.patch(
            "/api/conversations/999999",
            headers=AUTH_HEADERS,
            json={"title": "new title"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_conversations_no_auth_dev_bypass(self, client):
        """In dev mode, requests without Auth header still succeed via bypass."""
        resp = await client.get("/api/conversations")
        assert resp.status_code == 200


# ── Approvals ─────────────────────────────────────────────────────────────────

class TestApprovalsAPI:
    @pytest.mark.asyncio
    async def test_resolve_nonexistent_approval(self, client):
        resp = await client.post(
            "/api/approvals/00000000-0000-0000-0000-000000000000",
            headers=AUTH_HEADERS,
            json={"action": "approve"},
        )
        # Should 404 or 400
        assert resp.status_code in (404, 400)

    @pytest.mark.asyncio
    async def test_invalid_approval_action(self, client):
        resp = await client.post(
            "/api/approvals/00000000-0000-0000-0000-000000000000",
            headers=AUTH_HEADERS,
            json={"action": "invalid"},
        )
        assert resp.status_code in (400, 404, 422)


# ── Chat ──────────────────────────────────────────────────────────────────────

class TestChatAPI:
    @pytest.mark.asyncio
    async def test_chat_no_auth_dev_bypass(self, client):
        """In dev mode, chat requests without Auth header succeed via bypass."""
        resp = await client.post(
            "/api/chat",
            json={"message": "hello", "skill_id": "shared:architect"},
        )
        assert resp.status_code == 200  # SSE stream starts

    @pytest.mark.asyncio
    async def test_chat_missing_message(self, client):
        resp = await client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"skill_id": "shared:architect"},
        )
        assert resp.status_code == 422


# ── Metrics ───────────────────────────────────────────────────────────────────

class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "nexus_" in body or "process_" in body or "python_" in body
