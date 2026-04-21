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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown tasks."""
    _setup_logging()
    settings = get_settings()
    logger.info("Starting Team Architect Backend (env=%s)", settings.APP_ENV)

    # Create tables (dev convenience; prod uses alembic)
    engine = get_engine()
    SQLModel.metadata.create_all(engine)

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
    """Background task to expire stale approvals every 60 seconds."""
    from app.agent.approvals import expire_stale_approvals
    from app.db.engine import get_session

    while True:
        await asyncio.sleep(60)
        try:
            with get_session() as session:
                await expire_stale_approvals(session)
        except Exception as e:
            logger.error("Approval sweeper error: %s", str(e))


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
