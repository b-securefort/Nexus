"""Chat API endpoints — SSE streaming chat + approval handling + resume."""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlmodel import select

from app.agent.approvals import (
    get_pending_approval_for_conversation,
    resolve_approval,
)
from app.agent.orchestrator import handle_chat
from app.agent.streaming import sse_approval_required, sse_done, sse_error
from app.auth.models import User
from app.db.engine import get_session
from app.db.models import Conversation, Message, PendingApproval, UserRecord
from app.deps import current_user
from app.config import get_settings
from app.skills.loader import load_skill
from app.skills.models import Skill

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# In-memory rate limiter: user_oid -> list of request timestamps
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(user_oid: str) -> None:
    """Check per-user rate limit. Raises HTTPException if exceeded."""
    settings = get_settings()
    now = time.time()
    window = 60.0

    # Clean old entries
    timestamps = _rate_limit_store[user_oid]
    _rate_limit_store[user_oid] = [t for t in timestamps if now - t < window]

    if len(_rate_limit_store[user_oid]) >= settings.CHAT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")

    _rate_limit_store[user_oid].append(now)


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    skill_id: Optional[str] = None
    message: str

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        if len(v) > 16000:
            raise ValueError("Message too long (max 16000 characters)")
        return v


class ApprovalRequest(BaseModel):
    action: str  # "approve" | "deny"


def _upsert_user(user: User) -> None:
    """Upsert user record, throttled to once per minute."""
    with get_session() as session:
        stmt = select(UserRecord).where(UserRecord.oid == user.oid)
        record = session.exec(stmt).first()
        now = datetime.now(timezone.utc)

        if not record:
            record = UserRecord(
                oid=user.oid,
                email=user.email,
                display_name=user.display_name,
            )
            session.add(record)
            session.commit()
        elif (now - record.last_seen_at.replace(tzinfo=timezone.utc)).total_seconds() > 60:
            record.last_seen_at = now
            record.email = user.email
            record.display_name = user.display_name
            session.add(record)
            session.commit()


def _skill_to_snapshot(skill: Skill) -> str:
    """Serialize a skill to JSON for the conversation snapshot."""
    return json.dumps({
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tools": skill.tools,
        "source": skill.source,
    })


@router.post("/chat")
async def chat(body: ChatRequest, user: User = Depends(current_user)):
    """Start or continue a chat conversation. Returns SSE stream."""
    _check_rate_limit(user.oid)
    _upsert_user(user)

    with get_session() as session:
        if body.conversation_id:
            # Continue existing conversation
            conversation = session.get(Conversation, body.conversation_id)
            if not conversation or conversation.deleted_at is not None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if conversation.user_oid != user.oid:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            # New conversation
            if not body.skill_id:
                raise HTTPException(status_code=400, detail="skill_id is required for new conversations")

            skill = load_skill(body.skill_id, user.oid, session)

            # Generate title from first message
            title = body.message[:80].strip()
            if len(body.message) > 80:
                title += "..."

            conversation = Conversation(
                user_oid=user.oid,
                title=title,
                skill_id=body.skill_id,
                skill_snapshot_json=_skill_to_snapshot(skill),
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

    async def event_stream():
        with get_session() as session:
            try:
                async for event in handle_chat(session, conversation, body.message, user):
                    yield event
            except Exception as e:
                logger.error("Chat stream error: %s", str(e), exc_info=True)
                yield sse_error("An internal error occurred")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/approvals/{approval_id}")
async def handle_approval(approval_id: str, body: ApprovalRequest, user: User = Depends(current_user)):
    """Approve or deny a pending tool call."""
    if body.action not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'deny'")

    with get_session() as session:
        # Verify the approval belongs to this user
        stmt = select(PendingApproval).where(PendingApproval.id == approval_id)
        approval = session.exec(stmt).first()

        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        if approval.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")
        if approval.status != "pending":
            raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

        resolved = resolve_approval(session, approval_id, body.action)
        if not resolved:
            raise HTTPException(status_code=500, detail="Failed to resolve approval")

    return {"status": "ok"}


@router.get("/chat/resume")
async def resume_chat(conversation_id: int, user: User = Depends(current_user)):
    """Reconnect to a paused chat stream (e.g., after page reload during pending approval)."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        pending = get_pending_approval_for_conversation(session, conversation_id)

    async def event_stream():
        if pending:
            yield sse_approval_required(
                pending.id,
                pending.tool_name,
                json.loads(pending.tool_args_json),
                pending.reason,
            )
        else:
            yield sse_done(conversation_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
