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
