"""Tests for the role-based access map in app/auth/rbac.py.

These tests verify the filter logic directly, bypassing the FastAPI layer.
The conftest sets DEV_AUTH_BYPASS=true globally, so we monkeypatch
`_is_dev_bypass` to False in role-filter tests to simulate a deployed
environment with real Entra auth.
"""

import pytest

from app.auth import rbac
from app.auth.models import User


@pytest.fixture(autouse=True)
def restore_access_map():
    """Restore _ACCESS_MAP to defaults around every test in this module."""
    rbac.reset_access_map_for_tests()
    yield
    rbac.reset_access_map_for_tests()


@pytest.fixture
def deployed_mode(monkeypatch):
    """Simulate a deployed environment (DEV_AUTH_BYPASS=false)."""
    monkeypatch.setattr(rbac, "_is_dev_bypass", lambda: False)


def _user(*roles: str) -> User:
    return User(
        oid="u-test",
        email="u@test",
        display_name="U",
        roles=list(roles),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Default access map shape
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultAccessMap:
    def test_default_map_passes_validation(self):
        """The hardcoded DEFAULT_ACCESS_MAP must itself be a valid shape."""
        # _validate_map_shape raises on invalid input; this also verifies the
        # __default__ key requirement.
        rbac._validate_map_shape(rbac.DEFAULT_ACCESS_MAP)

    def test_no_role_key_present(self):
        assert rbac.NO_ROLE_KEY in rbac.DEFAULT_ACCESS_MAP

    def test_engineer_is_superset_of_default(self):
        default_tools = set(rbac.DEFAULT_ACCESS_MAP[rbac.NO_ROLE_KEY]["tools"])
        engineer_tools = set(rbac.DEFAULT_ACCESS_MAP["engineer"]["tools"])
        assert default_tools.issubset(engineer_tools), (
            "Engineer role must include every Default-tier tool"
        )

    def test_architect_is_superset_of_engineer(self):
        engineer_tools = set(rbac.DEFAULT_ACCESS_MAP["engineer"]["tools"])
        architect_tools = set(rbac.DEFAULT_ACCESS_MAP["architect"]["tools"])
        assert engineer_tools.issubset(architect_tools), (
            "Architect role must include every Engineer-tier tool"
        )
        # Architect-only tools (drawio specialists) must be present
        assert "ask_user" in architect_tools
        assert "render_drawio" in architect_tools

    def test_default_skills_are_kb_only(self):
        skills = rbac.DEFAULT_ACCESS_MAP[rbac.NO_ROLE_KEY]["skills"]
        assert skills == ["kb-searcher"]

    def test_architect_skills_include_drawio(self):
        skills = set(rbac.DEFAULT_ACCESS_MAP["architect"]["skills"])
        assert "drawio-diagrammer" in skills

    def test_engineer_skills_exclude_architect_skill(self):
        """Engineers must not see the Azure Architect shared skill."""
        skills = set(rbac.DEFAULT_ACCESS_MAP["engineer"]["skills"])
        assert "architect" not in skills
        assert "drawio-diagrammer" not in skills


# ─────────────────────────────────────────────────────────────────────────────
# allowed_skills_for / allowed_tools_for
# ─────────────────────────────────────────────────────────────────────────────

class TestFilters:
    def test_dev_bypass_returns_none(self):
        """Dev bypass means no filtering — local runs see everything."""
        # No monkeypatch — conftest already has DEV_AUTH_BYPASS=true
        assert rbac.allowed_skills_for(_user()) is None
        assert rbac.allowed_tools_for(_user("engineer")) is None

    def test_no_role_user_gets_default_only(self, deployed_mode):
        skills = rbac.allowed_skills_for(_user())
        assert skills == {"kb-searcher"}

    def test_engineer_sees_default_and_engineer_skills(self, deployed_mode):
        skills = rbac.allowed_skills_for(_user("engineer"))
        assert skills == {"kb-searcher", "chat-with-kb"}

    def test_architect_sees_all_skills(self, deployed_mode):
        skills = rbac.allowed_skills_for(_user("architect"))
        assert skills == {
            "kb-searcher", "chat-with-kb", "architect",
            "drawio-diagrammer",
        }

    def test_engineer_cannot_use_architect_only_tools(self, deployed_mode):
        tools = rbac.allowed_tools_for(_user("engineer"))
        assert tools is not None
        assert "az_cli" in tools
        assert "execute_script" in tools
        assert "read_file" in tools
        # Drawio specialist tools are architect-only
        assert "render_drawio" not in tools
        assert "generate_drawio_from_python" not in tools

    def test_unknown_role_grants_no_access(self, deployed_mode):
        """A role in the JWT but not in the access map contributes nothing."""
        skills = rbac.allowed_skills_for(_user("ghost-role"))
        # No mapped role → falls back to __default__ only if roles list is
        # empty. A user with a role that doesn't match the map gets nothing,
        # not __default__ — we don't silently demote.
        assert skills == set()

    def test_multiple_roles_take_union(self, deployed_mode):
        """A user with both engineer and architect gets the union."""
        skills = rbac.allowed_skills_for(_user("engineer", "architect"))
        assert skills == {
            "kb-searcher", "chat-with-kb", "architect",
            "drawio-diagrammer",
        }


# ─────────────────────────────────────────────────────────────────────────────
# is_skill_allowed / is_tool_allowed
# ─────────────────────────────────────────────────────────────────────────────

class TestPredicates:
    def test_is_skill_allowed_in_dev_bypass(self):
        # Dev bypass → everything allowed, regardless of skill slug
        assert rbac.is_skill_allowed(_user(), "drawio-diagrammer") is True

    def test_is_skill_allowed_engineer(self, deployed_mode):
        assert rbac.is_skill_allowed(_user("engineer"), "chat-with-kb") is True
        assert rbac.is_skill_allowed(_user("engineer"), "architect") is False

    def test_is_tool_allowed_blocks_az_cli_for_default_role(self, deployed_mode):
        assert rbac.is_tool_allowed(_user(), "read_kb_file") is True
        assert rbac.is_tool_allowed(_user(), "az_cli") is False


# ─────────────────────────────────────────────────────────────────────────────
# _validate_map_shape
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateMapShape:
    def _good(self) -> dict:
        return {
            rbac.NO_ROLE_KEY: {"skills": ["kb-searcher"], "tools": ["read_kb_file"]},
            "engineer": {"skills": ["chat-with-kb"], "tools": ["az_cli"]},
        }

    def test_accepts_valid(self):
        rbac._validate_map_shape(self._good())

    def test_rejects_non_dict_root(self):
        with pytest.raises(ValueError, match="dict"):
            rbac._validate_map_shape(["not", "a", "dict"])

    def test_rejects_missing_default_key(self):
        bad = {"engineer": {"skills": [], "tools": []}}
        with pytest.raises(ValueError, match=rbac.NO_ROLE_KEY):
            rbac._validate_map_shape(bad)

    def test_rejects_role_value_not_dict(self):
        bad = {rbac.NO_ROLE_KEY: ["not", "a", "dict"]}
        with pytest.raises(ValueError, match="must be a dict"):
            rbac._validate_map_shape(bad)

    def test_rejects_non_list_skills(self):
        bad = {rbac.NO_ROLE_KEY: {"skills": "string", "tools": []}}
        with pytest.raises(ValueError, match="skills"):
            rbac._validate_map_shape(bad)

    def test_rejects_non_string_tool_name(self):
        bad = {rbac.NO_ROLE_KEY: {"skills": [], "tools": [123]}}
        with pytest.raises(ValueError, match="tools"):
            rbac._validate_map_shape(bad)


# ─────────────────────────────────────────────────────────────────────────────
# init_rbac fallback behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestInitRbac:
    @pytest.mark.asyncio
    async def test_no_endpoint_keeps_defaults(self, monkeypatch, caplog):
        """Empty AZURE_APPCONFIG_ENDPOINT is the documented no-op path."""
        settings = rbac.get_settings()
        monkeypatch.setattr(settings, "AZURE_APPCONFIG_ENDPOINT", "", raising=False)
        await rbac.init_rbac()
        # Defaults are still in place
        assert rbac.NO_ROLE_KEY in rbac._ACCESS_MAP
        assert "engineer" in rbac._ACCESS_MAP
        assert "architect" in rbac._ACCESS_MAP
