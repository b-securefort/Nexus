"""
KB reindexer — detects changed files, chunks them, embeds via Azure OpenAI,
and upserts into kb_chunks + kb_chunks_vec.

Design notes:
- threading.Lock prevents overlapping runs in the same process (single-worker
  assumption; see DESIGN.md §6 for multi-worker caveat).
- Only files whose content_hash or embed_model has changed are re-embedded.
  Changing KB_CHUNK_MAX_CHARS or KB_CHUNK_OVERLAP_FRACTION requires a manual
  force_rebuild() — see DESIGN.md §6 "KB reindex" section.
- Runs synchronously (no async) so it is safe to call from asyncio.to_thread().
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db.engine import get_engine
from app.kb.chunker import chunk_markdown
from app.kb.embedder import embed_model_key, embed_texts
from app.kb.vector_store import (
    all_indexed_paths,
    chunk_count,
    delete_chunks_for_path,
    get_stored_state,
    upsert_file_chunks,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_status: dict = {
    "state": "idle",       # idle | running | complete | error
    "indexed_files": 0,
    "total_files": 0,
    "started_at": None,
    "completed_at": None,
    "errors": [],
}


def status() -> dict:
    """Return a snapshot of the current reindex status."""
    return dict(_status)


def _update_status(**kwargs) -> None:
    _status.update(kwargs)


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def reindex_all(force: bool = False) -> None:
    """Scan kb_data/kb/ for changed files, re-chunk and re-embed them.

    Thread-safe. If already running, logs and returns immediately.
    Set force=True to re-embed all files regardless of stored hash.
    """
    if not _lock.acquire(blocking=False):
        logger.info("KB reindex already running — skipping concurrent request")
        return

    try:
        _reindex(force=force)
    finally:
        _lock.release()


def _reindex(force: bool) -> None:
    settings = get_settings()
    model_key = embed_model_key()
    kb_dir = Path(settings.KB_REPO_LOCAL_PATH) / "kb"

    _update_status(
        state="running",
        indexed_files=0,
        total_files=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=None,
        errors=[],
    )
    logger.info("KB reindex started (force=%s, model=%s)", force, model_key)

    try:
        md_files = sorted(kb_dir.rglob("*.md")) if kb_dir.exists() else []
        _update_status(total_files=len(md_files))

        if not md_files:
            logger.info("No markdown files found under %s", kb_dir)
            _update_status(state="complete", completed_at=datetime.now(timezone.utc).isoformat())
            return

        engine = get_engine()
        with engine.connect() as conn:
            # Detect embed-model change → treat as force
            if not force:
                row = conn.execute(
                    text("SELECT DISTINCT embed_model FROM kb_chunks LIMIT 1")
                ).fetchone()
                if row and row[0] != model_key:
                    logger.info(
                        "Embed model changed (%s → %s) — forcing full re-embed",
                        row[0], model_key,
                    )
                    force = True

            kb_root = Path(settings.KB_REPO_LOCAL_PATH)
            errors: list[str] = []

            for md_path in md_files:
                try:
                    kb_path = md_path.relative_to(kb_root).as_posix()
                    content = md_path.read_text(encoding="utf-8", errors="replace")
                    content_hash = _file_hash(content)
                    file_mtime = md_path.stat().st_mtime

                    if not force:
                        stored = get_stored_state(conn, kb_path)
                        if stored and stored[0] == content_hash and stored[1] == model_key:
                            _update_status(indexed_files=_status["indexed_files"] + 1)
                            continue

                    chunks = chunk_markdown(kb_path, content)
                    if not chunks:
                        _update_status(indexed_files=_status["indexed_files"] + 1)
                        continue

                    texts = [c.text for c in chunks]
                    embeddings = embed_texts(texts)

                    upsert_file_chunks(
                        conn, kb_path, chunks, embeddings,
                        content_hash, file_mtime, model_key,
                    )
                    conn.commit()
                    _update_status(indexed_files=_status["indexed_files"] + 1)
                    logger.debug("Indexed %s (%d chunks)", kb_path, len(chunks))

                except Exception as e:
                    msg = f"{md_path.name}: {e}"
                    errors.append(msg)
                    logger.error("Reindex error — %s", msg)
                    _update_status(indexed_files=_status["indexed_files"] + 1)

            # GC: remove chunks for files that no longer exist on disk
            _gc(conn, kb_root, md_files)
            conn.commit()
            _update_status(errors=errors)

        total = chunk_count(engine.connect())
        logger.info(
            "KB reindex complete — %d files, %d total chunks, %d errors",
            len(md_files), total, len(errors),
        )
        _update_status(
            state="complete" if not errors else "complete_with_errors",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.exception("KB reindex failed: %s", e)
        _update_status(
            state="error",
            errors=[str(e)],
            completed_at=datetime.now(timezone.utc).isoformat(),
        )


def _gc(conn, kb_root: Path, live_files: list[Path]) -> None:
    """Delete chunks whose source file no longer exists on disk."""
    live_paths = {f.relative_to(kb_root).as_posix() for f in live_files}
    indexed = all_indexed_paths(conn)
    for stale in indexed - live_paths:
        logger.info("GC: removing chunks for deleted file %s", stale)
        delete_chunks_for_path(conn, stale)


def force_rebuild() -> None:
    """Public entry point for the /api/kb/index/rebuild endpoint."""
    reindex_all(force=True)
