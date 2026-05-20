"""
Git sync service for the knowledge base repository.
Handles cloning, pulling, and periodic refresh.
"""

import asyncio
import logging
from pathlib import Path

from git import Repo, GitCommandError

from app.config import get_settings

logger = logging.getLogger(__name__)

_last_sync: str | None = None


def get_last_sync() -> str | None:
    return _last_sync


def _set_last_sync():
    global _last_sync
    from datetime import datetime, timezone
    _last_sync = datetime.now(timezone.utc).isoformat()


def sync_repo() -> None:
    """Clone or pull the KB repo. Called on startup and periodically."""
    settings = get_settings()
    local_path = Path(settings.KB_REPO_LOCAL_PATH)

    if settings.KB_REPO_LOCAL_ONLY:
        logger.info("KB_REPO_LOCAL_ONLY=true, skipping git sync")
        if not local_path.exists():
            local_path.mkdir(parents=True, exist_ok=True)
            logger.warning("Created empty KB directory at %s", local_path)
        _set_last_sync()
        return

    try:
        if not local_path.exists() or not (local_path / ".git").exists():
            logger.info("Cloning KB repo from %s to %s", settings.KB_REPO_URL, local_path)
            clone_url = _build_auth_url(settings)
            Repo.clone_from(
                clone_url,
                str(local_path),
                branch=settings.KB_REPO_BRANCH,
                depth=1,
            )
        else:
            logger.info("Fetching KB repo updates")
            repo = Repo(str(local_path))
            origin = repo.remotes.origin
            origin.fetch()
            repo.git.reset("--hard", f"origin/{settings.KB_REPO_BRANCH}")

        _set_last_sync()
        logger.info("KB repo sync completed")

    except GitCommandError as e:
        logger.error("Git sync failed: %s", str(e))
        # Don't crash — serve stale content
    except Exception as e:
        logger.error("Unexpected error during KB sync: %s", str(e))


def _build_auth_url(settings) -> str:
    """Build authenticated URL for cloning."""
    if settings.KB_REPO_AUTH_METHOD == "pat" and settings.KB_REPO_PAT:
        # Insert PAT into URL for Azure DevOps
        url = settings.KB_REPO_URL
        if "dev.azure.com" in url:
            # https://PAT@dev.azure.com/...
            url = url.replace("https://", f"https://{settings.KB_REPO_PAT}@")
        return url
    return settings.KB_REPO_URL


async def start_periodic_sync():
    """Background task for periodic KB sync."""
    settings = get_settings()
    interval = settings.KB_SYNC_INTERVAL_SECONDS

    while True:
        await asyncio.sleep(interval)
        try:
            sync_repo()
            # Run pilot ingestion so new remote content lands in kb_data/kb/
            from app.kb.ingest.runner import run_all_sources
            await asyncio.to_thread(run_all_sources)
            # Re-index after sync + ingest so all new/changed KB files are searchable.
            # Both indexes must be refreshed: kb_chunks (hybrid, used by
            # search_kb_hybrid) AND the in-memory file-level index (used by
            # search_kb and the system-prompt KB summary block).
            from app.kb.reindex import reindex_all
            from app.kb.indexer import load_index
            await asyncio.to_thread(reindex_all)
            await asyncio.to_thread(load_index)
        except Exception as e:
            logger.error("Periodic KB sync failed: %s", str(e))
