"""Forensic audit-log read API (DESIGN.md §5 2026-06-15).

Read-only surface over the append-only `tool_executions` table, gated to the
`superadmin` Entra App Role (`require_superadmin`). There is deliberately NO write,
update, or delete endpoint — the table is append-only and its only deleter is the
time-based `_audit_log_prune` sweeper, so an audited operator (who may themselves
hold a powerful role) can never strip their own trail through the admin door.

For the 1-2 reviewers reconstructing an incident ("who ran what, and did it take").
A dedicated admin page is deliberately deferred — JSON here (or `sqlite3` on a DB
backup) is enough for occasional forensic review.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import select

from app.auth.models import User
from app.db.engine import get_session
from app.db.models import ToolExecution
from app.deps import require_superadmin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditRow(BaseModel):
    id: int
    user_oid: str
    user_email: str
    conversation_id: Optional[int]
    tool_name: str
    rendered_command: str
    review_fingerprint: Optional[str]
    outcome: str
    risk_level: Optional[str]
    reason: Optional[str]
    created_at: str


@router.get("", response_model=list[AuditRow])
async def list_audit_events(
    user: User = Depends(require_superadmin),
    user_oid: Optional[str] = Query(None, description="Filter to one actor's oid"),
    outcome: Optional[str] = Query(
        None, description="success|error|denied|blocked|integrity_failed"
    ),
    tool_name: Optional[str] = Query(None, description="Filter to one tool"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List audit rows, newest first. Optional actor / outcome / tool filters."""
    with get_session() as session:
        stmt = select(ToolExecution).order_by(ToolExecution.created_at.desc())
        if user_oid:
            stmt = stmt.where(ToolExecution.user_oid == user_oid)
        if outcome:
            stmt = stmt.where(ToolExecution.outcome == outcome)
        if tool_name:
            stmt = stmt.where(ToolExecution.tool_name == tool_name)
        rows = session.exec(stmt.offset(offset).limit(limit)).all()

    return [
        AuditRow(
            id=r.id,
            user_oid=r.user_oid,
            user_email=r.user_email,
            conversation_id=r.conversation_id,
            tool_name=r.tool_name,
            rendered_command=r.rendered_command,
            review_fingerprint=r.review_fingerprint,
            outcome=r.outcome,
            risk_level=r.risk_level,
            reason=r.reason,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
