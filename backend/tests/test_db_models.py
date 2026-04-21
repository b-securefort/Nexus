"""Tests for DB models."""

from datetime import datetime, timezone
from app.db.models import UserRecord, Conversation, Message, PersonalSkill, PendingApproval


class TestUserRecord:
    def test_create(self, db_session):
        user = UserRecord(oid="u1", email="u@test.com", display_name="User")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        assert user.id is not None
        assert user.oid == "u1"
        assert user.created_at is not None
        assert user.last_seen_at is not None


class TestConversation:
    def test_create(self, db_session):
        conv = Conversation(
            user_oid="u1",
            title="Test Chat",
            skill_id="shared:architect",
            skill_snapshot_json="{}",
        )
        db_session.add(conv)
        db_session.commit()
        db_session.refresh(conv)
        assert conv.id is not None
        assert conv.deleted_at is None

    def test_soft_delete(self, db_session):
        conv = Conversation(
            user_oid="u1", title="Del", skill_id="shared:x", skill_snapshot_json="{}"
        )
        db_session.add(conv)
        db_session.commit()
        conv.deleted_at = datetime.now(timezone.utc)
        db_session.add(conv)
        db_session.commit()
        db_session.refresh(conv)
        assert conv.deleted_at is not None


class TestMessage:
    def test_create_user_message(self, db_session):
        conv = Conversation(
            user_oid="u1", title="C", skill_id="s", skill_snapshot_json="{}"
        )
        db_session.add(conv)
        db_session.commit()

        msg = Message(conversation_id=conv.id, role="user", content="Hello")
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)
        assert msg.id is not None
        assert msg.role == "user"

    def test_create_tool_message(self, db_session):
        conv = Conversation(
            user_oid="u1", title="C", skill_id="s", skill_snapshot_json="{}"
        )
        db_session.add(conv)
        db_session.commit()

        msg = Message(
            conversation_id=conv.id,
            role="tool",
            content="result",
            tool_call_id="tc-1",
            tool_name="search_kb",
        )
        db_session.add(msg)
        db_session.commit()
        assert msg.tool_name == "search_kb"


class TestPersonalSkill:
    def test_create(self, db_session):
        skill = PersonalSkill(
            user_oid="u1",
            name="my-skill",
            display_name="My Skill",
            description="desc",
            system_prompt="Be helpful",
            tools_json='["read_kb_file"]',
        )
        db_session.add(skill)
        db_session.commit()
        db_session.refresh(skill)
        assert skill.id is not None
        assert skill.deleted_at is None


class TestPendingApproval:
    def test_create(self, db_session):
        import uuid
        conv = Conversation(
            user_oid="u1", title="C", skill_id="s", skill_snapshot_json="{}"
        )
        db_session.add(conv)
        db_session.commit()

        approval = PendingApproval(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            user_oid="u1",
            tool_name="run_shell",
            tool_args_json='{"command":"ls"}',
            reason="List files",
        )
        db_session.add(approval)
        db_session.commit()
        db_session.refresh(approval)
        assert approval.id is not None
        assert approval.status == "pending"
        assert approval.resolved_at is None
