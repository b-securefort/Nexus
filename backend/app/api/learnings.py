"""
Admin endpoints for the agent learnings store.

All endpoints require the `architect` Entra App Role (via `require_architect`).
DEV_AUTH_BYPASS=true passes through the role check for local development —
the dev user has no real roles but should still be able to inspect/manage
the learnings store.

Endpoints:
  GET    /api/learnings           — list, filterable + paginated
  GET    /api/learnings/{id}      — single entry with full details
  PATCH  /api/learnings/{id}      — change status (manual promote / archive / reactivate)
  DELETE /api/learnings/{id}      — hard delete (for noise cleanup)

The agent itself does NOT use these endpoints. They're an admin surface.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlmodel import Session, select

from app.auth.models import User
from app.db.engine import get_session
from app.db.models import AgentLearning
from app.deps import require_architect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learnings", tags=["learnings"])


# Statuses allowed via the admin PATCH endpoint. We deliberately omit
# `rejected` — that status is reserved for entries the LLM judge flagged
# at write time; promoting a rejected entry would re-enable poisoning.
_PATCHABLE_STATUSES = {"active", "provisional", "archived"}

_ALL_STATUSES = {"active", "provisional", "archived", "rejected"}
_ALL_TYPES = {"semantic", "procedural"}
_ALL_CATEGORIES = {"syntax-fix", "known-issue", "workaround", "best-practice", "gotcha"}


# ── Response models ─────────────────────────────────────────────────────────


class LearningSummary(BaseModel):
    """Lightweight row for list views."""
    id: int
    type: str
    category: str
    tool_name: str
    summary: str
    status: str
    validation_count: int
    failure_count: int
    recorded_at: datetime
    last_validated_at: Optional[datetime] = None
    last_retrieved_at: Optional[datetime] = None


class LearningDetail(LearningSummary):
    """Full entry — used for detail view."""
    details: str
    archived_at: Optional[datetime] = None
    originating_conversation_id: Optional[int] = None
    judge_verdict: Optional[dict] = None
    embed_model: Optional[str] = None


class LearningListResponse(BaseModel):
    items: list[LearningSummary]
    total: int  # total matching the filter (for pagination UIs)
    offset: int
    limit: int


class PatchStatusRequest(BaseModel):
    status: str = Field(description="New status: active | provisional | archived")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _PATCHABLE_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_PATCHABLE_STATUSES)}; "
                "'rejected' cannot be assigned manually (reserved for LLM-judge audit)."
            )
        return v


# ── Helpers ─────────────────────────────────────────────────────────────────


def _to_summary(row: AgentLearning) -> LearningSummary:
    return LearningSummary(
        id=row.id,  # type: ignore[arg-type]
        type=row.type,
        category=row.category,
        tool_name=row.tool_name,
        summary=row.summary,
        status=row.status,
        validation_count=row.validation_count,
        failure_count=row.failure_count,
        recorded_at=row.recorded_at,
        last_validated_at=row.last_validated_at,
        last_retrieved_at=row.last_retrieved_at,
    )


def _to_detail(row: AgentLearning) -> LearningDetail:
    judge_verdict: Optional[dict] = None
    if row.judge_verdict_json:
        try:
            judge_verdict = json.loads(row.judge_verdict_json)
        except json.JSONDecodeError:
            logger.warning("Malformed judge_verdict_json on learning id=%s", row.id)
            judge_verdict = {"_parse_error": True, "raw": row.judge_verdict_json[:500]}
    return LearningDetail(
        id=row.id,  # type: ignore[arg-type]
        type=row.type,
        category=row.category,
        tool_name=row.tool_name,
        summary=row.summary,
        details=row.details,
        status=row.status,
        validation_count=row.validation_count,
        failure_count=row.failure_count,
        recorded_at=row.recorded_at,
        last_validated_at=row.last_validated_at,
        last_retrieved_at=row.last_retrieved_at,
        archived_at=row.archived_at,
        originating_conversation_id=row.originating_conversation_id,
        judge_verdict=judge_verdict,
        embed_model=row.embed_model,
    )


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=LearningListResponse)
async def list_learnings(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    type_filter: Optional[str] = Query(None, alias="type", description="Filter by type (semantic | procedural)"),
    category: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_architect),
):
    """List learnings, filterable and paginated.

    Default sort: most recent first.
    """
    if status_filter is not None and status_filter not in _ALL_STATUSES:
        raise HTTPException(400, detail=f"status must be one of {sorted(_ALL_STATUSES)}")
    if type_filter is not None and type_filter not in _ALL_TYPES:
        raise HTTPException(400, detail=f"type must be one of {sorted(_ALL_TYPES)}")
    if category is not None and category not in _ALL_CATEGORIES:
        raise HTTPException(400, detail=f"category must be one of {sorted(_ALL_CATEGORIES)}")

    with get_session() as session:
        stmt = select(AgentLearning)
        if status_filter:
            stmt = stmt.where(AgentLearning.status == status_filter)
        if type_filter:
            stmt = stmt.where(AgentLearning.type == type_filter)
        if category:
            stmt = stmt.where(AgentLearning.category == category)
        if tool_name:
            stmt = stmt.where(AgentLearning.tool_name == tool_name)

        # Count BEFORE applying limit/offset
        # SQLAlchemy doesn't give us a clean count from a Select directly with
        # SQLModel — use a raw count query against the same filters.
        count_sql = "SELECT COUNT(*) FROM agent_learnings WHERE 1=1"
        params: dict = {}
        if status_filter:
            count_sql += " AND status = :status"
            params["status"] = status_filter
        if type_filter:
            count_sql += " AND type = :type"
            params["type"] = type_filter
        if category:
            count_sql += " AND category = :category"
            params["category"] = category
        if tool_name:
            count_sql += " AND tool_name = :tool_name"
            params["tool_name"] = tool_name
        total = session.exec(text(count_sql).bindparams(**params)).first()
        total_int = int(total[0]) if total else 0

        stmt = stmt.order_by(AgentLearning.recorded_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
        rows = session.exec(stmt).all()

        return LearningListResponse(
            items=[_to_summary(r) for r in rows],
            total=total_int,
            offset=offset,
            limit=limit,
        )


@router.get("/{learning_id}", response_model=LearningDetail)
async def get_learning(learning_id: int, user: User = Depends(require_architect)):
    with get_session() as session:
        row = session.get(AgentLearning, learning_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Learning not found")
        return _to_detail(row)


@router.patch("/{learning_id}", response_model=LearningDetail)
async def patch_learning_status(
    learning_id: int,
    body: PatchStatusRequest,
    user: User = Depends(require_architect),
):
    """Manually change a learning's status.

    Used for:
      - Promoting a still-provisional entry the architect knows is correct
        (skips the auto-promotion validation_count threshold)
      - Demoting an active entry to archived when it's no longer correct
      - Reactivating an archived entry that turned out to be valid after all

    The `rejected` status cannot be set manually — it's reserved for the
    LLM-judge audit trail to prevent re-enabling poisoning attempts.
    """
    with get_session() as session:
        row = session.get(AgentLearning, learning_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Learning not found")
        if row.status == "rejected":
            # The judge persisted this as poisoning evidence. Re-activating
            # it would defeat the defense. Architects can still inspect it
            # via GET, but not flip its status via this API.
            raise HTTPException(
                status_code=409,
                detail="Cannot change status of a rejected learning (LLM-judge audit row).",
            )

        old_status = row.status
        row.status = body.status
        if body.status == "archived" and row.archived_at is None:
            row.archived_at = datetime.utcnow()
        elif body.status != "archived":
            row.archived_at = None
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(
            "Architect %s changed learning id=%s status %s -> %s",
            user.email, learning_id, old_status, body.status,
        )
        return _to_detail(row)


@router.delete("/{learning_id}", status_code=204)
async def delete_learning(learning_id: int, user: User = Depends(require_architect)):
    """Hard delete a learning AND its vec0 embedding row.

    Use sparingly — for noise cleanup. The audit trail of rejected entries
    typically should NOT be deleted (they document poisoning attempts).
    """
    with get_session() as session:
        row = session.get(AgentLearning, learning_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Learning not found")
        session.delete(row)
        # Clean up the vec0 sibling row — vec0 has no FK so we do it manually
        try:
            session.exec(
                text("DELETE FROM agent_learnings_vec WHERE rowid = :rid").bindparams(rid=learning_id)
            )
        except Exception:
            # If the vec0 extension isn't loaded, there's no sibling row anyway
            logger.debug("vec0 cleanup skipped for learning id=%s", learning_id)
        session.commit()
        logger.info("Architect %s deleted learning id=%s", user.email, learning_id)
    return None
