"""Conversation API endpoints."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth.models import User
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
    """Soft delete a conversation."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        conversation.deleted_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()
        return {"status": "ok"}


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
