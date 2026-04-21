"""
SQLModel table definitions per §7.1 of the PRD.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRecord(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    oid: str = Field(unique=True, nullable=False, index=True)
    email: str = Field(nullable=False)
    display_name: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_oid: str = Field(nullable=False, index=True)
    title: str = Field(nullable=False)
    skill_id: str = Field(nullable=False)
    skill_snapshot_json: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(nullable=False, index=True)
    role: str = Field(nullable=False)  # "user" | "assistant" | "tool"
    content: str = Field(nullable=False)
    tool_calls_json: Optional[str] = Field(default=None)
    tool_call_id: Optional[str] = Field(default=None)
    tool_name: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class PersonalSkill(SQLModel, table=True):
    __tablename__ = "personal_skills"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_oid: str = Field(nullable=False, index=True)
    name: str = Field(nullable=False)
    display_name: str = Field(nullable=False)
    description: str = Field(default="", nullable=False)
    system_prompt: str = Field(nullable=False)
    tools_json: str = Field(default="[]", nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None)


class PendingApproval(SQLModel, table=True):
    __tablename__ = "pending_approvals"

    id: str = Field(primary_key=True)  # UUID
    conversation_id: int = Field(nullable=False, index=True)
    user_oid: str = Field(nullable=False)
    tool_name: str = Field(nullable=False)
    tool_args_json: str = Field(nullable=False)
    reason: str = Field(nullable=False)
    status: str = Field(nullable=False, default="pending")  # pending | approved | denied | expired
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    resolved_at: Optional[datetime] = Field(default=None)
    result_json: Optional[str] = Field(default=None)
