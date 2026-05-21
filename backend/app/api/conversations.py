"""Conversation API endpoints."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth.models import User
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import Conversation, Message
from app.deps import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationResponse(BaseModel):
    id: int
    title: str
    skill_id: str
    created_at: str
    updated_at: str


class ConversationDetailResponse(BaseModel):
    id: int
    title: str
    skill_id: str
    skill_snapshot_json: str
    created_at: str
    updated_at: str
    messages: list[dict]


class ConversationUpdateRequest(BaseModel):
    title: str


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(user: User = Depends(current_user)):
    """List current user's conversations, newest first, excluding soft-deleted."""
    with get_session() as session:
        stmt = (
            select(Conversation)
            .where(Conversation.user_oid == user.oid)
            .where(Conversation.deleted_at.is_(None))  # type: ignore
            .order_by(Conversation.updated_at.desc())  # type: ignore
        )
        conversations = session.exec(stmt).all()
        return [
            ConversationResponse(
                id=c.id,
                title=c.title,
                skill_id=c.skill_id,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
            )
            for c in conversations
        ]


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(conversation_id: int, user: User = Depends(current_user)):
    """Fetch a conversation with its messages."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)  # type: ignore
        )
        messages = session.exec(stmt).all()

        return ConversationDetailResponse(
            id=conversation.id,
            title=conversation.title,
            skill_id=conversation.skill_id,
            skill_snapshot_json=conversation.skill_snapshot_json,
            created_at=conversation.created_at.isoformat(),
            updated_at=conversation.updated_at.isoformat(),
            messages=[
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "tool_calls_json": m.tool_calls_json,
                    "tool_call_id": m.tool_call_id,
                    "tool_name": m.tool_name,
                    "attachments_json": m.attachments_json,
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        )


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: int, user: User = Depends(current_user)):
    """Soft delete a conversation and unlink associated upload/output files."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        # Collect file paths from message attachments before soft-deleting
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        messages = session.exec(stmt).all()

        settings = get_settings()
        upload_dir = Path(settings.UPLOAD_DIR).resolve()
        output_dir = Path("output").resolve()

        files_to_delete: list[Path] = []
        for msg in messages:
            if not msg.attachments_json:
                continue
            try:
                attachments = json.loads(msg.attachments_json)
            except json.JSONDecodeError:
                continue
            for att in attachments:
                filename = att.get("filename", "")
                url = att.get("url", "")
                if not filename:
                    continue
                if url.startswith("/api/output/"):
                    # Output file (diagram renders, generated files)
                    candidate = (output_dir / filename).resolve()
                    if candidate.is_relative_to(output_dir):
                        files_to_delete.append(candidate)
                else:
                    # Upload file
                    candidate = (upload_dir / filename).resolve()
                    if candidate.is_relative_to(upload_dir):
                        files_to_delete.append(candidate)

        conversation.deleted_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()

    # Unlink files after the DB transaction commits — don't block delete on FS errors
    for file_path in files_to_delete:
        try:
            file_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete file %s: %s", file_path, exc)

    return {"status": "ok"}


class LeaseStatusResponse(BaseModel):
    """A4 — Reports whether a chat turn is currently in flight, and whether
    the worker that started it appears to have died.

    `state`:
      - "idle"        — no turn in flight
      - "active"      — heartbeat is fresh; the turn is progressing normally
      - "stale"       — heartbeat is older than the staleness threshold; the
                        worker likely died. The UI should offer a "Restart
                        turn" affordance.

    `seconds_since_heartbeat` is None when state == "idle".
    """
    state: str
    seconds_since_heartbeat: Optional[float] = None
    lease_owner: Optional[str] = None
    last_user_message_id: Optional[int] = None


@router.get("/{conversation_id}/lease", response_model=LeaseStatusResponse)
async def get_conversation_lease(
    conversation_id: int, user: User = Depends(current_user)
):
    """Report the current orchestrator-lease state for a conversation.

    The frontend polls this when a tool call has been pending approval for a
    long time. If the worker that started the turn died, this returns
    "stale" and the UI can prompt the user to restart the turn from the
    last user message (re-issued through /api/chat as a new turn — no
    auto-reconstruction of synthetic retry/drawio state, per A4's revised
    guidance).
    """
    from app.agent.orchestrator import LEASE_STALE_AFTER_SECONDS

    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        # Look up the most recent user message for the restart hint
        last_user_msg = session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role == "user")
            .order_by(Message.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        ).first()

        hb = conversation.lease_heartbeat_at
        if hb is None:
            return LeaseStatusResponse(
                state="idle",
                seconds_since_heartbeat=None,
                lease_owner=None,
                last_user_message_id=last_user_msg.id if last_user_msg else None,
            )

        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - hb).total_seconds()
        state = "stale" if delta > LEASE_STALE_AFTER_SECONDS else "active"
        return LeaseStatusResponse(
            state=state,
            seconds_since_heartbeat=round(delta, 1),
            lease_owner=conversation.lease_owner,
            last_user_message_id=last_user_msg.id if last_user_msg else None,
        )


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: int,
    body: ConversationUpdateRequest,
    user: User = Depends(current_user),
):
    """Rename a conversation."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        conversation.title = body.title
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()
        return {"status": "ok"}
