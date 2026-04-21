"""Tests for approval state machine."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from app.agent.approvals import (
    create_pending_approval,
    resolve_approval,
    get_pending_approval_for_conversation,
    _approval_events,
    _approval_results,
)
from app.db.models import PendingApproval


class TestApprovalStateMachine:
    """Test approval state transitions."""

    def test_create_pending(self, db_session):
        approval = create_pending_approval(
            session=db_session,
            conversation_id=1,
            user_oid="user-1",
            tool_name="run_shell",
            tool_args_json='{"command": "ls"}',
            reason="List files",
        )
        assert approval.status == "pending"
        assert approval.tool_name == "run_shell"
        assert approval.id in _approval_events

    def test_approve(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "run_shell", '{}', "test"
        )
        resolved = resolve_approval(db_session, approval.id, "approve")
        assert resolved is True

        # Check DB state
        row = db_session.get(PendingApproval, approval.id)
        assert row.status == "approved"
        assert row.resolved_at is not None

    def test_deny(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "run_shell", '{}', "test"
        )
        resolved = resolve_approval(db_session, approval.id, "deny")
        assert resolved is True

        row = db_session.get(PendingApproval, approval.id)
        assert row.status == "denied"

    def test_cannot_resolve_twice(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "run_shell", '{}', "test"
        )
        resolve_approval(db_session, approval.id, "approve")
        result = resolve_approval(db_session, approval.id, "deny")
        assert result is False  # Already resolved

    def test_get_pending_for_conversation(self, db_session):
        create_pending_approval(
            db_session, 1, "user-1", "run_shell", '{}', "test"
        )
        pending = get_pending_approval_for_conversation(db_session, 1)
        assert pending is not None
        assert pending.status == "pending"

    def test_no_pending_after_resolve(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "run_shell", '{}', "test"
        )
        resolve_approval(db_session, approval.id, "approve")
        pending = get_pending_approval_for_conversation(db_session, 1)
        assert pending is None


class TestSystemPromptComposition:
    """Test system prompt composition."""

    def test_compose_system_prompt(self):
        from app.agent.orchestrator import _compose_system_prompt
        from app.skills.models import Skill
        from app.auth.models import User

        skill = Skill(
            id="shared:test",
            name="test",
            display_name="Test",
            description="",
            system_prompt="You are a test assistant.",
            tools=[],
        )
        user = User(oid="u1", email="test@test.com", display_name="Test User")

        prompt = _compose_system_prompt(skill, user)
        assert "You are a test assistant." in prompt
        assert "Test User" in prompt
        assert "test@test.com" in prompt
