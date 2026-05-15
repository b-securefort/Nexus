"""
FastAPI app factory — registers routers, middleware, and startup tasks.
"""

import asyncio
import logging
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.config import get_settings
from app.db.engine import get_engine
from app.db.models import SQLModel
from app.kb.git_sync import sync_repo, start_periodic_sync
from app.kb.indexer import load_index
from app.skills.shared import load_shared_skills
from app.tools.base import init_tools

logger = logging.getLogger(__name__)

# Prometheus metrics
CHAT_REQUESTS = Counter("chat_requests_total", "Total chat requests", ["status"])
CHAT_DURATION = Histogram("chat_request_duration_seconds", "Chat request duration")
TOOL_CALLS = Counter("tool_calls_total", "Total tool calls", ["tool", "result"])
APPROVALS = Counter("approvals_total", "Total approvals", ["result"])
OPENAI_TOKENS = Counter("azure_openai_tokens_total", "Token usage", ["direction"])
KB_SYNC = Counter("kb_sync_total", "KB sync attempts", ["result"])


def _setup_logging():
    """Configure structured JSON logging."""
    from pythonjsonlogger import jsonlogger

    settings = get_settings()
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.APP_LOG_LEVEL)


def _apply_lightweight_migrations(engine):
    """Add columns that may be missing on existing tables (dev convenience).

    This is a no-op if the columns already exist.
    """
    import sqlalchemy

    with engine.connect() as conn:
        # Check if attachments_json column exists on messages table
        try:
            conn.execute(sqlalchemy.text("SELECT attachments_json FROM messages LIMIT 0"))
        except Exception:
            logger.info("Adding attachments_json column to messages table")
            conn.execute(sqlalchemy.text("ALTER TABLE messages ADD COLUMN attachments_json TEXT"))
            conn.commit()

        # Compaction summary cache columns on conversations
        try:
            conn.execute(sqlalchemy.text("SELECT summary_text FROM conversations LIMIT 0"))
        except Exception:
            logger.info("Adding summary_text column to conversations table")
            conn.execute(sqlalchemy.text("ALTER TABLE conversations ADD COLUMN summary_text TEXT"))
            conn.commit()
        try:
            conn.execute(sqlalchemy.text("SELECT summary_through_message_id FROM conversations LIMIT 0"))
        except Exception:
            logger.info("Adding summary_through_message_id column to conversations table")
            conn.execute(sqlalchemy.text("ALTER TABLE conversations ADD COLUMN summary_through_message_id INTEGER"))
            conn.commit()

        # Per-message compression cache (long user pastes + image descriptions)
        try:
            conn.execute(sqlalchemy.text("SELECT text_summary FROM messages LIMIT 0"))
        except Exception:
            logger.info("Adding text_summary column to messages table")
            conn.execute(sqlalchemy.text("ALTER TABLE messages ADD COLUMN text_summary TEXT"))
            conn.commit()
        try:
            conn.execute(sqlalchemy.text("SELECT image_summary FROM messages LIMIT 0"))
        except Exception:
            logger.info("Adding image_summary column to messages table")
            conn.execute(sqlalchemy.text("ALTER TABLE messages ADD COLUMN image_summary TEXT"))
            conn.commit()

        # KB hybrid retrieval virtual tables + triggers (Phase 2). The
        # regular `kb_chunks` table is created by SQLModel.metadata.create_all
        # via the model declaration; only the virtual tables and triggers
        # need raw DDL here.
        _ensure_kb_virtual_tables(conn)


def _ensure_kb_virtual_tables(conn) -> None:
    """Create kb_chunks_fts (FTS5), kb_chunks_vec (vec0), and the FTS sync
    triggers if they don't already exist. Idempotent — safe to call on every
    startup. Logs and continues on failure (e.g., if sqlite-vec extension
    didn't load on this Python build)."""
    import sqlalchemy

    statements = [
        # FTS5 over kb_chunks.text + kb_chunks.heading. `unicode61` tokenizer
        # WITHOUT porter stemming so technical jargon ("kubernetes", "azure"
        # vs "kubernet"/"azur") stays intact.
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5(
          text, heading,
          content='kb_chunks', content_rowid='id',
          tokenize='unicode61 remove_diacritics 2'
        )
        """,
        # Trigger: keep FTS in sync on INSERT
        """
        CREATE TRIGGER IF NOT EXISTS kb_chunks_ai AFTER INSERT ON kb_chunks BEGIN
          INSERT INTO kb_chunks_fts(rowid, text, heading)
          VALUES (new.id, new.text, new.heading);
        END
        """,
        # Trigger: keep FTS in sync on DELETE
        """
        CREATE TRIGGER IF NOT EXISTS kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN
          INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, text, heading)
          VALUES('delete', old.id, old.text, old.heading);
        END
        """,
        # Trigger: keep FTS in sync on UPDATE (delete-then-insert pattern per FTS5 docs)
        """
        CREATE TRIGGER IF NOT EXISTS kb_chunks_au AFTER UPDATE ON kb_chunks BEGIN
          INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, text, heading)
          VALUES('delete', old.id, old.text, old.heading);
          INSERT INTO kb_chunks_fts(rowid, text, heading)
          VALUES (new.id, new.text, new.heading);
        END
        """,
        # vec0 dense vectors (384-dim float32, matches bge-small-en-v1.5).
        # rowid joins kb_chunks.id explicitly from the reindexer.
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_vec USING vec0(
          embedding float[384]
        )
        """,
    ]
    for ddl in statements:
        try:
            conn.execute(sqlalchemy.text(ddl))
        except Exception as e:
            # If sqlite-vec didn't load we'll fail on the vec0 create; that's
            # fine — search_kb_hybrid is marked unavailable in that case.
            logger.warning("KB schema DDL skipped: %s", str(e).split("\n")[0])
            continue
    conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown tasks."""
    _setup_logging()
    settings = get_settings()
    logger.info("Starting Team Architect Backend (env=%s)", settings.APP_ENV)

    # Create tables (dev convenience; prod uses alembic)
    engine = get_engine()
    SQLModel.metadata.create_all(engine)

    # Ensure new columns exist on existing tables (dev convenience)
    _apply_lightweight_migrations(engine)

    # Init tools
    init_tools()

    # KB sync
    sync_repo()
    load_index()
    load_shared_skills()

    # Start background tasks
    sync_task = asyncio.create_task(start_periodic_sync())
    approval_task = asyncio.create_task(_approval_sweeper())
    backup_task = None
    if settings.BACKUP_ENABLED:
        backup_task = asyncio.create_task(_backup_loop())

    yield

    # Shutdown
    sync_task.cancel()
    approval_task.cancel()
    if backup_task:
        backup_task.cancel()
    logger.info("Shutting down")


async def _approval_sweeper():
    """Background task to expire stale approvals AND pending questions every 60s."""
    from app.agent.approvals import expire_stale_approvals
    from app.agent.questions import expire_stale_questions
    from app.db.engine import get_session

    while True:
        await asyncio.sleep(60)
        try:
            with get_session() as session:
                await expire_stale_approvals(session)
                await expire_stale_questions(session)
        except Exception as e:
            logger.error("Approval/question sweeper error: %s", str(e))


async def _backup_loop():
    """Background task for periodic SQLite backups."""
    import sqlite3
    from pathlib import Path
    from datetime import datetime, timezone

    settings = get_settings()
    if not settings.BACKUP_ENABLED:
        return

    while True:
        await asyncio.sleep(settings.BACKUP_INTERVAL_SECONDS)
        try:
            db_path = settings.DATABASE_URL.replace("sqlite:///", "")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = Path(db_path).parent / f"app-db-{timestamp}.db"

            source = sqlite3.connect(db_path)
            dest = sqlite3.connect(str(backup_path))
            source.backup(dest)
            source.close()
            dest.close()

            logger.info("SQLite backup created: %s", backup_path)
            # TODO: Upload to Azure Blob Storage and handle retention
        except Exception as e:
            logger.error("Backup error: %s", str(e))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Team Architect Assistant",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Register routers
    from app.api.health import router as health_router
    from app.api.chat import router as chat_router
    from app.api.skills import router as skills_router
    from app.api.conversations import router as conversations_router

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(skills_router)
    app.include_router(conversations_router)

    # Prometheus metrics endpoint
    @app.get("/metrics")
    async def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
