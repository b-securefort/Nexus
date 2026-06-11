"""Archetype skeleton library (v1: skill-level).

Most platform diagrams are one of a handful of stories; the expensive part of
drawing them — band structure, spine direction, side-lane placement — is the
same every time. The library bakes those decisions into complete,
detector-clean starter IRs that the agent copies and edits instead of
designing layout from scratch.

The skeletons live in the KB (`kb/patterns/diagram-archetypes.md`) because the
agent reads them at runtime with `read_kb_file` — this module is the
engine-side reader of the SAME document, so tests (and a future
archetype-aware tool parameter) verify exactly what the agent will copy. Each
`## <slug> — <title>` section must carry one ```json block holding a full
Diagram IR in the authoring contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DOC = (
    Path(__file__).resolve().parents[2]
    / "kb_data" / "kb" / "patterns" / "diagram-archetypes.md"
)

_HEADING_RE = re.compile(r"^## (?P<slug>[a-z0-9-]+) — ", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def load_archetypes(doc_path: Path | None = None) -> dict[str, dict]:
    """Parse the KB archetype doc into {slug: {"doc": section_text, "ir": dict}}."""
    text = (doc_path or _DOC).read_text(encoding="utf-8")
    headings = list(_HEADING_RE.finditer(text))
    out: dict[str, dict] = {}
    for i, m in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[m.start():end]
        block = _JSON_BLOCK_RE.search(section)
        if block is None:
            raise ValueError(f"archetype '{m['slug']}' has no ```json skeleton block")
        out[m["slug"]] = {"doc": section, "ir": json.loads(block.group(1))}
    return out
