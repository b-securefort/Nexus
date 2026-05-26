"""
Ingestion runner — orchestrates all enabled sources.

Called from git_sync.start_periodic_sync() and optionally on startup.
ADO wiki ingestion is list-driven (one entry per configured wiki); each
entry is wrapped in its own try/except so one broken source never blocks
the others. The PDF link-list source keeps its single-instance enable flag
(no multi-source need yet — add a list field when that changes).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


# Per-source last-sync state surfaced via GET /api/kb/index/status. Keyed
# by label (e.g. "ado_wiki:platform"); stores the last successful sync
# time, the page count, and any errors from the most recent attempt.
_SOURCE_STATUS: dict[str, dict] = {}


def get_source_status() -> dict[str, dict]:
    """Return a snapshot of per-source last-sync state for the status API."""
    return {k: dict(v) for k, v in _SOURCE_STATUS.items()}


def _record_status(key: str, *, pages_synced: int, errors: list[str]) -> None:
    _SOURCE_STATUS[key] = {
        "label": key.split(":", 1)[1] if ":" in key else key,
        "last_sync_at": datetime.now(timezone.utc).isoformat(),
        "pages_synced": pages_synced,
        "errors": errors,
    }


def run_all_sources() -> dict[str, int]:
    """Run all enabled ingestion sources.

    Returns a dict mapping ``<source-type>:<label>`` to the number of
    documents written. Per-source errors are logged and recorded on the
    status snapshot, but never propagate — one broken source never blocks
    the others.
    """
    settings = get_settings()
    kb_root = Path(settings.KB_REPO_LOCAL_PATH)
    results: dict[str, int] = {}

    # ── ADO wikis (one call per configured source) ────────────────────────
    if settings.INGEST_ADO_WIKI_SOURCES:
        from app.kb.ingest.ado_wiki import ingest_ado_wiki
        for source in settings.INGEST_ADO_WIKI_SOURCES:
            key = f"ado_wiki:{source.label}"
            try:
                count = ingest_ado_wiki(kb_root, source, settings)
                results[key] = count
                _record_status(key, pages_synced=count, errors=[])
            except Exception as e:
                logger.error(
                    "ADO wiki '%s' ingestion failed: %s",
                    source.label, e, exc_info=True,
                )
                results[key] = 0
                _record_status(key, pages_synced=0, errors=[str(e)])
    else:
        logger.debug(
            "ADO wiki ingestion disabled (INGEST_ADO_WIKI_SOURCES is empty)"
        )

    # ── PDF link list ─────────────────────────────────────────────────────
    if settings.INGEST_PDF_LIST_ENABLED:
        try:
            from app.kb.ingest.pdf_fetcher import ingest_pdfs
            count = ingest_pdfs(kb_root, settings)
            results["pdf_web"] = count
            _record_status("pdf_web", pages_synced=count, errors=[])
        except Exception as e:
            logger.error("PDF ingestion failed: %s", e, exc_info=True)
            results["pdf_web"] = 0
            _record_status("pdf_web", pages_synced=0, errors=[str(e)])
    else:
        logger.debug("PDF ingestion disabled (INGEST_PDF_LIST_ENABLED=false)")

    if results:
        total = sum(results.values())
        logger.info(
            "Ingestion complete: %d total documents written %s",
            total,
            results,
        )
    return results
