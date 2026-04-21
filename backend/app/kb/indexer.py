"""
KB index builder and loader.
Reads kb_index.json from the KB repo, or builds a minimal index from file paths.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class KBEntry:
    path: str
    title: str
    summary: str
    tags: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_index: list[KBEntry] = []


def get_index() -> list[KBEntry]:
    return _index


def load_index() -> list[KBEntry]:
    """Load kb_index.json or build a minimal index from file paths."""
    global _index
    settings = get_settings()
    kb_root = Path(settings.KB_REPO_LOCAL_PATH)
    index_path = kb_root / "kb_index.json"

    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            _index = [
                KBEntry(
                    path=entry.get("path", ""),
                    title=entry.get("title", ""),
                    summary=entry.get("summary", ""),
                    tags=entry.get("tags", []),
                )
                for entry in raw
            ]
            logger.info("Loaded KB index with %d entries", len(_index))
            return _index
        except Exception as e:
            logger.warning("Failed to load kb_index.json: %s, building minimal index", str(e))

    # Fallback: build minimal index from file paths
    _index = _build_minimal_index(kb_root)
    logger.info("Built minimal KB index with %d entries", len(_index))
    return _index


def _build_minimal_index(kb_root: Path) -> list[KBEntry]:
    """Build a minimal index from file paths and first H1 heading."""
    entries = []
    kb_dir = kb_root / "kb"
    if not kb_dir.exists():
        return entries

    for md_file in sorted(kb_dir.rglob("*.md")):
        rel_path = str(md_file.relative_to(kb_root)).replace("\\", "/")
        title = _extract_first_h1(md_file) or md_file.stem
        entries.append(KBEntry(path=rel_path, title=title, summary="", tags=[]))

    return entries


def _extract_first_h1(filepath: Path) -> str:
    """Extract the first H1 heading from a markdown file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
    except Exception:
        pass
    return ""


def get_index_summary() -> str:
    """Get a compact text summary of the KB index for the system prompt."""
    lines = []
    for entry in _index:
        tags = f" ({', '.join(entry.tags)})" if entry.tags else ""
        summary_part = f": {entry.summary}" if entry.summary else ""
        lines.append(f"- {entry.path} — {entry.title}{summary_part}{tags}")

    result = "\n".join(lines)

    # Truncate if too large (>20KB)
    if len(result) > 20480:
        logger.warning("KB index summary exceeds 20KB, truncating")
        result = result[:20480] + "\n... (truncated)"

    return result
