"""One-shot script to swap Azure2 icons for relevant Devicon SVGs on the
already-rendered Nexus diagrams. Throwaway: do not commit; delete after the
diagrams are re-rendered.

Strategy:
- Backups already taken at output/backup/ (preserved separately).
- For each .drawio file, parse the XML, walk every mxCell vertex.
- Determine the new icon by matching against the cell's `value` (label).
- Real Azure services (Azure OpenAI, Azure ARM, App Configuration, Entra ID,
  Azure DevOps as repo backing) keep their Azure2 icons.
- AOAI-call nodes (Rephrase, LLM judge, LLM summariser, Azure OpenAI embed)
  keep the Azure OpenAI icon, because the node's job IS the AOAI call.
- Everything else swaps to a Devicon SVG, base64-embedded inline so the
  rendered .drawio is fully self-contained (no CDN dependency at view time).
- Re-render each .drawio to PNG via render_drawio_to_disk().
"""
from __future__ import annotations

import base64
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.tools.generic.render_drawio import render_drawio_to_disk

DEVICON_BASE = "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons"
ICON_URLS = {
    "python": f"{DEVICON_BASE}/python/python-original.svg",
    "fastapi": f"{DEVICON_BASE}/fastapi/fastapi-original.svg",
    "react": f"{DEVICON_BASE}/react/react-original.svg",
    "sqlite": f"{DEVICON_BASE}/sqlite/sqlite-original.svg",
    "github": f"{DEVICON_BASE}/github/github-original.svg",
}


def fetch_data_uri(url: str) -> str:
    # Strategy switched: drawio desktop CLI's headless PNG export does not
    # handle data: URIs reliably in the image= style attribute. Use the
    # direct CDN URL; the renderer fetches it at export time.
    # Trade-off: viewers (and re-renderers) need network access to the CDN
    # while the diagram is open.
    return url


def build_icon_registry() -> dict[str, str]:
    print("Fetching Devicon SVGs ...")
    out = {}
    for key, url in ICON_URLS.items():
        out[key] = fetch_data_uri(url)
        print(f"  {key}: {len(out[key])} chars")
    return out


# Label-substring routing. Order matters — longer / more specific matches first.
# Returns the new image data URI (Devicon) or None to keep the existing Azure2 icon.
def pick_icon(value: str, icons: dict[str, str]) -> str | None:
    v = value.lower()

    # --- Keep Azure2 icons for real Azure services ---
    azure_keep = (
        "azure openai", "azure arm", "app configuration", "entra id",
        "azure devops",
    )
    if any(k in v for k in azure_keep):
        return None

    # --- AOAI-call nodes keep openai icon (the node IS an LLM call) ---
    aoai_call_keep = (
        "rephrase via llm", "llm judge", "llm summariser",
        "openai embed",  # drill 3: "Azure OpenAI embed" (ingest + query)
    )
    if any(k in v for k in aoai_call_keep):
        return None

    # --- External / UI ---
    if "frontend" in v:
        return icons["react"]
    if "kb git" in v:
        return icons["github"]

    # --- API endpoints (FastAPI routes) ---
    if (
        "api/chat" in v or "/api/skills" in v or "/api/tools" in v
        or "/api/conversations" in v or "sse done" in v
        or "post /api" in v or "get /api" in v
    ):
        return icons["fastapi"]

    # --- Storage (SQLite, including the agent_learnings + kb_chunks tables) ---
    if (
        "app.db" in v or "kb_chunks\n" in v or v.startswith("kb_chunks")
        or "agent_learnings\nstatus" in v or v.startswith("agent_learnings")
        or "conversations.skill_snapshot" in v
    ):
        return icons["sqlite"]

    # --- User stays as Globe (external person via web) ---
    if v.strip() == "user":
        return None

    # --- Everything else with an azure2 icon is Nexus Python code ---
    # This catches: Orchestrator, Compaction, Learnings retrieval, Circuit
    # breaker, Tool dispatch, Approval gate / ask_user pause, Read-only tools,
    # Mutating tools, Tool execute, Save user msg, Skill allowlist, Semaphore,
    # ARM token preflight, Blocked-prefix check, subprocess.run, Tool result,
    # Auth middleware, _ACCESS_MAP, Hardcoded defaults, retrieve_relevant_learnings,
    # Inject markers, mark_learning_outcome, Derive raw learning, Gate 1/2/3,
    # Rejected entry, search_kb_hybrid, Chunker, git sync, BM25 stage,
    # Vector stage, Reciprocal Rank Fusion, Top-K chunks, etc.
    return icons["python"]


IMAGE_ATTR_RE = re.compile(r"image=[^;\"]+")


def swap_icons_in_xml(xml_text: str, icons: dict[str, str]) -> str:
    # Use simple regex pass on the raw XML to avoid ElementTree mangling
    # the order of style attributes or quoting. We only touch cells that
    # have shape=image styles.
    def replace_cell(match: re.Match) -> str:
        opening = match.group(0)
        # Extract value="..." for label routing
        m_val = re.search(r'value="([^"]*)"', opening)
        if not m_val:
            return opening
        value = m_val.group(1)
        # Decode &lt;br&gt; back to newlines for label matching
        value_decoded = (
            value.replace("&lt;br&gt;", "\n")
                 .replace("&amp;", "&")
                 .replace("&quot;", '"')
        )
        new_icon = pick_icon(value_decoded, icons)
        if new_icon is None:
            return opening
        # Replace the image=... portion of the style attribute
        return IMAGE_ATTR_RE.sub(f"image={new_icon}", opening, count=1)

    # Match opening mxCell tags that contain shape=image
    cell_open_re = re.compile(r'<mxCell\b[^>]*shape=image[^>]*>')
    return cell_open_re.sub(replace_cell, xml_text)


def process_file(drawio_path: Path, icons: dict[str, str]) -> tuple[int, int]:
    original = drawio_path.read_text(encoding="utf-8")
    swapped = swap_icons_in_xml(original, icons)
    # Count swap occurrences
    orig_image_refs = len(re.findall(r'image=img/lib/azure2/', original))
    new_image_refs = len(re.findall(r'image=data:image/', swapped))
    drawio_path.write_text(swapped, encoding="utf-8")
    return orig_image_refs, new_image_refs


def main() -> None:
    icons = build_icon_registry()
    out_dir = Path("output")
    targets = sorted(out_dir.glob("nexus-*.drawio"))
    print(f"\nFound {len(targets)} .drawio files to process\n")
    for path in targets:
        before, after = process_file(path, icons)
        print(f"  {path.name}: azure2={before}, data-uri now={after}")

    print("\nRe-rendering PNGs ...")
    for path in targets:
        out_path, mode, err = render_drawio_to_disk(path.name, "png")
        if out_path is not None:
            kb = out_path.stat().st_size // 1024
            print(f"  {path.name} -> {out_path.name} ({kb} KB, via {mode})")
        else:
            print(f"  {path.name} -> ERROR: {err}")


if __name__ == "__main__":
    main()
