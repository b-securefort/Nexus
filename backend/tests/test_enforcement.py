"""Tests for spend-cap enforcement (DESIGN.md §5 2026-06-14).

`check_over_cap` is the enforcement decision (flags + over-threshold). The
pre-flight gate in handle_chat short-circuits BEFORE any LLM call, so it's
testable without mocking Azure.
"""

import pytest
from sqlmodel import Session, SQLModel, select

from app.agent.spend import check_over_cap
from app.auth.models import User
from app.config import get_settings
from app.db.engine import get_engine
from app.db.models import Conversation, Message, UsageEvent


@pytest.fixture(autouse=True)
def _schema():
    from app.main import _apply_lightweight_migrations
    SQLModel.metadata.create_all(get_engine())
    _apply_lightweight_migrations(get_engine())
    yield


def _seed_spend(oid: str, completion_tokens: int):
    with Session(get_engine()) as s:
        s.add(UsageEvent(
            user_oid=oid, conversation_id=None, deployment="gpt-4o-mini",
            prompt_tokens=0, cached_tokens=0, completion_tokens=completion_tokens,
        ))
        s.commit()


def _cleanup(oid: str):
    with Session(get_engine()) as s:
        for ev in s.exec(select(UsageEvent).where(UsageEvent.user_oid == oid)).all():
            s.delete(ev)
        for c in s.exec(select(Conversation).where(Conversation.user_oid == oid)).all():
            for m in s.exec(select(Message).where(Message.conversation_id == c.id)).all():
                s.delete(m)
            s.delete(c)
        s.commit()


# ── check_over_cap ───────────────────────────────────────────────────────────

def test_disabled_enforcement_never_blocks(monkeypatch):
    oid = "covercap-disabled"
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENABLED", True)
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENFORCED", False)
    monkeypatch.setattr(get_settings(), "USAGE_WEEKLY_CAP_USD_DEFAULT", 0.01)
    _seed_spend(oid, 1_000_000)  # $0.60 spend ≫ $0.01 cap
    try:
        assert check_over_cap(oid, []) is None  # not enforced → proceed
    finally:
        _cleanup(oid)


def test_enforced_under_cap_passes(monkeypatch):
    oid = "covercap-under"
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENABLED", True)
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENFORCED", True)
    monkeypatch.setattr(get_settings(), "USAGE_WEEKLY_CAP_USD_DEFAULT", 20.0)
    _seed_spend(oid, 1_000_000)  # $0.60 ≪ $20
    try:
        assert check_over_cap(oid, []) is None
    finally:
        _cleanup(oid)


def test_enforced_over_cap_blocks(monkeypatch):
    oid = "covercap-over"
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENABLED", True)
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENFORCED", True)
    monkeypatch.setattr(get_settings(), "USAGE_WEEKLY_CAP_USD_DEFAULT", 0.01)
    _seed_spend(oid, 1_000_000)
    try:
        b = check_over_cap(oid, [])
        assert b is not None
        assert b["remaining_usd"] <= 0
    finally:
        _cleanup(oid)


# ── pre-flight gate in handle_chat ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_blocks_turn_when_over_cap(monkeypatch):
    from app.agent.orchestrator import handle_chat

    oid = "enforce-preflight"
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENABLED", True)
    monkeypatch.setattr(get_settings(), "USAGE_CAP_ENFORCED", True)
    monkeypatch.setattr(get_settings(), "USAGE_WEEKLY_CAP_USD_DEFAULT", 0.01)
    _seed_spend(oid, 1_000_000)

    with Session(get_engine()) as s:
        conv = Conversation(user_oid=oid, title="t", skill_id="kb-searcher", skill_snapshot_json="{}")
        s.add(conv)
        s.commit()
        s.refresh(conv)
        conv_id = conv.id

    user = User(oid=oid, email="e@x", display_name="E", roles=[])
    events = []
    try:
        with Session(get_engine()) as s:
            conv = s.get(Conversation, conv_id)
            async for ev in handle_chat(s, conv, "hello", user):
                events.append(ev)
        joined = "".join(events).lower()
        # An error event was emitted and the turn ended — no assistant reply.
        assert "error" in joined
        assert "usage cap" in joined
        assert "done" in joined
    finally:
        _cleanup(oid)
