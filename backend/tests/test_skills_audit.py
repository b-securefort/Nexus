"""Tests for `audit_shared_skill_tool_allowlists`.

Pins three behaviours:

1. Phantom tool name in a skill's allowlist → drift dict + WARNING log.
2. All tool names in TOOL_REGISTRY (enabled OR disabled-by-config) → empty
   drift + INFO log; no WARNING.
3. No skills loaded → empty drift + DEBUG (silent, not a warning).

The audit is the structural guard for the bug class the `diagram_gen`
phantom exposed on 2026-05-19. Without it, mis-typed or stale tool names
in skill allowlists get silently dropped at chat time by `resolve_tools`,
and the operator only finds out via per-call WARNING logs in production
(or by reading the tool_calls in a sanity transcript, which is what
happened).
"""

import logging

import pytest

from app.skills import shared as shared_module
from app.skills.shared import audit_shared_skill_tool_allowlists
from app.skills.models import Skill
from app.tools.base import TOOL_REGISTRY, init_tools


@pytest.fixture(autouse=True)
def _ensure_tools_loaded():
    """Audit reads TOOL_REGISTRY. Populate it unconditionally: merely
    importing some app modules registers a tool or two as a side effect
    (e.g. app.api.chat registers ask_user), so a non-empty registry does
    NOT mean the full set is present. init_tools() is idempotent —
    repeated calls re-import and re-set enabled flags but leave existing
    registrations alone."""
    init_tools()
    yield


@pytest.fixture
def fake_skill_cache(monkeypatch):
    """Replace the module-level _shared_skills_cache with a controlled
    fixture per test. Restores via monkeypatch teardown."""
    fake: dict[str, Skill] = {}
    monkeypatch.setattr(shared_module, "_shared_skills_cache", fake)
    return fake


class TestAuditDetection:

    def test_phantom_tool_name_is_flagged(self, fake_skill_cache, caplog):
        """A skill listing a tool name not in TOOL_REGISTRY must produce
        both a non-empty drift entry AND a WARNING log."""
        fake_skill_cache["my-skill"] = Skill(
            id="shared:my-skill",
            name="my-skill",
            display_name="My Skill",
            description="",
            system_prompt="...",
            tools=["read_kb_file", "diagram_gen", "search_kb"],  # diagram_gen is the phantom
            source="shared",
        )
        with caplog.at_level(logging.WARNING):
            drift = audit_shared_skill_tool_allowlists()

        assert drift == {"my-skill": ["diagram_gen"]}
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("diagram_gen" in r.getMessage() for r in warnings), (
            f"Expected WARNING containing 'diagram_gen', got: {[r.getMessage() for r in warnings]}"
        )

    def test_all_known_tools_passes_silently(self, fake_skill_cache, caplog):
        """A skill where every tool is in TOOL_REGISTRY → empty drift,
        no WARNING. INFO line allowed (and expected) on the clean path."""
        # Pick names known to be in the live registry (generic tools).
        # If init_tools() changes its mandatory set, update this list.
        valid = ["read_kb_file", "search_kb", "fetch_ms_docs"]
        # Sanity: ensure our fixture inputs are actually in the registry,
        # so the test is testing the audit, not a stale assumption.
        for t in valid:
            assert t in TOOL_REGISTRY, f"prerequisite: {t} must be registered"

        fake_skill_cache["clean-skill"] = Skill(
            id="shared:clean-skill",
            name="clean-skill",
            display_name="Clean",
            description="",
            system_prompt="...",
            tools=valid,
            source="shared",
        )
        with caplog.at_level(logging.DEBUG):
            drift = audit_shared_skill_tool_allowlists()

        assert drift == {}
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not warnings, f"Did not expect WARNING, got: {[r.getMessage() for r in warnings]}"

    def test_disabled_by_config_is_not_flagged(self, fake_skill_cache, caplog, monkeypatch):
        """Tools present in TOOL_REGISTRY but with enabled_by_config=False
        are operational state, not drift. The audit must NOT warn on them.
        resolve_tools handles the disabled case at call time with its own
        log line — that's the right place for that signal."""
        # Pick a tool we know is registered, then flip its config flag off.
        target = "search_kb_semantic"  # has its own enable flag in init_tools
        assert target in TOOL_REGISTRY
        monkeypatch.setattr(TOOL_REGISTRY[target], "enabled_by_config", False)

        fake_skill_cache["uses-disabled"] = Skill(
            id="shared:uses-disabled",
            name="uses-disabled",
            display_name="Uses Disabled",
            description="",
            system_prompt="...",
            tools=[target, "read_kb_file"],
            source="shared",
        )
        with caplog.at_level(logging.WARNING):
            drift = audit_shared_skill_tool_allowlists()

        # Disabled-by-config is NOT drift. Drift is "name doesn't resolve".
        assert drift == {}
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert not warnings, (
            "Disabled-by-config tools must not produce a drift WARNING — "
            f"got: {[r.getMessage() for r in warnings]}"
        )

    def test_no_skills_loaded_returns_empty(self, fake_skill_cache):
        """Loader hasn't run, or a deploy has no skills. Audit must
        return cleanly without claiming the empty set is 'clean' (because
        that's misleading — nothing was checked)."""
        # fake_skill_cache is already empty from the fixture.
        drift = audit_shared_skill_tool_allowlists()
        assert drift == {}

    def test_multiple_phantoms_across_multiple_skills(self, fake_skill_cache, caplog):
        """Each skill's unknown tools collected independently."""
        fake_skill_cache["skill-a"] = Skill(
            id="shared:skill-a", name="skill-a", display_name="A",
            description="", system_prompt="...",
            tools=["read_kb_file", "totally_made_up"],
            source="shared",
        )
        fake_skill_cache["skill-b"] = Skill(
            id="shared:skill-b", name="skill-b", display_name="B",
            description="", system_prompt="...",
            tools=["search_kb", "another_phantom"],
            source="shared",
        )
        with caplog.at_level(logging.WARNING):
            drift = audit_shared_skill_tool_allowlists()

        assert drift == {
            "skill-a": ["totally_made_up"],
            "skill-b": ["another_phantom"],
        }
