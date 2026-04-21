"""Health check endpoints."""

from fastapi import APIRouter

from app.kb.git_sync import get_last_sync
from app.db.engine import get_engine

router = APIRouter()


@router.get("/healthz")
async def healthz():
    """Liveness probe — always returns OK if the app is running."""
    return {
        "status": "ok",
        "kb_last_sync": get_last_sync(),
    }


@router.get("/readyz")
async def readyz():
    """Readiness probe — checks DB and KB sync status."""
    db_ok = False
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(type(conn).execute.__func__.__get__(conn)("SELECT 1"))  # type: ignore
        db_ok = True
    except Exception:
        pass

    # Simpler DB check
    try:
        from sqlmodel import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    kb_synced = get_last_sync() is not None

    if not db_ok or not kb_synced:
        return {"status": "not_ready", "db_ok": db_ok, "kb_synced": kb_synced}

    return {"status": "ready", "db_ok": db_ok, "kb_synced": kb_synced}
