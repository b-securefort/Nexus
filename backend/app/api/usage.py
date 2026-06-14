"""Usage / spend-cap API (DESIGN.md §5 2026-06-14).

Read-only surface for the frontend "weekly budget remaining" indicator. Returns
the current user's resolved cap, this-week spend, carried-forward debt, and the
remaining budget. Enforcement is separate — this endpoint never blocks anything.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agent.spend import remaining_for_user
from app.auth.models import User
from app.config import get_settings
from app.db.engine import get_session
from app.deps import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])


class UsageRemainingResponse(BaseModel):
    enabled: bool
    cap_usd: Optional[float] = None
    spent_this_week_usd: Optional[float] = None
    carryover_debt_usd: Optional[float] = None
    remaining_usd: Optional[float] = None
    remaining_fraction: Optional[float] = None
    week_resets_at: Optional[str] = None


@router.get("/me", response_model=UsageRemainingResponse)
async def my_usage(user: User = Depends(current_user)):
    """Current user's weekly spend-cap status for the budget indicator."""
    if not get_settings().USAGE_CAP_ENABLED:
        return UsageRemainingResponse(enabled=False)
    with get_session() as session:
        data = remaining_for_user(session, user.oid, user.roles or [])
    return UsageRemainingResponse(enabled=True, **data)
