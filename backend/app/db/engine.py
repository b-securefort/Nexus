"""
SQLModel engine and session factory.
"""

from contextlib import contextmanager

from sqlmodel import Session, create_engine

from app.config import get_settings
from app.db.sqlite_vec_loader import attach_sqlite_vec

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {"check_same_thread": False}  # Required for SQLite with FastAPI
        _engine = create_engine(
            settings.DATABASE_URL,
            connect_args=connect_args,
            echo=(settings.APP_ENV == "dev"),
        )
        # Loads the sqlite-vec extension on every new connection so vec0 virtual
        # tables and the WAL pragma are in effect for KB hybrid retrieval.
        attach_sqlite_vec(_engine)
    return _engine


@contextmanager
def get_session():
    """Yield a SQLModel session."""
    engine = get_engine()
    with Session(engine) as session:
        yield session
