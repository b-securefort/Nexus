"""Admin endpoints for per-user usage caps (DESIGN.md §5 2026-06-14).

Architect-gated (``require_architect``; DEV_AUTH_BYPASS passes through, matching
the /api/learnings admin surface). Lets an architect view each user's weekly
spend and set or clear their individual cap.

Caps are entered and shown in CREDITS (1 credit = $0.01) to match the UI; they
are stored in USD on ``users.credit_cap_usd``. NULL = fall back to the
role/default cap. Role caps apply only at request time (roles aren't persisted),
so this resting list resolves a user's effective cap against their per-user
override or the flat default.

Endpoints:
  GET   /api/users        — list users with cap + this-week spend/remaining
  PATCH /api/users/{oid}  — set (credits) or clear (null) a user's cap
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.agent.spend import compute_remaining, resolve_cap
from app.auth.models import User
from app.config import get_settings
from app.db.engine import get_session
from app.db.models import UserRecord
from app.deps import require_architect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["admin-users"])

CREDITS_PER_USD = 100.0


def _usd_to_credits(usd: Optional[float]) -> Optional[int]:
    return None if usd is None else round(usd * CREDITS_PER_USD)


class UserUsageRow(BaseModel):
    oid: str
    email: str
    display_name: str
    # Per-user override in credits, or None when the user uses the default.
    cap_credits: Optional[int] = None
    # Resolved cap actually applied (override or default), in credits.
    effective_cap_credits: int
    spent_this_week_credits: int
    remaining_credits: int
    week_resets_at: str


class UserListResponse(BaseModel):
    items: list[UserUsageRow]
    default_cap_credits: int


class UpdateCapRequest(BaseModel):
    # Cap in credits; null clears the override (revert to default). Must be >= 0.
    cap_credits: Optional[int] = Field(default=None, ge=0)


def _row_for(session: Session, u: UserRecord) -> UserUsageRow:
    cap_usd = resolve_cap(u.credit_cap_usd, [])
    b = compute_remaining(session, u.oid, cap_usd)
    return UserUsageRow(
        oid=u.oid,
        email=u.email,
        display_name=u.display_name,
        cap_credits=_usd_to_credits(u.credit_cap_usd),
        effective_cap_credits=round(b["cap_usd"] * CREDITS_PER_USD),
        spent_this_week_credits=round(b["spent_this_week_usd"] * CREDITS_PER_USD),
        remaining_credits=round(b["remaining_usd"] * CREDITS_PER_USD),
        week_resets_at=b["week_resets_at"],
    )


@router.get("", response_model=UserListResponse)
async def list_users(user: User = Depends(require_architect)):
    """List all users with their cap and current-week spend (admin only)."""
    with get_session() as session:
        users = session.exec(select(UserRecord).order_by(UserRecord.email)).all()  # type: ignore[arg-type]
        items = [_row_for(session, u) for u in users]
    default_cap = round(get_settings().USAGE_WEEKLY_CAP_USD_DEFAULT * CREDITS_PER_USD)
    return UserListResponse(items=items, default_cap_credits=default_cap)


@router.patch("/{oid}", response_model=UserUsageRow)
async def update_user_cap(
    oid: str, body: UpdateCapRequest, user: User = Depends(require_architect)
):
    """Set a user's weekly cap (credits) or clear it (null → role/default)."""
    with get_session() as session:
        u = session.exec(select(UserRecord).where(UserRecord.oid == oid)).first()
        if u is None:
            raise HTTPException(404, detail="User not found")
        u.credit_cap_usd = (
            None if body.cap_credits is None else body.cap_credits / CREDITS_PER_USD
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        logger.info(
            "Admin %s set cap for user %s to %s credits",
            user.oid, oid, body.cap_credits if body.cap_credits is not None else "default",
        )
        return _row_for(session, u)
