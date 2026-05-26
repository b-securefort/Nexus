"""
ADO wiki ingestion — fetches pages from an Azure DevOps wiki via REST API
and writes them as normalised markdown under kb_data/kb/ado_wiki/<label>/.

Multi-source design (see DESIGN.md §5 2026-05-26): each configured source
in settings.INGEST_ADO_WIKI_SOURCES is ingested independently. A
``_source_meta.json`` sentinel in each label directory pins the
(org, project, wiki) triple so accidental label rebinds fail loudly
instead of silently swapping content.

Requires:
  INGEST_ADO_WIKI_SOURCES='[{"label":"<slug>","org":"https://dev.azure.com/<org>",
                            "project":"<project>","wiki":"<wiki-name>"}, ...]'
  KB_REPO_PAT=<personal access token with org-level Wiki (read) scope>
"""

import base64
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import AdoWikiSource
from app.kb.ingest.normalize import write_document

logger = logging.getLogger(__name__)

# Sentinel file pinning the (org, project, wiki) triple bound to a label dir.
# If the configured triple drifts from what's on disk, ingestion aborts for
# that source — see DESIGN.md §5 2026-05-26.
_SENTINEL_FILENAME = "_source_meta.json"


class LabelRebindError(RuntimeError):
    """The (org, project, wiki) triple in config differs from the sentinel
    on disk. Either the label was accidentally rebound to a different wiki,
    or the user intends a rebind and needs to delete the label directory."""

# ADO wiki uses [[Page Name]] or [[Page Name|Display]] internal links.
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]")


def _auth_header(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _normalize_links(text: str, base_path: str) -> str:
    """Convert ADO [[Page Name]] links to relative markdown links."""

    def _replace(m: re.Match) -> str:
        page = m.group(1).strip()
        display = (m.group(2) or page).strip()
        slug = page.lower().replace(" ", "-")
        return f"[{display}]({slug}.md)"

    return _WIKI_LINK_RE.sub(_replace, text)


def _api_base(org: str, project: str, wiki_name: str) -> str:
    org = org.rstrip("/")
    return f"{org}/{project}/_apis/wiki/wikis/{wiki_name}"


def _list_pages(
    client: httpx.Client, org: str, project: str, wiki_name: str
) -> list[dict]:
    """Return a flat list of all wiki page dicts (id, path, gitItemPath)."""
    url = f"{_api_base(org, project, wiki_name)}/pages"
    params = {
        "api-version": "7.1",
        "recursionLevel": "full",
        "$top": 1000,
    }
    try:
        resp = client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("ADO wiki page list failed: %s", e)
        return []

    pages: list[dict] = []
    _flatten(data.get("value", data), pages)
    return pages


def _flatten(node: dict | list, out: list[dict]) -> None:
    """Recursively flatten a tree of wiki page nodes."""
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
        return
    if isinstance(node, dict):
        if "path" in node:
            out.append(node)
        for child in node.get("subPages", []):
            _flatten(child, out)


def _fetch_page(
    client: httpx.Client, org: str, project: str, wiki_name: str, page_path: str
) -> str | None:
    """Fetch raw markdown content for a single wiki page."""
    url = f"{_api_base(org, project, wiki_name)}/pages"
    params = {
        "api-version": "7.1",
        "path": page_path,
        "includeContent": "true",
    }
    try:
        resp = client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("content", "")
    except Exception as e:
        logger.warning("Failed to fetch page %s: %s", page_path, e)
        return None


def _check_sentinel(label_dir: Path, source: AdoWikiSource) -> None:
    """Verify that the on-disk label directory is bound to the configured
    (org, project, wiki) triple. Writes the sentinel if absent; raises
    LabelRebindError if it exists and disagrees with config.

    The sentinel is intentionally separate from front-matter so a single
    file check catches rebinds before any wiki API call is made.
    """
    sentinel_path = label_dir / _SENTINEL_FILENAME
    configured = {
        "org": source.org.rstrip("/"),
        "project": source.project,
        "wiki": source.wiki,
    }

    if sentinel_path.exists():
        try:
            stored = json.loads(sentinel_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise LabelRebindError(
                f"label '{source.label}' sentinel at {sentinel_path} is "
                f"unreadable ({e}). Delete the file and the directory to "
                "re-establish the binding, or restore from git history."
            )
        stored_triple = {k: stored.get(k, "") for k in ("org", "project", "wiki")}
        if stored_triple != configured:
            raise LabelRebindError(
                f"label '{source.label}' was previously bound to "
                f"org={stored_triple['org']}, project={stored_triple['project']}, "
                f"wiki={stored_triple['wiki']} but config now says "
                f"org={configured['org']}, project={configured['project']}, "
                f"wiki={configured['wiki']}. "
                f"If this rebind is intentional, delete {label_dir} and let "
                "Nexus reindex from scratch. If unintentional, fix the label "
                "in .env."
            )
        return

    label_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        **configured,
        "label": source.label,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    sentinel_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("ADO wiki: wrote sentinel for label '%s'", source.label)


def ingest_ado_wiki(kb_root: Path, source: AdoWikiSource, settings) -> int:
    """Fetch all pages from one configured ADO wiki source and write to
    ``kb_root/kb/ado_wiki/<label>/``.

    Returns the count of documents written (created or updated).
    Raises LabelRebindError if the sentinel disagrees with the source config.
    """
    pat = settings.KB_REPO_PAT
    if not pat:
        logger.warning(
            "ADO wiki ingestion for label '%s' skipped — KB_REPO_PAT is empty",
            source.label,
        )
        return 0

    org = source.org.rstrip("/")
    project = source.project
    wiki_name = source.wiki

    label_dir = kb_root / "kb" / "ado_wiki" / source.label
    _check_sentinel(label_dir, source)

    headers = _auth_header(pat)
    written = 0

    with httpx.Client(headers=headers) as client:
        pages = _list_pages(client, org, project, wiki_name)
        logger.info(
            "ADO wiki [%s]: found %d pages in %s/%s",
            source.label, len(pages), project, wiki_name,
        )

        for page in pages:
            page_path: str = page.get("path", "")
            if not page_path or page_path == "/":
                continue

            content = _fetch_page(client, org, project, wiki_name, page_path)
            if content is None:
                continue

            # Use last path segment as title
            title = page_path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").strip()
            if not title:
                title = "Untitled"

            body = _normalize_links(content, page_path)
            page_url = (
                f"{org}/{project}/_wiki/wikis/{wiki_name}?pagePath={page_path}"
            )

            write_document(
                kb_root=kb_root,
                source="ado_wiki",
                source_instance=source.label,
                title=title,
                body=body,
                source_url=page_url,
                original_path=page_path,
            )
            written += 1

    logger.info(
        "ADO wiki [%s] ingestion complete: %d pages written",
        source.label, written,
    )
    return written
