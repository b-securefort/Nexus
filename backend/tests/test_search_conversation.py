"""Tests for the search_conversation recall tool and the high-tier model
routing settings added alongside the context-economy rework."""

import json

import pytest

from app.auth.models import User
from app.config import Settings
from app.db.models import Conversation, Message
from app.tools.base import set_conversation_id
from app.tools.generic.search_conversation import SearchConversationTool

_USER = User(oid="test-user", email="t@t", display_name="T")


@pytest.fixture
def tool():
    return SearchConversationTool()


@pytest.fixture
def seeded_conv(db_session, monkeypatch):
    """A conversation with messages, with get_session patched to the test DB
    and the conversation-id ContextVar set (as the orchestrator does)."""
    conv = Conversation(
        user_oid="test-user", title="t", skill_id="s",
        skill_snapshot_json="{}",
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    contents = [
        ("user", "set up storage account stgnexusprod01 in westus2", None),
        ("assistant", "creating it now", None),
        ("tool", json.dumps({"status": "success", "tool": "az_cli",
                             "data": "Created stgnexusprod01 id=/subs/abc-123"}), "az_cli"),
        ("user", "now add a keyvault", None),
    ]
    for role, content, tool_name in contents:
        m = Message(conversation_id=conv.id, role=role, content=content,
                    tool_name=tool_name)
        db_session.add(m)
    db_session.commit()

    from contextlib import contextmanager

    @contextmanager
    def _fake_get_session():
        yield db_session

    import app.db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_session", _fake_get_session)

    set_conversation_id(conv.id)
    yield conv
    set_conversation_id(None)


class TestSearchConversation:
    def test_finds_exact_value_in_old_tool_output(self, tool, seeded_conv):
        out = tool.execute({"query": "stgnexusprod01"}, _USER)
        data = json.loads(out)
        assert data["results"], out
        snippets = " ".join(r["snippet"] for r in data["results"])
        assert "stgnexusprod01" in snippets

    def test_multi_word_query_is_anded(self, tool, seeded_conv):
        out = tool.execute({"query": "storage westus2"}, _USER)
        data = json.loads(out)
        assert len(data["results"]) == 1
        assert data["results"][0]["role"] == "user"

    def test_no_match_returns_guidance(self, tool, seeded_conv):
        out = tool.execute({"query": "kubernetes"}, _USER)
        data = json.loads(out)
        assert data["results"] == []
        assert "No stored message" in data["message"]

    def test_requires_query(self, tool, seeded_conv):
        assert tool.execute({}, _USER).startswith("Error")

    def test_requires_conversation_context(self, tool):
        set_conversation_id(None)
        out = tool.execute({"query": "x"}, _USER)
        assert out.startswith("Error")

    def test_max_results_capped(self, tool, seeded_conv, db_session):
        for i in range(15):
            db_session.add(Message(
                conversation_id=seeded_conv.id, role="tool",
                content=f"repeated-marker hit {i}", tool_name="az_cli",
            ))
        db_session.commit()
        out = tool.execute({"query": "repeated-marker", "max_results": 99}, _USER)
        data = json.loads(out)
        assert len(data["results"]) == 10  # hard cap

    def test_no_approval_required(self, tool):
        assert tool.requires_approval is False


class TestHighTierModelRouting:
    def test_defaults_to_base_deployment(self):
        s = Settings(AZURE_OPENAI_DEPLOYMENT="gpt-5.4-mini",
                     AZURE_OPENAI_DEPLOYMENT_HIGH="")
        assert s.chat_deployment == "gpt-5.4-mini"
        assert s.chat_api_version == s.AZURE_OPENAI_API_VERSION

    def test_high_deployment_wins_when_configured(self):
        s = Settings(
            AZURE_OPENAI_DEPLOYMENT="gpt-5.4-mini",
            AZURE_OPENAI_DEPLOYMENT_HIGH="gpt-5.4",
            AZURE_OPENAI_API_VERSION_HIGH="2025-04-01-preview",
            AZURE_OPENAI_CONTEXT_WINDOW_TOKENS_HIGH=400000,
        )
        assert s.chat_deployment == "gpt-5.4"
        assert s.chat_api_version == "2025-04-01-preview"
        # Explicit config wins over the substring table (which would say
        # 128K for any "gpt-5.4*" name).
        assert s.chat_context_window == 400000

    def test_high_api_version_falls_back_to_base(self):
        s = Settings(
            AZURE_OPENAI_DEPLOYMENT_HIGH="gpt-5.4",
            AZURE_OPENAI_API_VERSION="2024-10-21",
            AZURE_OPENAI_API_VERSION_HIGH="",
        )
        assert s.chat_api_version == "2024-10-21"
