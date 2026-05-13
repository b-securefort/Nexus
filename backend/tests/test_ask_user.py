"""Tests for the ask_user tool, validate_questions helper, and the question
state machine. The orchestrator integration is exercised end-to-end from
test_api.py; here we cover the unit pieces.
"""

import asyncio
import json

import pytest

from app.agent.questions import (
    create_pending_question,
    expire_stale_questions,
    get_pending_question_for_conversation,
    resolve_question,
    wait_for_answer,
)
from app.tools.ask_user import AskUserTool, validate_questions


# ── validate_questions ─────────────────────────────────────────────────────

def test_validate_accepts_well_formed_payload():
    out, err = validate_questions([
        {
            "question": "Which topology?",
            "header": "Topology",
            "options": [
                {"label": "Hub & spoke", "description": "Standard pattern"},
                {"label": "Spoke only"},
            ],
            "multi_select": False,
        }
    ])
    assert err is None
    assert len(out) == 1
    q = out[0]
    assert q["question"] == "Which topology?"
    assert q["header"] == "Topology"
    assert q["multi_select"] is False
    assert len(q["options"]) == 2
    assert q["options"][1]["description"] == ""  # filled with empty default


def test_validate_rejects_too_many_questions():
    payload = [
        {
            "question": f"Q{i}?",
            "header": f"H{i}",
            "options": [{"label": "A"}, {"label": "B"}],
        }
        for i in range(5)
    ]
    _, err = validate_questions(payload)
    assert "1-4" in err


def test_validate_rejects_too_few_options():
    _, err = validate_questions([
        {
            "question": "Why?",
            "header": "Why",
            "options": [{"label": "Because"}],
        }
    ])
    assert "options" in err and "2-4" in err


def test_validate_rejects_too_many_options():
    _, err = validate_questions([
        {
            "question": "Why?",
            "header": "Why",
            "options": [{"label": l} for l in ("A", "B", "C", "D", "E")],
        }
    ])
    assert "options" in err


def test_validate_rejects_long_header():
    _, err = validate_questions([
        {
            "question": "Why?",
            "header": "A" * 30,
            "options": [{"label": "A"}, {"label": "B"}],
        }
    ])
    assert "header" in err


def test_validate_rejects_missing_label():
    _, err = validate_questions([
        {
            "question": "Why?",
            "header": "Why",
            "options": [{"label": ""}, {"label": "B"}],
        }
    ])
    assert "label" in err


def test_validate_rejects_non_list():
    _, err = validate_questions("not a list")
    assert "list" in err


# ── AskUserTool.execute fallback ───────────────────────────────────────────

def test_tool_execute_returns_orchestrator_required_error():
    """Direct execute() (i.e. without going through the streaming orchestrator)
    must NOT block waiting for input — it should return a clear error."""
    from app.auth.models import User
    tool = AskUserTool()
    payload = {
        "questions": [
            {
                "question": "Which?",
                "header": "Pick",
                "options": [{"label": "A"}, {"label": "B"}],
            }
        ]
    }
    result = tool.execute(payload, User(oid="t", email="t@t.com", display_name="t"))
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "orchestrator" in parsed["message"]


def test_tool_execute_with_invalid_payload_returns_error():
    from app.auth.models import User
    tool = AskUserTool()
    result = tool.execute(
        {"questions": [{"question": "x"}]},  # missing header/options
        User(oid="t", email="t@t.com", display_name="t"),
    )
    assert result.startswith("Error:")


# ── Question state machine (in-memory + DB) ────────────────────────────────

@pytest.mark.asyncio
async def test_question_full_round_trip(db_session):
    questions = [
        {
            "question": "Pick one",
            "header": "Pick",
            "options": [{"label": "A", "description": ""}, {"label": "B", "description": ""}],
            "multi_select": False,
        }
    ]
    record = create_pending_question(
        session=db_session,
        conversation_id=1,
        user_oid="u",
        questions=questions,
    )
    assert record.status == "pending"

    async def waiter():
        return await wait_for_answer(record.id)

    task = asyncio.create_task(waiter())
    # Yield once so the waiter actually parks on the event.
    await asyncio.sleep(0)

    assert resolve_question(
        db_session, record.id,
        [{"question": "Pick one", "selected": ["A"]}],
    ) is True

    status, answers = await asyncio.wait_for(task, timeout=2)
    assert status == "answered"
    assert answers == [{"question": "Pick one", "selected": ["A"]}]

    # DB is also updated
    db_session.refresh(record)
    assert record.status == "answered"
    assert record.answers_json is not None


@pytest.mark.asyncio
async def test_question_resolve_after_expiry_returns_false(db_session, monkeypatch):
    """If a question is expired by the sweeper, a late resolve_question call
    must noop (return False) rather than corrupt state."""
    from app.config import get_settings

    record = create_pending_question(
        session=db_session, conversation_id=2, user_oid="u",
        questions=[{
            "question": "Q?", "header": "H",
            "options": [{"label": "A"}, {"label": "B"}],
            "multi_select": False,
        }],
    )

    # Force the timeout to ~0 so the sweeper expires immediately.
    settings = get_settings()
    monkeypatch.setattr(settings, "TOOL_APPROVAL_TIMEOUT_SECONDS", 0, raising=False)

    await expire_stale_questions(db_session)
    db_session.refresh(record)
    assert record.status == "expired"

    ok = resolve_question(
        db_session, record.id,
        [{"question": "Q?", "selected": ["A"]}],
    )
    assert ok is False


def test_get_pending_question_for_conversation(db_session):
    rec = create_pending_question(
        session=db_session, conversation_id=99, user_oid="u",
        questions=[{
            "question": "Q?", "header": "H",
            "options": [{"label": "A"}, {"label": "B"}],
            "multi_select": False,
        }],
    )
    found = get_pending_question_for_conversation(db_session, 99)
    assert found is not None and found.id == rec.id

    resolve_question(db_session, rec.id, [
        {"question": "Q?", "selected": ["A"]},
    ])

    # Once resolved, no pending record for the conversation
    assert get_pending_question_for_conversation(db_session, 99) is None
