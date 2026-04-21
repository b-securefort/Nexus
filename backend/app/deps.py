"""
FastAPI dependencies for dependency injection.
"""

from fastapi import Depends, Request
from sqlmodel import Session

from app.auth.entra import get_current_user
from app.auth.models import User
from app.db.engine import get_session


async def current_user(request: Request) -> User:
    """Get the authenticated user from the request."""
    return await get_current_user(request)


def db_session() -> Session:
    """Get a database session."""
    with get_session() as session:
        yield session
