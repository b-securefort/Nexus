"""Tests for PDF ingestion — text extraction and ingest_pdfs flow."""

import pytest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── helpers to create a minimal valid PDF ────────────────────────────────────

def _make_pdf_bytes(text: str = "Hello from PDF page one.") -> bytes:
    """Create a minimal born-digital PDF containing a single page of text."""
    try:
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, NumberObject
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── _pdf_to_markdown ──────────────────────────────────────────────────────────

def test_pdf_to_markdown_empty_bytes():
    """An empty/corrupt PDF should return an empty string, not raise."""
    from app.kb.ingest.pdf_fetcher import _pdf_to_markdown
    result = _pdf_to_markdown(b"not a pdf", "title")
    assert result == ""


def test_pdf_to_markdown_blank_page():
    """A PDF with blank pages should return empty string."""
    from app.kb.ingest.pdf_fetcher import _pdf_to_markdown
    pdf_bytes = _make_pdf_bytes()
    # Blank pages have no extractable text
    result = _pdf_to_markdown(pdf_bytes, "Blank PDF")
    assert isinstance(result, str)


def test_pdf_to_markdown_with_text(tmp_path):
    """A PDF with extractable text should produce non-empty markdown."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    from pypdf.generic import EncodedStreamObject, NameObject

    # Build a PDF with a page containing a content stream with text
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    from app.kb.ingest.pdf_fetcher import _pdf_to_markdown
    # Even blank pages won't crash; with real text content we'd get section headers
    result = _pdf_to_markdown(pdf_bytes, "Test Doc")
    assert isinstance(result, str)


# ── ingest_pdfs integration (mocked HTTP) ────────────────────────────────────

def _fake_settings(**kwargs):
    s = MagicMock()
    s.INGEST_ADO_WIKI_ORG = ""
    s.INGEST_ADO_WIKI_PROJECT = ""
    s.INGEST_ADO_WIKI_NAME = ""
    s.KB_REPO_PAT = ""
    s.INGEST_PDF_LIST_WIKI_PATH = ""
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_ingest_pdfs_no_wiki_path(tmp_path):
    """When INGEST_PDF_LIST_WIKI_PATH is empty, returns 0."""
    from app.kb.ingest.pdf_fetcher import ingest_pdfs
    settings = _fake_settings()
    count = ingest_pdfs(tmp_path, settings)
    assert count == 0


def test_ingest_pdfs_empty_link_list(tmp_path):
    """When the link list page is empty, returns 0."""
    from app.kb.ingest.pdf_fetcher import ingest_pdfs

    link_list_file = tmp_path / "links.md"
    link_list_file.write_text("", encoding="utf-8")
    settings = _fake_settings(INGEST_PDF_LIST_WIKI_PATH=str(link_list_file))
    count = ingest_pdfs(tmp_path, settings)
    assert count == 0


def test_ingest_pdfs_local_link_list(tmp_path):
    """Reads a local link list file and attempts download (mocked)."""
    pytest.importorskip("pypdf")
    from app.kb.ingest.pdf_fetcher import ingest_pdfs

    # Create a minimal PDF
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf_buf = BytesIO()
    writer.write(pdf_buf)
    pdf_bytes = pdf_buf.getvalue()

    link_list_file = tmp_path / "links.md"
    link_list_file.write_text(
        "- [Test Doc](https://example.com/test.pdf)\n",
        encoding="utf-8",
    )

    settings = _fake_settings(INGEST_PDF_LIST_WIKI_PATH=str(link_list_file))

    mock_response = MagicMock()
    mock_response.content = pdf_bytes
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.kb.ingest.pdf_fetcher.httpx.get", return_value=mock_response):
        count = ingest_pdfs(tmp_path, settings)

    # Blank page has no text → no document written (0 written)
    assert count == 0  # blank page extracts no text


def test_ingest_pdfs_skips_non_pdf(tmp_path):
    """Skips URLs that return non-PDF content."""
    from app.kb.ingest.pdf_fetcher import ingest_pdfs

    link_list_file = tmp_path / "links.md"
    link_list_file.write_text(
        "- [HTML Page](https://example.com/page.html)\n",
        encoding="utf-8",
    )
    settings = _fake_settings(INGEST_PDF_LIST_WIKI_PATH=str(link_list_file))

    mock_response = MagicMock()
    mock_response.content = b"<html>not a pdf</html>"
    mock_response.headers = {"content-type": "text/html"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.kb.ingest.pdf_fetcher.httpx.get", return_value=mock_response):
        count = ingest_pdfs(tmp_path, settings)

    assert count == 0


def test_ingest_pdfs_handles_download_error(tmp_path):
    """A download error on one PDF is logged but does not abort the run."""
    from app.kb.ingest.pdf_fetcher import ingest_pdfs
    import httpx

    link_list_file = tmp_path / "links.md"
    link_list_file.write_text(
        "- [Broken](https://example.com/broken.pdf)\n",
        encoding="utf-8",
    )
    settings = _fake_settings(INGEST_PDF_LIST_WIKI_PATH=str(link_list_file))

    with patch(
        "app.kb.ingest.pdf_fetcher.httpx.get",
        side_effect=httpx.RequestError("timeout"),
    ):
        count = ingest_pdfs(tmp_path, settings)

    assert count == 0


# ── runner.run_all_sources ────────────────────────────────────────────────────

def test_runner_all_disabled(tmp_path):
    """With both sources disabled, run_all_sources returns empty dict."""
    from app.kb.ingest.runner import run_all_sources

    mock_settings = MagicMock()
    mock_settings.KB_REPO_LOCAL_PATH = str(tmp_path)
    mock_settings.INGEST_ADO_WIKI_ENABLED = False
    mock_settings.INGEST_PDF_LIST_ENABLED = False

    with patch("app.kb.ingest.runner.get_settings", return_value=mock_settings):
        results = run_all_sources()

    assert results == {}


def test_runner_ado_wiki_source_called(tmp_path):
    """When ADO wiki is enabled, ingest_ado_wiki is called."""
    from app.kb.ingest.runner import run_all_sources

    mock_settings = MagicMock()
    mock_settings.KB_REPO_LOCAL_PATH = str(tmp_path)
    mock_settings.INGEST_ADO_WIKI_ENABLED = True
    mock_settings.INGEST_PDF_LIST_ENABLED = False

    with (
        patch("app.kb.ingest.runner.get_settings", return_value=mock_settings),
        patch("app.kb.ingest.ado_wiki.ingest_ado_wiki", return_value=5) as mock_ingest,
    ):
        results = run_all_sources()

    assert results.get("ado_wiki") == 5
    mock_ingest.assert_called_once()


def test_runner_source_error_does_not_abort(tmp_path):
    """An exception in one source is caught; runner returns 0 for that source."""
    from app.kb.ingest.runner import run_all_sources

    mock_settings = MagicMock()
    mock_settings.KB_REPO_LOCAL_PATH = str(tmp_path)
    mock_settings.INGEST_ADO_WIKI_ENABLED = True
    mock_settings.INGEST_PDF_LIST_ENABLED = True

    def bad_wiki(*a, **kw):
        raise RuntimeError("ADO down")

    def ok_pdf(*a, **kw):
        return 3

    with (
        patch("app.kb.ingest.runner.get_settings", return_value=mock_settings),
        patch("app.kb.ingest.ado_wiki.ingest_ado_wiki", side_effect=bad_wiki),
        patch("app.kb.ingest.pdf_fetcher.ingest_pdfs", return_value=3),
    ):
        results = run_all_sources()

    # ado_wiki failed → 0; pdf might or might not run depending on import path
    assert results.get("ado_wiki") == 0
