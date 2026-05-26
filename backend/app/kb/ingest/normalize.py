"""
Shared utilities for ingested KB documents:
  - front-matter writing
  - filename sanitization
  - output-path helpers
"""

import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path


_UNSAFE_RE = re.compile(r"[^\w\-]")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def slugify(text: str, max_len: int = 80) -> str:
    """Convert arbitrary text to a safe filename stem."""
    slug = text.lower().strip()
    slug = slug.replace(" ", "-")
    slug = _UNSAFE_RE.sub("", slug)
    slug = _MULTI_DASH_RE.sub("-", slug)
    slug = slug.strip("-")
    return slug[:max_len] or "doc"


def write_document(
    *,
    kb_root: Path,
    source: str,
    title: str,
    body: str,
    source_url: str = "",
    original_path: str = "",
    source_instance: str | None = None,
    extra_front_matter: dict | None = None,
) -> Path:
    """Write a normalised markdown document under ``kb_root/kb/<source>/``
    (or ``kb_root/kb/<source>/<source_instance>/`` when source_instance is set).

    The file is only (re)written if the body content has changed, so repeated
    ingest runs are cheap and do not disturb mtime on unchanged files.

    Returns the absolute path of the written file.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = slugify(title)
    dest_dir = kb_root / "kb" / source
    if source_instance:
        dest_dir = dest_dir / source_instance
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.md"

    front_matter_lines = [
        "---",
        f'source: "{source}"',
    ]
    if source_instance:
        front_matter_lines.append(f'source_instance: "{source_instance}"')
    if source_url:
        front_matter_lines.append(f'source_url: "{source_url}"')
    if original_path:
        front_matter_lines.append(f'original_path: "{original_path}"')
    front_matter_lines.append(f'last_synced: "{now}"')
    front_matter_lines.append(f'title: "{title}"')

    if extra_front_matter:
        for k, v in extra_front_matter.items():
            front_matter_lines.append(f'{k}: "{v}"')

    front_matter_lines.append("---")

    front = "\n".join(front_matter_lines) + "\n"
    full = front + "\n# " + title + "\n\n" + body.strip() + "\n"

    # Skip write if content unchanged (avoids spurious mtime changes)
    if dest.exists() and dest.read_text(encoding="utf-8") == full:
        return dest

    dest.write_text(full, encoding="utf-8")
    return dest


def strip_front_matter(text: str) -> str:
    """Remove YAML front-matter block from a markdown string."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")
