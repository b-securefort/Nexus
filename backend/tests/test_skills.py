"""Tests for skill parsing and loading."""

import pytest
import json
from pathlib import Path
from app.skills.shared import _parse_skill_file
from app.skills.models import Skill
from app.skills.personal import (
    create_personal_skill,
    get_personal_skill,
    list_personal_skills,
    update_personal_skill,
    delete_personal_skill,
)


class TestSkillParsing:
    """Test shared skill SKILL.md parsing."""

    def test_parse_valid_skill(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
display_name: Test Skill
description: A test skill
tools:
  - read_kb_file
  - search_kb
---

You are a test assistant.
"""
        )
        skill = _parse_skill_file("test-skill", skill_file)
        assert skill is not None
        assert skill.display_name == "Test Skill"
        assert skill.description == "A test skill"
        assert skill.tools == ["read_kb_file", "search_kb"]
        assert skill.system_prompt == "You are a test assistant."
        assert skill.source == "shared"
        assert skill.id == "shared:test-skill"

    def test_parse_missing_frontmatter(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("No frontmatter here")
        skill = _parse_skill_file("bad", skill_file)
        assert skill is None

    def test_parse_missing_display_name(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
description: No display name
---

Body text
"""
        )
        skill = _parse_skill_file("bad", skill_file)
        assert skill is None

    def test_parse_tuning_fields(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
display_name: Tuned
description: Skill with decoder tuning
reasoning_effort: low
verbosity: Medium
tools: []
---

Body
"""
        )
        skill = _parse_skill_file("tuned", skill_file)
        assert skill is not None
        assert skill.reasoning_effort == "low"
        assert skill.verbosity == "medium"  # normalized to lowercase

    def test_parse_tuning_fields_absent_default_none(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
display_name: Plain
description: No tuning keys
tools: []
---

Body
"""
        )
        skill = _parse_skill_file("plain", skill_file)
        assert skill is not None
        assert skill.reasoning_effort is None
        assert skill.verbosity is None

    def test_parse_tuning_fields_invalid_dropped(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
display_name: Bad Tuning
description: Invalid values must not fail the load
reasoning_effort: turbo
verbosity: 11
tools: []
---

Body
"""
        )
        skill = _parse_skill_file("bad-tuning", skill_file)
        assert skill is not None  # skill still loads
        assert skill.reasoning_effort is None
        assert skill.verbosity is None

    def test_parse_minimal_effort_allowed(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(
            """---
display_name: Minimal
description: minimal is a valid effort but not a valid verbosity
reasoning_effort: minimal
verbosity: minimal
tools: []
---

Body
"""
        )
        skill = _parse_skill_file("minimal", skill_file)
        assert skill is not None
        assert skill.reasoning_effort == "minimal"
        assert skill.verbosity is None


class TestSkillTuningResolution:
    """Snapshot roundtrip + per-turn resolution of reasoning_effort/verbosity."""

    def _skill(self, **overrides) -> Skill:
        base = dict(
            id="shared:t", name="t", display_name="T", description="",
            system_prompt="p", tools=[], source="shared",
        )
        base.update(overrides)
        return Skill(**base)

    def test_snapshot_roundtrip_preserves_tuning(self):
        from app.api.chat import _skill_to_snapshot
        from app.agent.orchestrator import _skill_from_snapshot

        skill = self._skill(reasoning_effort="low", verbosity="medium")
        restored = _skill_from_snapshot(_skill_to_snapshot(skill))
        assert restored.reasoning_effort == "low"
        assert restored.verbosity == "medium"

    def test_legacy_snapshot_without_tuning_keys(self):
        from app.agent.orchestrator import _skill_from_snapshot

        legacy = json.dumps({
            "id": "shared:old", "name": "old", "display_name": "Old",
            "description": "", "system_prompt": "p", "tools": [], "source": "shared",
        })
        restored = _skill_from_snapshot(legacy)
        assert restored.reasoning_effort is None
        assert restored.verbosity is None

    def test_resolution_skill_overrides_config(self):
        from types import SimpleNamespace
        from app.agent.orchestrator import _resolve_tuning_kwargs

        settings = SimpleNamespace(CHAT_REASONING_EFFORT="high", CHAT_VERBOSITY="low")
        skill = self._skill(reasoning_effort="low", verbosity="medium")
        assert _resolve_tuning_kwargs(skill, settings) == {
            "reasoning_effort": "low",
            "verbosity": "medium",
        }

    def test_resolution_falls_back_to_config(self):
        from types import SimpleNamespace
        from app.agent.orchestrator import _resolve_tuning_kwargs

        settings = SimpleNamespace(CHAT_REASONING_EFFORT="", CHAT_VERBOSITY="low")
        skill = self._skill()  # no per-skill tuning
        assert _resolve_tuning_kwargs(skill, settings) == {"verbosity": "low"}

    def test_resolution_empty_everywhere_omits_params(self):
        from types import SimpleNamespace
        from app.agent.orchestrator import _resolve_tuning_kwargs

        settings = SimpleNamespace(CHAT_REASONING_EFFORT="", CHAT_VERBOSITY="")
        skill = self._skill()
        assert _resolve_tuning_kwargs(skill, settings) == {}


class TestPersonalSkillsCRUD:
    """Test personal skills CRUD operations."""

    def test_create_and_get(self, db_session):
        skill = create_personal_skill(
            db_session,
            user_oid="user-1",
            name="my-skill",
            display_name="My Skill",
            description="desc",
            system_prompt="You are helpful",
            tools=["read_kb_file"],
        )
        assert skill.id == "personal:my-skill"
        assert skill.source == "personal"

        fetched = get_personal_skill(db_session, "user-1", "my-skill")
        assert fetched is not None
        assert fetched.display_name == "My Skill"

    def test_user_isolation(self, db_session):
        """User A cannot see User B's skills."""
        create_personal_skill(
            db_session, "user-a", "skill-a", "Skill A", "", "prompt", []
        )
        create_personal_skill(
            db_session, "user-b", "skill-b", "Skill B", "", "prompt", []
        )

        a_skills = list_personal_skills(db_session, "user-a")
        assert len(a_skills) == 1
        assert a_skills[0].name == "skill-a"

        b_skill = get_personal_skill(db_session, "user-a", "skill-b")
        assert b_skill is None

    def test_update(self, db_session):
        create_personal_skill(
            db_session, "user-1", "my-skill", "Old Name", "", "old prompt", []
        )
        updated = update_personal_skill(
            db_session, "user-1", "my-skill", display_name="New Name"
        )
        assert updated is not None
        assert updated.display_name == "New Name"

    def test_soft_delete(self, db_session):
        create_personal_skill(
            db_session, "user-1", "my-skill", "Name", "", "prompt", []
        )
        deleted = delete_personal_skill(db_session, "user-1", "my-skill")
        assert deleted is True

        fetched = get_personal_skill(db_session, "user-1", "my-skill")
        assert fetched is None

        # List should not include deleted
        skills = list_personal_skills(db_session, "user-1")
        assert len(skills) == 0
