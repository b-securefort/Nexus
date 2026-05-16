"""
PDF ingestion — reads a link list from an ADO wiki page or a local file,
downloads each PDF via httpx, extracts text with pypdf, and writes normalised
markdown under kb_data/kb/pdf_web/.

Requirements:
  INGEST_PDF_LIST_ENABLED=true
  INGEST_PDF_LIST_WIKI_PATH=<path within the ADO wiki to the link-list page>
  (+ ADO wiki credentials for the list page; PDFs themselves may be open URLs)

Link list format (markdown in the wiki page, one URL per line):
  - [Title](https://example.com/doc.pdf)
  - https://example.com/doc2.pdf
  or plain URLs, one per line.
"""

import logging
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.kb.ingest.normalize import slugify, write_document

logger = logging.getLogger(__name__)

# Match markdown links: [Title](URL)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+\.pdf[^\)]*)\)", re.I)
# Plain URLs ending in .pdf
_PLAIN_URL_RE = re.compile(r"(https?://\S+\.pdf\S*)", re.I)


def _extract_links(text: str) -> list[tuple[str, str]]:
    """Return list of (title, url) pairs from link-list text.

    Accepts markdown links [Title](URL.pdf) and plain PDF URLs.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for m in _MD_LINK_RE.finditer(text):
        title, url = m.group(1).strip(), m.group(2).strip()
        if url not in seen:
            seen.add(url)
            results.append((title, url))

    for m in _PLAIN_URL_RE.finditer(text):
        url = m.group(1).strip().rstrip(")],.'\"")
        if url not in seen:
            seen.add(url)
            # Derive title from filename
            title = Path(urlparse(url).path).stem.replace("-", " ").replace("_", " ")
            results.append((title, url))

    return results


def _fetch_link_list(wiki_path: str, settings) -> str:
    """Fetch the link-list page content from the ADO wiki.

    Falls back to treating INGEST_PDF_LIST_WIKI_PATH as a local file path if
    the ADO wiki credentials are not configured — useful for offline testing.
    """
    import base64

    org = settings.INGEST_ADO_WIKI_ORG.rstrip("/")
    project = settings.INGEST_ADO_WIKI_PROJECT
    wiki_name = settings.INGEST_ADO_WIKI_NAME
    pat = settings.KB_REPO_PAT

    # If ADO credentials are available, fetch from the wiki
    if org and project and wiki_name and pat:
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(f":{pat}".encode()).decode()
        }
        url = (
            f"{org}/{project}/_apis/wiki/wikis/{wiki_name}/pages"
            f"?api-version=7.1&path={wiki_path}&includeContent=true"
        )
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json().get("content", "")
        except Exception as e:
            logger.warning("Could not fetch link-list page from ADO wiki: %s", e)

    # Fallback: treat wiki_path as a local filesystem path
    local = Path(wiki_path)
    if local.exists():
        return local.read_text(encoding="utf-8")

    logger.error("PDF link list not found: wiki_path=%s", wiki_path)
    return ""


def _pdf_to_markdown(pdf_bytes: bytes, title: str) -> str:
    """Extract text from a born-digital PDF and format as markdown."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf is not installed — cannot extract PDF text")
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception as e:
        logger.warning("PDF parse error: %s", e)
        return ""
    pages_text: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages_text.append(text)

    if not pages_text:
        return ""

    # Simple structure: each page becomes a section
    sections = []
    for i, text in enumerate(pages_text, 1):
        sections.append(f"## Page {i}\n\n{text}")

    return "\n\n".join(sections)


def ingest_pdfs(kb_root: Path, settings) -> int:
    """Download PDFs from the link list and write to kb_root.

    Returns the count of documents written (created or updated).
    """
    wiki_path = settings.INGEST_PDF_LIST_WIKI_PATH
    if not wiki_path:
        logger.warning("INGEST_PDF_LIST_WIKI_PATH not set — skipping PDF ingestion")
        return 0

    list_content = _fetch_link_list(wiki_path, settings)
    if not list_content:
        logger.warning("PDF link list is empty — skipping PDF ingestion")
        return 0

    links = _extract_links(list_content)
    logger.info("PDF ingestion: found %d PDF links in link list", len(links))

    written = 0
    for title, url in links:
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Failed to download %s: %s", url, e)
            continue

        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and not url.lower().endswith(".pdf"):
            logger.warning("Skipping non-PDF response from %s (type=%s)", url, content_type)
            continue

        body = _pdf_to_markdown(resp.content, title)
        if not body:
            logger.warning("No text extracted from %s — may be a scanned PDF (no OCR support)", url)
            continue

        write_document(
            kb_root=kb_root,
            source="pdf_web",
            title=title,
            body=body,
            source_url=url,
            original_path=url,
        )
        written += 1
        logger.info("Ingested PDF: %s", title)

    logger.info("PDF ingestion complete: %d documents written", written)
    return written
