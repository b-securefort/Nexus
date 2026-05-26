"""Tests for kb/ingest/normalize.py and ado_wiki link normalization."""

import pytest
from pathlib import Path

from app.kb.ingest.normalize import slugify, write_document, strip_front_matter
from app.kb.ingest.ado_wiki import _normalize_links, _flatten


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"

def test_slugify_special_chars():
    assert slugify("ADR-001: Multi-Region Failover!") == "adr-001-multi-region-failover"

def test_slugify_unicode():
    # Non-ascii letters are stripped by \w (which matches ASCII in default re)
    result = slugify("Café & résumé")
    assert " " not in result
    assert result  # non-empty

def test_slugify_max_len():
    long_title = "a" * 200
    assert len(slugify(long_title)) == 80

def test_slugify_empty():
    assert slugify("") == "doc"

def test_slugify_only_special():
    assert slugify("!@#$%") == "doc"


# ── write_document ────────────────────────────────────────────────────────────

def test_write_document_creates_file(tmp_path):
    dest = write_document(
        kb_root=tmp_path,
        source="ado_wiki",
        title="My Test Page",
        body="Some content here.",
        source_url="https://example.com/page",
        original_path="/My Test Page",
    )
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "source: \"ado_wiki\"" in content
    assert "source_url: \"https://example.com/page\"" in content
    assert "title: \"My Test Page\"" in content
    assert "# My Test Page" in content
    assert "Some content here." in content


def test_write_document_path_is_under_source(tmp_path):
    dest = write_document(
        kb_root=tmp_path,
        source="pdf_web",
        title="A PDF Doc",
        body="PDF content.",
    )
    assert dest.parent == tmp_path / "kb" / "pdf_web"


def test_write_document_skips_unchanged(tmp_path):
    kwargs = dict(kb_root=tmp_path, source="s", title="T", body="B")
    dest1 = write_document(**kwargs)
    mtime1 = dest1.stat().st_mtime

    # Write again with same content
    import time; time.sleep(0.01)
    dest2 = write_document(**kwargs)
    mtime2 = dest2.stat().st_mtime

    assert mtime1 == mtime2  # file was not rewritten


def test_write_document_updates_on_change(tmp_path):
    dest = write_document(kb_root=tmp_path, source="s", title="T", body="Old content")
    mtime1 = dest.stat().st_mtime

    import time; time.sleep(0.05)
    dest2 = write_document(kb_root=tmp_path, source="s", title="T", body="New content")
    mtime2 = dest2.stat().st_mtime

    assert mtime2 > mtime1
    assert "New content" in dest2.read_text(encoding="utf-8")


def test_write_document_front_matter_ordering(tmp_path):
    dest = write_document(
        kb_root=tmp_path,
        source="ado_wiki",
        title="Order Test",
        body="content",
        source_url="https://x.com/p",
        original_path="/order-test",
    )
    text = dest.read_text(encoding="utf-8")
    lines = text.split("\n")
    # First line is ---
    assert lines[0] == "---"
    # source is immediately after ---
    assert lines[1].startswith('source:')
    assert lines[2].startswith('source_url:')
    assert lines[3].startswith('original_path:')


def test_write_document_extra_front_matter(tmp_path):
    dest = write_document(
        kb_root=tmp_path,
        source="s",
        title="T",
        body="B",
        extra_front_matter={"custom_key": "custom_val"},
    )
    assert 'custom_key: "custom_val"' in dest.read_text(encoding="utf-8")


def test_write_document_source_instance_subdir(tmp_path):
    """source_instance adds a subdirectory under <source>/ and a front-matter field."""
    dest = write_document(
        kb_root=tmp_path,
        source="ado_wiki",
        source_instance="platform",
        title="Page",
        body="Content.",
        source_url="https://dev.azure.com/myorg/Platform/_wiki/wikis/Platform.wiki?pagePath=/Page",
    )
    assert dest.parent == tmp_path / "kb" / "ado_wiki" / "platform"
    text = dest.read_text(encoding="utf-8")
    assert 'source: "ado_wiki"' in text
    assert 'source_instance: "platform"' in text


def test_write_document_no_source_instance_no_subdir(tmp_path):
    """Backwards-compatible behaviour: no source_instance = no subdir, no field."""
    dest = write_document(
        kb_root=tmp_path,
        source="pdf_web",
        title="Doc",
        body="Body",
    )
    assert dest.parent == tmp_path / "kb" / "pdf_web"
    assert 'source_instance' not in dest.read_text(encoding="utf-8")


# ── _source_meta.json sentinel (label-rebind defence) ─────────────────────────

def test_sentinel_written_on_first_ingest(tmp_path):
    from app.config import AdoWikiSource
    from app.kb.ingest.ado_wiki import _check_sentinel

    src = AdoWikiSource(
        label="platform",
        org="https://dev.azure.com/myorg",
        project="Platform",
        wiki="Platform.wiki",
    )
    label_dir = tmp_path / "kb" / "ado_wiki" / "platform"
    _check_sentinel(label_dir, src)
    sentinel = label_dir / "_source_meta.json"
    assert sentinel.exists()
    import json
    data = json.loads(sentinel.read_text(encoding="utf-8"))
    assert data["org"] == "https://dev.azure.com/myorg"
    assert data["project"] == "Platform"
    assert data["wiki"] == "Platform.wiki"
    assert data["label"] == "platform"


def test_sentinel_matching_triple_passes(tmp_path):
    from app.config import AdoWikiSource
    from app.kb.ingest.ado_wiki import _check_sentinel

    src = AdoWikiSource(
        label="platform",
        org="https://dev.azure.com/myorg",
        project="Platform",
        wiki="Platform.wiki",
    )
    label_dir = tmp_path / "kb" / "ado_wiki" / "platform"
    _check_sentinel(label_dir, src)  # first call writes sentinel
    _check_sentinel(label_dir, src)  # second call must pass (no rebind)


def test_sentinel_rebind_raises(tmp_path):
    from app.config import AdoWikiSource
    from app.kb.ingest.ado_wiki import _check_sentinel, LabelRebindError

    src_v1 = AdoWikiSource(
        label="platform",
        org="https://dev.azure.com/myorg",
        project="Platform",
        wiki="Platform.wiki",
    )
    src_v2 = AdoWikiSource(
        label="platform",  # same label
        org="https://dev.azure.com/myorg",
        project="DataPlatform",  # but pointing at a different project
        wiki="DataPlatform.wiki",
    )
    label_dir = tmp_path / "kb" / "ado_wiki" / "platform"
    _check_sentinel(label_dir, src_v1)

    with pytest.raises(LabelRebindError, match="previously bound"):
        _check_sentinel(label_dir, src_v2)


def test_sentinel_corrupt_raises(tmp_path):
    from app.config import AdoWikiSource
    from app.kb.ingest.ado_wiki import _check_sentinel, LabelRebindError

    src = AdoWikiSource(
        label="platform",
        org="https://dev.azure.com/myorg",
        project="Platform",
        wiki="Platform.wiki",
    )
    label_dir = tmp_path / "kb" / "ado_wiki" / "platform"
    label_dir.mkdir(parents=True)
    (label_dir / "_source_meta.json").write_text("not json", encoding="utf-8")

    with pytest.raises(LabelRebindError, match="unreadable"):
        _check_sentinel(label_dir, src)


# ── strip_front_matter ────────────────────────────────────────────────────────

def test_strip_front_matter_basic():
    text = "---\nsource: test\n---\n# Title\nBody"
    result = strip_front_matter(text)
    assert result.startswith("# Title")
    assert "source" not in result

def test_strip_front_matter_no_front_matter():
    text = "# Title\nBody"
    assert strip_front_matter(text) == text

def test_strip_front_matter_unclosed():
    text = "---\nsource: test\n# Title"
    # No closing ---, return as-is
    assert strip_front_matter(text) == text


# ── ADO wiki link normalization ───────────────────────────────────────────────

def test_normalize_links_basic():
    text = "See [[Installation Guide]] for details."
    result = _normalize_links(text, "/")
    assert "[Installation Guide](installation-guide.md)" in result

def test_normalize_links_with_display():
    text = "Read [[Installation Guide|the guide]] here."
    result = _normalize_links(text, "/")
    assert "[the guide](installation-guide.md)" in result

def test_normalize_links_multiple():
    text = "[[Page A]] and [[Page B]]"
    result = _normalize_links(text, "/")
    assert "[Page A](page-a.md)" in result
    assert "[Page B](page-b.md)" in result

def test_normalize_links_no_links():
    text = "No wiki links here."
    assert _normalize_links(text, "/") == text


# ── ADO wiki page-tree flattening ─────────────────────────────────────────────

def test_flatten_list_of_pages():
    pages = [
        {"path": "/Page A"},
        {"path": "/Page B"},
    ]
    out: list[dict] = []
    _flatten(pages, out)
    assert len(out) == 2

def test_flatten_nested_subpages():
    tree = {
        "path": "/Parent",
        "subPages": [
            {"path": "/Parent/Child", "subPages": []},
        ],
    }
    out: list[dict] = []
    _flatten(tree, out)
    assert any(p["path"] == "/Parent" for p in out)
    assert any(p["path"] == "/Parent/Child" for p in out)

def test_flatten_empty():
    out: list[dict] = []
    _flatten([], out)
    assert out == []


# ── PDF link extraction (from pdf_fetcher) ────────────────────────────────────

def test_extract_links_markdown():
    from app.kb.ingest.pdf_fetcher import _extract_links  # noqa: F811
    text = "- [Azure Arch](https://example.com/azure-arch.pdf)"
    links = _extract_links(text)
    assert len(links) == 1
    assert links[0] == ("Azure Arch", "https://example.com/azure-arch.pdf")

def test_extract_links_plain_url():
    from app.kb.ingest.pdf_fetcher import _extract_links  # noqa: F811
    text = "https://example.com/guide.pdf"
    links = _extract_links(text)
    assert len(links) == 1
    assert links[0][1] == "https://example.com/guide.pdf"
    assert links[0][0] == "guide"  # derived from filename

def test_extract_links_deduplicates():
    from app.kb.ingest.pdf_fetcher import _extract_links  # noqa: F811
    text = (
        "- [Doc A](https://x.com/a.pdf)\n"
        "https://x.com/a.pdf\n"  # duplicate plain URL
        "- [Doc B](https://x.com/b.pdf)\n"
    )
    links = _extract_links(text)
    urls = [lnk[1] for lnk in links]
    assert len(urls) == len(set(urls))  # no duplicates

def test_extract_links_empty():
    from app.kb.ingest.pdf_fetcher import _extract_links  # noqa: F811
    assert _extract_links("no links here") == []

def test_extract_links_non_pdf_excluded():
    from app.kb.ingest.pdf_fetcher import _extract_links  # noqa: F811
    text = "[Doc](https://example.com/doc.docx)"
    assert _extract_links(text) == []
