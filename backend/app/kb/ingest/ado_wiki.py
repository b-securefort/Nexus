"""
ADO wiki ingestion — fetches pages from an Azure DevOps wiki via REST API
and writes them as normalised markdown under kb_data/kb/ado_wiki/.

Requires:
  INGEST_ADO_WIKI_ENABLED=true
  INGEST_ADO_WIKI_ORG=https://dev.azure.com/<org>
  INGEST_ADO_WIKI_PROJECT=<project>
  INGEST_ADO_WIKI_NAME=<wiki name>
  KB_REPO_PAT=<personal access token with Wiki (read) scope>
"""

import base64
import logging
import re
from pathlib import Path
from typing import Generator

import httpx

from app.kb.ingest.normalize import write_document

logger = logging.getLogger(__name__)

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


def ingest_ado_wiki(kb_root: Path, settings) -> int:
    """Fetch all pages from the configured ADO wiki and write to kb_root.

    Returns the count of documents written (created or updated).
    """
    org = settings.INGEST_ADO_WIKI_ORG.rstrip("/")
    project = settings.INGEST_ADO_WIKI_PROJECT
    wiki_name = settings.INGEST_ADO_WIKI_NAME
    pat = settings.KB_REPO_PAT

    if not all([org, project, wiki_name, pat]):
        logger.warning(
            "ADO wiki ingestion skipped — missing config "
            "(INGEST_ADO_WIKI_ORG, INGEST_ADO_WIKI_PROJECT, "
            "INGEST_ADO_WIKI_NAME, KB_REPO_PAT)"
        )
        return 0

    headers = _auth_header(pat)
    written = 0

    with httpx.Client(headers=headers) as client:
        pages = _list_pages(client, org, project, wiki_name)
        logger.info("ADO wiki: found %d pages in %s/%s", len(pages), project, wiki_name)

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
                title=title,
                body=body,
                source_url=page_url,
                original_path=page_path,
            )
            written += 1

    logger.info("ADO wiki ingestion complete: %d pages written", written)
    return written
