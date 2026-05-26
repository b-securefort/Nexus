"""Health check endpoints."""

import asyncio

from fastapi import APIRouter

from app.agent.circuit_breaker import get_status_dict as cb_status
from app.kb.git_sync import get_last_sync
from app.db.engine import get_engine
from app.phases import phase_status

router = APIRouter()


@router.get("/healthz")
async def healthz():
    """Liveness probe — always returns OK if the app is running.

    `phase` reports the current NEXUS_PHASE and per-gate enabled state — see
    app/phases.py. Useful for confirming which features are unlocked in a
    given deployment without grepping config.
    """
    return {
        "status": "ok",
        "kb_last_sync": get_last_sync(),
        "aoai_circuit_breaker": cb_status(),
        "phase": phase_status(),
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


@router.get("/api/kb/index/status")
async def kb_index_status():
    """Return the current KB hybrid-retrieval index status.

    Top-level rollup (state, indexed_files, total_files, started_at,
    completed_at, errors) is the reindexer's view. The ``sources`` array
    is the ingestion runner's view — one entry per configured KB source
    instance with its last-sync time, page count, and any errors.
    """
    from app.kb.reindex import status
    from app.kb.ingest.runner import get_source_status

    payload = status()
    payload["sources"] = list(get_source_status().values())
    return payload


@router.post("/api/kb/index/rebuild", status_code=202)
async def kb_index_rebuild():
    """Trigger a full KB re-index (force=True). Returns immediately; poll /status."""
    from app.kb.reindex import force_rebuild
    asyncio.create_task(asyncio.to_thread(force_rebuild))
    return {"status": "rebuild_started"}
