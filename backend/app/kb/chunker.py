"""
Markdown-aware chunker for KB hybrid retrieval.

Splitting strategy:
  - Split at H2 boundaries (## headings) first.
  - If a resulting segment exceeds KB_CHUNK_MAX_CHARS, split again at the
    first paragraph boundary (blank line) outside a code fence or table.
  - 15% overlap is carried forward when splitting by size (not at headings,
    where the boundary is already semantically clean).
  - Never split inside a fenced code block (``` ... ```) or a table block.

Each chunk carries:
  - heading  : "Doc Title > H2 > H3" breadcrumb at the point of emission
  - text     : the raw markdown fragment (front-matter stripped)
  - source_url: pulled from YAML front-matter if present, else None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.config import get_settings

# Regex that matches YAML front-matter fences (--- at col 0)
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
# Matches source_url inside front-matter
_SOURCE_URL_RE = re.compile(r"^source_url:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
# Heading patterns
_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+)", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+)", re.MULTILINE)
# Code fence toggle
_FENCE_RE = re.compile(r"^```")
# Table row
_TABLE_RE = re.compile(r"^\|")


@dataclass
class Chunk:
    kb_path: str
    chunk_idx: int
    heading: str      # "Doc Title > Section > Subsection"
    text: str
    source_url: str | None = None


def _strip_frontmatter(content: str) -> tuple[str, str | None]:
    """Remove YAML front-matter and return (body, source_url)."""
    source_url: str | None = None
    m = _FRONTMATTER_RE.match(content)
    if m:
        fm_block = m.group(0)
        url_m = _SOURCE_URL_RE.search(fm_block)
        if url_m:
            source_url = url_m.group(1).strip()
        content = content[m.end():]
    return content.lstrip("\n"), source_url


def _heading_breadcrumb(h1: str, h2: str, h3: str) -> str:
    parts = [p for p in (h1, h2, h3) if p]
    return " > ".join(parts) if parts else "Document"


def _split_at_paragraph(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split text at blank-line boundaries outside fences/tables, respecting max_chars."""
    segments: list[str] = []
    in_fence = False
    in_table = False
    buf: list[str] = []
    buf_len = 0
    carry: str = ""  # overlap from previous segment

    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track fenced code blocks
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
        # Track table blocks (runs of | lines)
        if not in_fence:
            if _TABLE_RE.match(stripped):
                in_table = True
            elif in_table and stripped == "":
                in_table = False

        buf.append(line)
        buf_len += len(line)

        # Emit on blank line outside a fence/table when over limit
        is_blank = stripped == ""
        if is_blank and not in_fence and not in_table and buf_len >= max_chars:
            segment = carry + "".join(buf).rstrip("\n")
            if segment.strip():
                segments.append(segment)
            # Overlap: last overlap_chars of current segment become carry
            carry = segment[-overlap_chars:] if overlap_chars else ""
            buf = []
            buf_len = 0

        i += 1

    # Flush remainder
    remainder = carry + "".join(buf).rstrip("\n")
    if remainder.strip():
        segments.append(remainder)

    return segments or [text]


def chunk_markdown(kb_path: str, content: str) -> list[Chunk]:
    """Chunk a markdown file into Chunk objects.

    Args:
        kb_path:  Relative path used as the chunk's kb_path field.
        content:  Raw file contents (may include YAML front-matter).

    Returns:
        List of Chunk objects, index starting at 0.
    """
    settings = get_settings()
    max_chars = settings.KB_CHUNK_MAX_CHARS
    overlap_chars = int(max_chars * settings.KB_CHUNK_OVERLAP_FRACTION)

    body, source_url = _strip_frontmatter(content)

    # Extract document title from first H1 (or filename)
    h1_m = _H1_RE.search(body)
    doc_title = h1_m.group(1).strip() if h1_m else Path(kb_path).stem.replace("-", " ").title()

    # Split on H2 boundaries (primary), then H3 within each H2 block (secondary).
    # Tracking H3 at flush time (old approach) recorded the *last* H3 in a block
    # for *all* content in that block — wrong when a block has multiple subsections.
    # Two-pass: first collect (h2, raw_block) pairs; then within each block collect
    # (h2, h3, sub_block) pairs by splitting at H3 lines.

    # Pass 1 — split at H2 boundaries
    h2_raw: list[tuple[str, str]] = []   # (h2_title, text_block)
    current_h2 = ""
    current_lines: list[str] = []

    for line in body.splitlines(keepends=True):
        h2_m = _H2_RE.match(line)
        if h2_m:
            if current_lines:
                h2_raw.append((current_h2, "".join(current_lines)))
            current_h2 = h2_m.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        h2_raw.append((current_h2, "".join(current_lines)))

    if not h2_raw:
        h2_raw = [("", body)]

    # Pass 2 — split each H2 block at H3 boundaries so each sub-block
    # carries the H3 that was *active when its content was written*.
    h2_splits: list[tuple[str, str, str]] = []   # (h2_title, h3_title, text_block)
    for h2_title, h2_block in h2_raw:
        current_h3 = ""
        h3_lines: list[str] = []
        for line in h2_block.splitlines(keepends=True):
            h3_m = _H3_RE.match(line)
            if h3_m:
                if h3_lines:
                    h2_splits.append((h2_title, current_h3, "".join(h3_lines)))
                current_h3 = h3_m.group(1).strip()
                h3_lines = [line]
            else:
                h3_lines.append(line)
        if h3_lines:
            h2_splits.append((h2_title, current_h3, "".join(h3_lines)))

    chunks: list[Chunk] = []
    chunk_idx = 0

    for h2_title, h3_title, block_text in h2_splits:
        block_text = block_text.strip()
        if not block_text:
            continue

        heading = _heading_breadcrumb(doc_title, h2_title, h3_title)

        if len(block_text) <= max_chars:
            chunks.append(Chunk(
                kb_path=kb_path,
                chunk_idx=chunk_idx,
                heading=heading,
                text=block_text,
                source_url=source_url,
            ))
            chunk_idx += 1
        else:
            # Block too large — split at paragraph boundaries with overlap
            sub_segments = _split_at_paragraph(block_text, max_chars, overlap_chars)
            for seg in sub_segments:
                if not seg.strip():
                    continue
                chunks.append(Chunk(
                    kb_path=kb_path,
                    chunk_idx=chunk_idx,
                    heading=heading,
                    text=seg,
                    source_url=source_url,
                ))
                chunk_idx += 1

    return chunks
