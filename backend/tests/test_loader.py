"""Tests for the skill loader module."""

import pytest
from sqlmodel import Session, create_engine, SQLModel
from app.skills.loader import load_skill


class TestSkillLoader:
    def test_load_shared_skill(self, db_session):
        skill = load_skill("shared:architect", "user-1", db_session)
        assert skill.name == "architect"
        assert skill.source == "shared"

    def test_load_shared_chat_with_kb(self, db_session):
        skill = load_skill("shared:chat-with-kb", "user-1", db_session)
        assert skill.display_name == "Chat with KB"
        assert "read_kb_file" in skill.tools

    def test_load_nonexistent_shared(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            load_skill("shared:nonexistent-xyz", "user-1", db_session)

    def test_load_personal_not_found(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            load_skill("personal:nope", "user-1", db_session)

    def test_invalid_format_no_colon(self, db_session):
        with pytest.raises(ValueError, match="Invalid skill id"):
            load_skill("no-colon-here", "user-1", db_session)

    def test_invalid_kind(self, db_session):
        with pytest.raises(ValueError, match="Invalid skill kind"):
            load_skill("unknown:name", "user-1", db_session)

    def test_load_personal_skill(self, db_session):
        from app.skills.personal import create_personal_skill
        create_personal_skill(
            db_session, "user-1", "my-test", "My Test", "", "prompt", ["read_kb_file"]
        )
        skill = load_skill("personal:my-test", "user-1", db_session)
        assert skill.source == "personal"
        assert skill.name == "my-test"

    def test_personal_skill_user_isolation(self, db_session):
        from app.skills.personal import create_personal_skill
        create_personal_skill(
            db_session, "user-a", "private", "Private", "", "prompt", []
        )
        with pytest.raises(ValueError, match="not found"):
            load_skill("personal:private", "user-b", db_session)
