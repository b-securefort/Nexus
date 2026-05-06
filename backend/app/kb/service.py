"""
KB service — read files and search the index.
"""

import logging
from pathlib import Path

from app.config import get_settings
from app.kb.indexer import KBEntry, get_index

logger = logging.getLogger(__name__)


class KBService:
    """Knowledge base service for reading files and searching the index."""

    def list_index(self) -> list[KBEntry]:
        """Return the full KB index."""
        return get_index()

    def read_file(self, path: str) -> str:
        """
        Read a file under kb/. Path must be relative and must not escape kb/.
        Raises PermissionError on path traversal attempts.
        Raises FileNotFoundError if file doesn't exist.
        """
        # Security: reject path traversal
        if ".." in path or path.startswith("/") or path.startswith("\\"):
            raise PermissionError(f"Invalid path: {path}")

        settings = get_settings()
        kb_root = Path(settings.KB_REPO_LOCAL_PATH).resolve()
        target = (kb_root / path).resolve()

        # Verify the resolved path is within kb_root/kb/
        kb_dir = (kb_root / "kb").resolve()
        if not str(target).startswith(str(kb_dir)):
            raise PermissionError(f"Path escapes KB directory: {path}")

        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not target.is_file():
            raise FileNotFoundError(f"Not a file: {path}")

        return target.read_text(encoding="utf-8")

    def search(self, query: str, limit: int = 10) -> list[KBEntry]:
        """
        Token-scored search over index titles, summaries, and tags.
        Each query token is scored independently: title match = 3, tag match = 2,
        summary match = 1. Results are sorted by score descending.
        """
        if limit > 50:
            limit = 50

        tokens = [t for t in query.lower().split() if len(t) > 1]
        if not tokens:
            return []

        scored: list[tuple[int, KBEntry]] = []
        for entry in get_index():
            title_l = entry.title.lower()
            tags_l = " ".join(entry.tags).lower()
            summary_l = entry.summary.lower()

            score = 0
            for token in tokens:
                if token in title_l:
                    score += 3
                if token in tags_l:
                    score += 2
                if token in summary_l:
                    score += 1

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]


# Singleton
_kb_service: KBService | None = None


def get_kb_service() -> KBService:
    global _kb_service
    if _kb_service is None:
        _kb_service = KBService()
    return _kb_service
