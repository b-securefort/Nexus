"""
Question state machine for ask_user tool calls. Mirrors approvals.py: the
agent triggers a question, the orchestrator awaits an asyncio Event, the
HTTP endpoint resolves it when the user submits answers, and a periodic
sweeper expires stale entries.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import PendingQuestion

logger = logging.getLogger(__name__)


# In-memory events for question resolution. Keyed by question_id.
_question_events: dict[str, asyncio.Event] = {}
# Stores the user's answer payload (list of {question, selected, notes?}) on
# resolve, so wait_for_answer can return it without re-reading the DB.
_question_results: dict[str, list[dict]] = {}


def create_pending_question(
    session: Session,
    conversation_id: int,
    user_oid: str,
    questions: list[dict],
) -> PendingQuestion:
    """Persist a new pending-question record and register the wait event.

    `questions` is the validated list of question dicts (1-4 items) the
    ask_user tool received. Each: {question, header, options:[...], multi_select}.
    """
    qid = str(uuid.uuid4())
    record = PendingQuestion(
        id=qid,
        conversation_id=conversation_id,
        user_oid=user_oid,
        questions_json=json.dumps(questions),
        status="pending",
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    _question_events[qid] = asyncio.Event()
    return record


async def wait_for_answer(question_id: str) -> tuple[str, list[dict] | None]:
    """Block until the user answers (or the question expires).

    Returns (status, answers). status is 'answered' or 'expired'. answers is
    the list of {question, selected, notes?} dicts on success, None otherwise.
    """
    settings = get_settings()
    timeout = getattr(settings, "QUESTION_TIMEOUT_SECONDS", None) or getattr(
        settings, "TOOL_APPROVAL_TIMEOUT_SECONDS", 300
    )

    event = _question_events.get(question_id)
    if event is None:
        return "expired", None

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        # The HTTP layer never resolved it - mark expired in memory; the
        # sweeper will reflect the same in DB on its next pass.
        _question_results.pop(question_id, None)
        _question_events.pop(question_id, None)
        return "expired", None

    answers = _question_results.pop(question_id, None)
    _question_events.pop(question_id, None)
    if answers is None:
        return "expired", None
    return "answered", answers


def resolve_question(
    session: Session, question_id: str, answers: list[dict]
) -> bool:
    """Record the user's answers and unblock the awaiting orchestrator.

    `answers` is a list of {question, selected:[label,...], notes?:str} dicts,
    one per question that was asked. Returns True if the record existed and
    was pending; False if it was already resolved or never existed.
    """
    stmt = select(PendingQuestion).where(PendingQuestion.id == question_id)
    record = session.exec(stmt).first()
    if record is None or record.status != "pending":
        return False

    record.status = "answered"
    record.resolved_at = datetime.now(timezone.utc)
    record.answers_json = json.dumps(answers)
    session.add(record)
    session.commit()

    _question_results[question_id] = answers
    event = _question_events.get(question_id)
    if event is not None:
        event.set()

    logger.info("Question %s answered with %d response(s)", question_id, len(answers))
    return True


def get_pending_question_for_conversation(
    session: Session, conversation_id: int
) -> PendingQuestion | None:
    stmt = (
        select(PendingQuestion)
        .where(PendingQuestion.conversation_id == conversation_id)
        .where(PendingQuestion.status == "pending")
    )
    return session.exec(stmt).first()


async def expire_stale_questions(session: Session) -> None:
    """Mark stale pending questions as expired. Runs in the background sweeper."""
    settings = get_settings()
    timeout = getattr(settings, "QUESTION_TIMEOUT_SECONDS", None) or getattr(
        settings, "TOOL_APPROVAL_TIMEOUT_SECONDS", 300
    )
    now = datetime.now(timezone.utc)

    stmt = select(PendingQuestion).where(PendingQuestion.status == "pending")
    records = session.exec(stmt).all()

    for record in records:
        created = record.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()
        if age > timeout:
            record.status = "expired"
            record.resolved_at = now
            session.add(record)

            event = _question_events.get(record.id)
            if event is not None:
                _question_results.pop(record.id, None)  # ensure wait sees None
                event.set()

            logger.info("Question %s expired (age %.0fs)", record.id, age)

    session.commit()
