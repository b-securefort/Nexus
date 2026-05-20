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
    """Load curated kb_index.json metadata and merge with an on-disk file scan.

    kb_index.json is OPTIONAL metadata enrichment, not the source of truth.
    Every .md file under kb/ becomes an index entry; curated summary + tags
    from the json (where present) are layered on top. Without this merge, a
    new file added to the KB without a matching json entry stays invisible
    to `search_kb` and the system prompt's KB summary block — that was the
    failure mode that drove agents to hallucinate paths in 2026-05-19 sanity.
    """
    global _index
    settings = get_settings()
    kb_root = Path(settings.KB_REPO_LOCAL_PATH)
    index_path = kb_root / "kb_index.json"

    # Step 1 — load curated metadata if present. Tolerant of missing/bad file:
    # the scan in step 2 still produces a usable minimal index either way.
    curated_by_path: dict[str, dict] = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                path = entry.get("path", "")
                if path:
                    curated_by_path[path] = entry
            logger.info(
                "Loaded curated KB metadata: %d entries from kb_index.json",
                len(curated_by_path),
            )
        except Exception as e:
            logger.warning(
                "Failed to parse kb_index.json: %s. Continuing with on-disk scan only.",
                str(e),
            )

    # Step 2 — scan disk and merge. Curated wins on title/summary/tags;
    # path comes from disk so it's always the canonical relative form.
    entries: list[KBEntry] = []
    kb_dir = kb_root / "kb"
    if kb_dir.exists():
        for md_file in sorted(kb_dir.rglob("*.md")):
            rel_path = str(md_file.relative_to(kb_root)).replace("\\", "/")
            curated = curated_by_path.get(rel_path)
            if curated:
                entries.append(KBEntry(
                    path=rel_path,
                    title=curated.get("title") or _extract_first_h1(md_file) or md_file.stem,
                    summary=curated.get("summary", ""),
                    tags=curated.get("tags", []) or [],
                ))
            else:
                # New file not yet curated — use minimal metadata.
                entries.append(KBEntry(
                    path=rel_path,
                    title=_extract_first_h1(md_file) or md_file.stem,
                    summary="",
                    tags=[],
                ))

    # Step 3 — handle curated entries the .md scan missed. The scan in step 2
    # is .md-only, but the curated index can include other file types the
    # team has chosen to expose (e.g. .drawio reference patterns, .txt icon
    # catalogs). For each curated entry not yet in the result list: if the
    # file exists on disk, include it as a curated entry; if not, log it as
    # a drift warning (KB content owner removed the file but forgot to
    # update kb_index.json).
    on_disk_paths = {e.path for e in entries}
    for path, curated in curated_by_path.items():
        if path in on_disk_paths:
            continue
        full_path = kb_root / path
        if full_path.is_file():
            entries.append(KBEntry(
                path=path,
                title=curated.get("title") or full_path.stem,
                summary=curated.get("summary", ""),
                tags=curated.get("tags", []) or [],
            ))
            on_disk_paths.add(path)
        else:
            logger.warning(
                "KB curated entry references missing file (skipped): %s",
                path,
            )
    # Keep deterministic ordering after the late additions.
    entries.sort(key=lambda e: e.path)

    _index = entries
    new_count = sum(1 for e in entries if e.path not in curated_by_path)
    logger.info(
        "Built KB index: %d entries (%d curated, %d uncurated/new)",
        len(_index), len(_index) - new_count, new_count,
    )
    return _index


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
