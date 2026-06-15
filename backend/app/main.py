"""
FastAPI app factory — registers routers, middleware, and startup tasks.
"""

import asyncio
import logging
import uuid

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.auth.rbac import init_rbac
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
    from pythonjsonlogger.json import JsonFormatter

    settings = get_settings()
    handler = logging.StreamHandler()
    formatter = JsonFormatter(
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

        # A4 — Lease heartbeat for long-turn recovery
        try:
            conn.execute(sqlalchemy.text("SELECT lease_heartbeat_at FROM conversations LIMIT 0"))
        except Exception:
            logger.info("Adding lease_heartbeat_at column to conversations table")
            conn.execute(sqlalchemy.text(
                "ALTER TABLE conversations ADD COLUMN lease_heartbeat_at DATETIME"
            ))
            conn.commit()
        try:
            conn.execute(sqlalchemy.text("SELECT lease_owner FROM conversations LIMIT 0"))
        except Exception:
            logger.info("Adding lease_owner column to conversations table")
            conn.execute(sqlalchemy.text(
                "ALTER TABLE conversations ADD COLUMN lease_owner TEXT"
            ))
            conn.commit()

        # Advisory risk assessment on approval cards (DESIGN.md §5 2026-06-04)
        try:
            conn.execute(sqlalchemy.text("SELECT risk_level FROM pending_approvals LIMIT 0"))
        except Exception:
            logger.info("Adding risk_level column to pending_approvals table")
            conn.execute(sqlalchemy.text("ALTER TABLE pending_approvals ADD COLUMN risk_level TEXT"))
            conn.commit()
        try:
            conn.execute(sqlalchemy.text("SELECT risk_description FROM pending_approvals LIMIT 0"))
        except Exception:
            logger.info("Adding risk_description column to pending_approvals table")
            conn.execute(sqlalchemy.text("ALTER TABLE pending_approvals ADD COLUMN risk_description TEXT"))
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

        # Multi-wiki ADO ingestion (DESIGN.md §5 2026-05-26). On the first
        # boot after this change, add the source_instance column AND drop
        # legacy single-wiki chunks (identified by the flat
        # kb/ado_wiki/<page>.md path that the new layout no longer uses —
        # new layout is kb/ado_wiki/<label>/<page>.md). The cleanup is
        # gated by the column-add path so it runs exactly once: subsequent
        # boots find the column present and skip the whole block.
        try:
            conn.execute(sqlalchemy.text("SELECT source_instance FROM kb_chunks LIMIT 0"))
        except Exception:
            logger.info("Adding source_instance column to kb_chunks table")
            conn.execute(sqlalchemy.text(
                "ALTER TABLE kb_chunks ADD COLUMN source_instance TEXT"
            ))
            try:
                legacy_ids = [r[0] for r in conn.execute(sqlalchemy.text(
                    "SELECT id FROM kb_chunks "
                    "WHERE kb_path LIKE 'kb/ado_wiki/%' "
                    "  AND kb_path NOT LIKE 'kb/ado_wiki/%/%'"
                )).fetchall()]
                if legacy_ids:
                    placeholders = ",".join(str(int(i)) for i in legacy_ids)
                    # vec0 has no triggers — delete it explicitly before kb_chunks
                    # (FTS5 trigger handles kb_chunks_fts on the kb_chunks delete).
                    conn.execute(sqlalchemy.text(
                        f"DELETE FROM kb_chunks_vec WHERE rowid IN ({placeholders})"
                    ))
                    conn.execute(sqlalchemy.text(
                        f"DELETE FROM kb_chunks WHERE id IN ({placeholders})"
                    ))
                    logger.info(
                        "Dropped %d legacy ado_wiki chunks during multi-source cutover",
                        len(legacy_ids),
                    )
            except Exception as e:
                logger.warning(
                    "Legacy ado_wiki cleanup skipped: %s", str(e).split("\n")[0]
                )
            conn.commit()

        # User-correction learning capture (DESIGN.md §5 2026-06-05). Add the
        # provenance column and backfill existing rows to the failure→success
        # source so the source-gated lifecycle (no tool-outcome promotion for
        # user_correction) treats legacy rows as reality-grounded.
        try:
            conn.execute(sqlalchemy.text("SELECT source FROM agent_learnings LIMIT 0"))
        except Exception:
            logger.info("Adding source column to agent_learnings table")
            conn.execute(sqlalchemy.text(
                "ALTER TABLE agent_learnings ADD COLUMN source TEXT "
                "NOT NULL DEFAULT 'failure_success'"
            ))
            conn.commit()

        # Per-user weekly spend cap (DESIGN.md §5 2026-06-14). NULL → role
        # default; the usage_events ledger table itself is created by
        # create_all (a brand-new table needs no ALTER).
        try:
            conn.execute(sqlalchemy.text("SELECT credit_cap_usd FROM users LIMIT 0"))
        except Exception:
            logger.info("Adding credit_cap_usd column to users table")
            conn.execute(sqlalchemy.text("ALTER TABLE users ADD COLUMN credit_cap_usd REAL"))
            conn.commit()

        # Agent learnings vec0 companion (procedural + semantic memory).
        # The regular `agent_learnings` table is created by SQLModel; the
        # vec0 virtual table holds embeddings used for top-K retrieval.
        _ensure_agent_learnings_vec(conn)


def _ensure_kb_virtual_tables(conn) -> None:
    """Create kb_chunks_fts (FTS5), kb_chunks_vec (vec0), and the FTS sync
    triggers if they don't already exist. Idempotent — safe to call on every
    startup. Logs and continues on failure (e.g., if sqlite-vec extension
    didn't load on this Python build).

    Also handles a vec0 dimension mismatch (e.g. upgrading from a 384-dim
    bge-small build to 1536-dim Azure OpenAI): drops and recreates the table
    and clears kb_chunks so the reindexer starts clean.
    """
    import re
    import sqlalchemy

    settings = get_settings()
    expected_dims = settings.KB_EMBED_DIMENSIONS

    # ── Detect and fix vec0 dimension mismatch ────────────────────────────────
    # Virtual tables appear in sqlite_master; the DDL contains the dimension.
    try:
        row = conn.execute(sqlalchemy.text(
            "SELECT sql FROM sqlite_master WHERE name='kb_chunks_vec' AND type='table'"
        )).fetchone()
        if row and row[0]:
            m = re.search(r"float\[(\d+)\]", row[0])
            if m and int(m.group(1)) != expected_dims:
                logger.warning(
                    "kb_chunks_vec has dimension %s but KB_EMBED_DIMENSIONS=%s — "
                    "dropping and recreating (all chunks will be re-embedded)",
                    m.group(1), expected_dims,
                )
                conn.execute(sqlalchemy.text("DROP TABLE IF EXISTS kb_chunks_vec"))
                conn.execute(sqlalchemy.text("DELETE FROM kb_chunks"))
                conn.commit()
    except Exception as e:
        logger.warning("vec0 dimension check skipped: %s", str(e).split("\n")[0])

    # ── Create tables and triggers if missing ────────────────────────────────
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
        # vec0 dense vectors — dimension matches KB_EMBED_DIMENSIONS (default 1536,
        # matches Azure OpenAI text-embedding-3-small). rowid joins kb_chunks.id.
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_vec USING vec0(
          embedding float[{expected_dims}]
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


def _ensure_agent_learnings_vec(conn) -> None:
    """Create the `agent_learnings_vec` vec0 table AND the FTS5 virtual table
    `agent_learnings_fts` (LMI #3) if either is missing.

    Dimensions match `KB_EMBED_DIMENSIONS` so the same Azure OpenAI embedding
    model serves both KB and learnings retrieval.

    The FTS5 table covers `summary` + `details` so retrieval can fall back to
    BM25 when dense embeddings miss things like error codes / CLI flag names
    (the gap the LMI #3 finding called out). Triggers keep it in sync with
    inserts / updates / deletes on `agent_learnings`.

    If sqlite-vec isn't loaded, only the FTS half is created — retrieval will
    still return useful results via BM25 alone.
    """
    import re
    import sqlalchemy

    settings = get_settings()
    expected_dims = settings.KB_EMBED_DIMENSIONS

    # Handle a dimension mismatch the same way KB does — drop & rebuild
    try:
        row = conn.execute(sqlalchemy.text(
            "SELECT sql FROM sqlite_master WHERE name='agent_learnings_vec' AND type='table'"
        )).fetchone()
        if row and row[0]:
            m = re.search(r"float\[(\d+)\]", row[0])
            if m and int(m.group(1)) != expected_dims:
                logger.warning(
                    "agent_learnings_vec has dimension %s but KB_EMBED_DIMENSIONS=%s — "
                    "dropping and recreating (all learnings will be re-embedded)",
                    m.group(1), expected_dims,
                )
                conn.execute(sqlalchemy.text("DROP TABLE IF EXISTS agent_learnings_vec"))
                # Clear embed_model marker so reembed picks them up
                conn.execute(sqlalchemy.text("UPDATE agent_learnings SET embed_model=NULL"))
                conn.commit()
    except Exception as e:
        logger.warning("agent_learnings_vec dimension check skipped: %s", str(e).split("\n")[0])

    vec_ddl = f"""
    CREATE VIRTUAL TABLE IF NOT EXISTS agent_learnings_vec USING vec0(
      embedding float[{expected_dims}]
    )
    """
    try:
        conn.execute(sqlalchemy.text(vec_ddl))
        conn.commit()
    except Exception as e:
        logger.warning("agent_learnings_vec creation skipped: %s", str(e).split("\n")[0])

    # FTS5 over agent_learnings.summary + .details (LMI #3). Same unicode61
    # tokenizer as kb_chunks_fts so behaviour stays consistent across the
    # two retrieval surfaces.
    fts_statements = [
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_learnings_fts USING fts5(
          summary, details,
          content='agent_learnings', content_rowid='id',
          tokenize='unicode61 remove_diacritics 2'
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS agent_learnings_ai
        AFTER INSERT ON agent_learnings BEGIN
          INSERT INTO agent_learnings_fts(rowid, summary, details)
          VALUES (new.id, new.summary, new.details);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS agent_learnings_ad
        AFTER DELETE ON agent_learnings BEGIN
          INSERT INTO agent_learnings_fts(agent_learnings_fts, rowid, summary, details)
          VALUES('delete', old.id, old.summary, old.details);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS agent_learnings_au
        AFTER UPDATE ON agent_learnings BEGIN
          INSERT INTO agent_learnings_fts(agent_learnings_fts, rowid, summary, details)
          VALUES('delete', old.id, old.summary, old.details);
          INSERT INTO agent_learnings_fts(rowid, summary, details)
          VALUES (new.id, new.summary, new.details);
        END
        """,
    ]
    for stmt in fts_statements:
        try:
            conn.execute(sqlalchemy.text(stmt))
        except Exception as e:
            logger.warning(
                "agent_learnings_fts DDL skipped: %s",
                str(e).split("\n")[0],
            )
            continue
    conn.commit()

    # Backfill FTS for rows that pre-date the table. content='agent_learnings'
    # external-content tables are empty after CREATE — the 'rebuild' command
    # repopulates from the source table. Cheap when already populated; the
    # row count is tiny relative to the kb chunks table.
    try:
        conn.execute(sqlalchemy.text(
            "INSERT INTO agent_learnings_fts(agent_learnings_fts) VALUES('rebuild')"
        ))
        conn.commit()
    except Exception as e:
        logger.debug(
            "agent_learnings_fts rebuild skipped: %s",
            str(e).split("\n")[0],
        )


async def _preflight_aoai_deployments() -> None:
    """Verify every configured Azure OpenAI deployment exists on the endpoint.

    Best-effort and advisory: ERROR-logs each configured name missing from the
    endpoint's deployment list; stays quiet on network failure (offline dev).
    Never blocks startup."""
    import httpx

    settings = get_settings()
    if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
        return
    configured = {
        "AZURE_OPENAI_DEPLOYMENT": settings.AZURE_OPENAI_DEPLOYMENT,
        "AZURE_OPENAI_EMBED_DEPLOYMENT": settings.AZURE_OPENAI_EMBED_DEPLOYMENT,
    }
    if settings.AZURE_OPENAI_DEPLOYMENT_HIGH:
        configured["AZURE_OPENAI_DEPLOYMENT_HIGH"] = settings.AZURE_OPENAI_DEPLOYMENT_HIGH
    url = (
        settings.AZURE_OPENAI_ENDPOINT.rstrip("/")
        + "/openai/deployments?api-version=2023-03-15-preview"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"api-key": settings.AZURE_OPENAI_API_KEY})
        if resp.status_code != 200:
            logger.warning("AOAI deployment preflight skipped (HTTP %s)", resp.status_code)
            return
        existing = {d.get("id") for d in resp.json().get("data", [])}
    except Exception as e:  # noqa: BLE001 — preflight must never break startup
        logger.warning("AOAI deployment preflight skipped: %s", str(e)[:120])
        return
    ok = True
    for setting_name, deployment in configured.items():
        if deployment and deployment not in existing:
            ok = False
            logger.error(
                "%s=%r does NOT exist on %s (existing deployments: %s). Every call "
                "to it will 404: the features routed there fail closed SILENTLY "
                "(risk review, compaction summaries, learnings, KB vector search) "
                "and repeated failures can open the shared circuit breaker and "
                "block chat. Fix the value in backend/.env or create the deployment.",
                setting_name, deployment,
                settings.AZURE_OPENAI_ENDPOINT, sorted(existing),
            )
    if ok:
        logger.info(
            "AOAI deployment preflight OK: %s", sorted(v for v in configured.values() if v)
        )


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

    # Load role access map (Entra App Roles → skills/tools). No-op if
    # AZURE_APPCONFIG_ENDPOINT is empty; falls back to defaults on failure.
    await init_rbac()

    # One-time migration of legacy learn.md → agent_learnings (idempotent;
    # bypasses the LLM judge because entries are marked 'provisional' and
    # go through the standard validation-or-archive lifecycle on use).
    try:
        from app.agent.learnings import migrate_legacy_learn_md, reembed_dirty
        from app.db.engine import get_session
        with get_session() as s:
            migrated = migrate_legacy_learn_md(s)
        if migrated:
            # Embed migrated rows in batches so they're immediately retrievable
            asyncio.create_task(asyncio.to_thread(reembed_dirty, 200))
    except Exception:
        logger.exception("Legacy learn.md migration failed (continuing)")

    # KB sync
    sync_repo()
    load_index()
    load_shared_skills()

    # Phase-gate audit — log current phase + which gates are on/off, and
    # WARN loudly if any gate is past its review_by date. See app/phases.py
    # and gatesreadme.md for the removal playbook.
    from app.phases import PHASE_GATES, format_startup_banner, overdue_gates
    logger.info(format_startup_banner())
    for gate_name in overdue_gates():
        gate = PHASE_GATES[gate_name]
        logger.warning(
            "Phase gate '%s' is past review_by (%s) and currently ACTIVE — "
            "removal criteria: %s. See gatesreadme.md.",
            gate_name, gate.review_by.isoformat(), gate.removal_criteria,
        )

    # Drift check: warn at startup if any shared skill's `tools:` allowlist
    # references a name not in TOOL_REGISTRY. Catches phantoms (like the
    # `diagram_gen` typo found in 2026-05-19 sanity testing) BEFORE a user
    # picks the skill and resolve_tools silently drops the missing name.
    # Must run after both init_tools() and load_shared_skills() above.
    from app.skills.shared import audit_shared_skill_tool_allowlists
    audit_shared_skill_tool_allowlists()

    # AOAI deployment preflight (background, non-blocking). A misconfigured
    # deployment name doesn't break chat loudly — it silently degrades every
    # call routed to it AND feeds the shared circuit breaker. Scream at startup
    # instead (found 2026-06-10: base + embed deployments named ones that
    # didn't exist on the endpoint; risk review, compaction summaries,
    # learnings, and KB vector search had all been failing closed for days).
    asyncio.create_task(_preflight_aoai_deployments())

    # Kick KB hybrid-retrieval reindex in background (non-blocking)
    from app.kb.reindex import reindex_all
    asyncio.create_task(asyncio.to_thread(reindex_all))

    # Start background tasks
    sync_task = asyncio.create_task(start_periodic_sync())
    approval_task = asyncio.create_task(_approval_sweeper())
    usage_prune_task = asyncio.create_task(_usage_ledger_prune())
    audit_prune_task = asyncio.create_task(_audit_log_prune())
    backup_task = None
    if settings.BACKUP_ENABLED:
        backup_task = asyncio.create_task(_backup_loop())

    yield

    # Shutdown
    sync_task.cancel()
    approval_task.cancel()
    usage_prune_task.cancel()
    audit_prune_task.cancel()
    if backup_task:
        backup_task.cancel()
    # Tear down the dedicated tool executor (A2). cancel_futures stops anything
    # still queued; we don't wait for in-flight work since the process is exiting.
    try:
        from app.agent.concurrency import shutdown_tool_executor
        shutdown_tool_executor(wait=False)
    except Exception:
        logger.exception("Failed to shutdown tool executor cleanly")
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


async def _usage_ledger_prune():
    """Background task: drop usage_events older than the retention window.

    Keeps the spend ledger bounded (DESIGN.md §5/§6 2026-06-14). The weekly cap
    math only ever looks back two weeks, so anything past the retention window is
    reporting-only. Runs regardless of the cap flags, since record_usage writes
    rows unconditionally. Sleeps first, like _backup_loop.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete as sa_delete

    from app.db.engine import get_session
    from app.db.models import UsageEvent

    settings = get_settings()
    interval = max(3600, settings.USAGE_LEDGER_PRUNE_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=settings.USAGE_LEDGER_RETENTION_DAYS
            )
            with get_session() as session:
                result = session.execute(
                    sa_delete(UsageEvent).where(UsageEvent.created_at < cutoff)
                )
                session.commit()
                if result.rowcount:
                    logger.info(
                        "Pruned %d usage_events older than %d days",
                        result.rowcount, settings.USAGE_LEDGER_RETENTION_DAYS,
                    )
        except Exception as e:
            logger.error("Usage ledger prune error: %s", str(e))


async def _audit_log_prune():
    """Background task: drop tool_executions older than the retention window.

    This sweeper is the audit log's ONLY deleter (DESIGN.md §5/§6 2026-06-15) —
    there is no update/delete API, so an audited operator cannot strip their own
    trail. Its window (`AUDIT_LOG_RETENTION_DAYS`) is deliberately separate from
    the usage ledger's, so the audit trail can be retained independently of
    spend telemetry. Sleeps first, like `_usage_ledger_prune`.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete as sa_delete

    from app.db.engine import get_session
    from app.db.models import ToolExecution

    settings = get_settings()
    interval = max(3600, settings.AUDIT_LOG_PRUNE_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=settings.AUDIT_LOG_RETENTION_DAYS
            )
            with get_session() as session:
                result = session.execute(
                    sa_delete(ToolExecution).where(ToolExecution.created_at < cutoff)
                )
                session.commit()
                if result.rowcount:
                    logger.info(
                        "Pruned %d tool_executions older than %d days",
                        result.rowcount, settings.AUDIT_LOG_RETENTION_DAYS,
                    )
        except Exception as e:
            logger.error("Audit log prune error: %s", str(e))


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
    from app.api.learnings import router as learnings_router
    from app.api.usage import router as usage_router
    from app.api.users import router as users_router
    from app.api.audit import router as audit_router

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(skills_router)
    app.include_router(conversations_router)
    app.include_router(learnings_router)
    app.include_router(usage_router)
    app.include_router(users_router)
    app.include_router(audit_router)

    # Prometheus metrics endpoint — gated to the `architect` Entra App Role.
    # Metrics expose request volumes and tool usage; treat them like other
    # admin-only data. Scrapers in production should use a service-principal
    # bearer token with the architect role assigned. DEV_AUTH_BYPASS still
    # passes through (see app/deps.py:require_architect).
    from app.deps import require_architect

    @app.get("/metrics")
    async def metrics(_user=Depends(require_architect)):
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
