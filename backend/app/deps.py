"""
FastAPI dependencies for dependency injection.
"""

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.auth.entra import get_current_user
from app.auth.models import User
from app.config import get_settings
from app.db.engine import get_session


async def current_user(request: Request) -> User:
    """Get the authenticated user from the request."""
    return await get_current_user(request)


async def require_architect(user: User = Depends(current_user)) -> User:
    """Authenticated user MUST hold the `architect` Entra App Role.

    Dev bypass passes through — DEV_AUTH_BYPASS=true is for local dev where
    the dev user has no real roles. In that mode the same short-circuit
    applied by `app/auth/rbac.py` filter functions applies here too.

    Used by admin endpoints (e.g. /api/learnings) that should only be
    callable by users with the architect role. Engineer-role users get 403.
    """
    if get_settings().DEV_AUTH_BYPASS:
        return user
    if "architect" not in user.roles:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires the architect Entra App Role.",
        )
    return user


async def require_superadmin(user: User = Depends(current_user)) -> User:
    """Authenticated user MUST hold the `superadmin` Entra App Role.

    The top tier (DESIGN.md §5 2026-06-15): architect's full access plus the
    forensic audit log. Used to gate the read-only /api/audit endpoint. Architect-
    and engineer-role users get 403 — the reviewers who read *what ran* must outrank
    the operators who *can run* destructive commands. Fail-closed: absent the
    explicit role, no access. DEV_AUTH_BYPASS passes through, matching
    `require_architect`.
    """
    if get_settings().DEV_AUTH_BYPASS:
        return user
    if "superadmin" not in user.roles:
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires the superadmin Entra App Role.",
        )
    return user


def db_session() -> Session:
    """Get a database session."""
    with get_session() as session:
        yield session
