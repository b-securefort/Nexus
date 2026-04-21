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
        Substring search over index titles, summaries, and tags.
        Case-insensitive.
        """
        if limit > 50:
            limit = 50

        query_lower = query.lower()
        results = []

        for entry in get_index():
            searchable = f"{entry.title} {entry.summary} {' '.join(entry.tags)}".lower()
            if query_lower in searchable:
                results.append(entry)
                if len(results) >= limit:
                    break

        return results


# Singleton
_kb_service: KBService | None = None


def get_kb_service() -> KBService:
    global _kb_service
    if _kb_service is None:
        _kb_service = KBService()
    return _kb_service
