"""
Ingestion runner — orchestrates all enabled sources.

Called from git_sync.start_periodic_sync() and optionally on startup.
Each source is individually gated by its enable flag in Settings.
All failures are logged and swallowed so a broken source never blocks others.
"""

import logging
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


def run_all_sources() -> dict[str, int]:
    """Run all enabled ingestion sources.

    Returns a dict mapping source name to number of documents written.
    Logs and continues on per-source errors.
    """
    settings = get_settings()
    kb_root = Path(settings.KB_REPO_LOCAL_PATH)
    results: dict[str, int] = {}

    # ── ADO wiki ──────────────────────────────────────────────────────────
    if settings.INGEST_ADO_WIKI_ENABLED:
        try:
            from app.kb.ingest.ado_wiki import ingest_ado_wiki
            count = ingest_ado_wiki(kb_root, settings)
            results["ado_wiki"] = count
        except Exception as e:
            logger.error("ADO wiki ingestion failed: %s", e, exc_info=True)
            results["ado_wiki"] = 0
    else:
        logger.debug("ADO wiki ingestion disabled (INGEST_ADO_WIKI_ENABLED=false)")

    # ── PDF link list ─────────────────────────────────────────────────────
    if settings.INGEST_PDF_LIST_ENABLED:
        try:
            from app.kb.ingest.pdf_fetcher import ingest_pdfs
            count = ingest_pdfs(kb_root, settings)
            results["pdf_web"] = count
        except Exception as e:
            logger.error("PDF ingestion failed: %s", e, exc_info=True)
            results["pdf_web"] = 0
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
