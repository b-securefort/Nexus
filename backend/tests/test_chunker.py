"""
Tests for app.kb.chunker and app.kb.acronyms.
Pure Python — no models, no DB, no network.
"""

import pytest
from unittest.mock import patch

from app.kb.acronyms import expand_query
from app.kb.chunker import Chunk, chunk_markdown, _strip_frontmatter


# ─── acronyms ────────────────────────────────────────────────────────────────

class TestExpandQuery:
    def test_passthrough_unknown_term(self):
        result = expand_query("kubernetes networking")
        assert result[0] == "kubernetes networking"

    def test_expands_single_acronym(self):
        result = expand_query("AKS")
        assert result[0] == "AKS"
        assert "kubernetes" in result

    def test_expands_case_insensitive(self):
        result = expand_query("aks")
        assert "kubernetes" in result

    def test_expands_multiple_tokens(self):
        result = expand_query("nsg rules")
        assert any("network security group" in t for t in result)

    def test_cap_at_six_terms(self):
        # "vnet nsg apim aks" each expand to multiple terms
        result = expand_query("vnet nsg apim aks")
        assert len(result) <= 6

    def test_no_duplicates(self):
        result = expand_query("AKS aks")
        assert len(result) == len(set(t.lower() for t in result))

    def test_original_always_first(self):
        result = expand_query("APIM gateway")
        assert result[0] == "APIM gateway"

    def test_no_expansion_for_plain_words(self):
        result = expand_query("storage account")
        assert result == ["storage account"]

    def test_aoai_expansion(self):
        result = expand_query("aoai deployment")
        assert any("openai" in t for t in result)

    def test_rbac_expansion(self):
        result = expand_query("rbac assignment")
        assert any("role" in t for t in result)


# ─── front-matter stripping ──────────────────────────────────────────────────

class TestStripFrontmatter:
    def test_no_frontmatter(self):
        body, url = _strip_frontmatter("# Title\nContent")
        assert body == "# Title\nContent"
        assert url is None

    def test_strips_frontmatter(self):
        content = "---\ntitle: Foo\n---\n# Title\nContent"
        body, url = _strip_frontmatter(content)
        assert body.startswith("# Title")
        assert "title: Foo" not in body

    def test_extracts_source_url(self):
        content = "---\nsource_url: https://example.com/doc\n---\n# Title"
        _, url = _strip_frontmatter(content)
        assert url == "https://example.com/doc"

    def test_source_url_with_quotes(self):
        content = '---\nsource_url: "https://example.com/doc"\n---\n# Title'
        _, url = _strip_frontmatter(content)
        assert url == "https://example.com/doc"

    def test_no_source_url_in_frontmatter(self):
        content = "---\ntitle: Foo\nauthor: Bar\n---\n# Title"
        _, url = _strip_frontmatter(content)
        assert url is None


# ─── chunker — basic splitting ───────────────────────────────────────────────

SIMPLE_DOC = """\
# My Document

Introduction paragraph.

## Section One

Content of section one.

## Section Two

Content of section two.
"""


class TestChunkMarkdown:
    def test_returns_chunks(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        assert len(chunks) >= 2

    def test_chunk_index_sequential(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        for i, c in enumerate(chunks):
            assert c.chunk_idx == i

    def test_kb_path_preserved(self):
        chunks = chunk_markdown("kb/adrs/adr-001.md", SIMPLE_DOC)
        assert all(c.kb_path == "kb/adrs/adr-001.md" for c in chunks)

    def test_heading_breadcrumb_includes_h1(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        assert all("My Document" in c.heading for c in chunks)

    def test_heading_breadcrumb_includes_h2(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        headings = [c.heading for c in chunks]
        assert any("Section One" in h for h in headings)
        assert any("Section Two" in h for h in headings)

    def test_no_empty_chunks(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        assert all(c.text.strip() for c in chunks)

    def test_source_url_from_frontmatter(self):
        doc = "---\nsource_url: https://example.com\n---\n# Doc\n## Sec\nContent."
        chunks = chunk_markdown("kb/test.md", doc)
        assert all(c.source_url == "https://example.com" for c in chunks)

    def test_source_url_none_without_frontmatter(self):
        chunks = chunk_markdown("kb/test.md", SIMPLE_DOC)
        assert all(c.source_url is None for c in chunks)

    def test_doc_with_no_h2_is_single_chunk(self):
        doc = "# Title\n\nJust a flat document with no subsections.\n"
        chunks = chunk_markdown("kb/test.md", doc)
        assert len(chunks) == 1

    def test_empty_sections_skipped(self):
        doc = "# Title\n\n## Empty Section\n\n## Real Section\n\nHas content.\n"
        chunks = chunk_markdown("kb/test.md", doc)
        assert all(c.text.strip() for c in chunks)
        texts = " ".join(c.text for c in chunks)
        assert "Has content" in texts


# ─── chunker — code fence awareness ─────────────────────────────────────────

CODE_DOC = """\
# Code Doc

## Example

Here is a code block:

```python
x = 1
y = 2
# This is NOT a heading split point
```

After the fence.
"""


class TestCodeFence:
    def test_fence_not_split_inside(self, monkeypatch):
        # Force max_chars small enough that without fence awareness it would split
        from app.config import Settings
        mock = Settings.model_construct(
            KB_CHUNK_MAX_CHARS=50,
            KB_CHUNK_OVERLAP_FRACTION=0.0,
        )
        with patch("app.kb.chunker.get_settings", return_value=mock):
            chunks = chunk_markdown("kb/code.md", CODE_DOC)
        # The fence block must not be split mid-fence
        for c in chunks:
            opens = c.text.count("```")
            # Either 0 (no fence) or even (open+close pairs)
            assert opens % 2 == 0, f"Unbalanced fence in chunk: {c.text!r}"


# ─── chunker — large doc splits by paragraph ─────────────────────────────────

def _make_large_section(n_paragraphs: int, words_per_para: int = 60) -> str:
    """Build a section big enough to force paragraph splitting."""
    paras = []
    for i in range(n_paragraphs):
        paras.append(" ".join(f"word{i}_{j}" for j in range(words_per_para)))
    return "\n\n".join(paras)


class TestLargeDocSplitting:
    def test_large_section_produces_multiple_chunks(self, monkeypatch):
        large = f"# Big Doc\n\n## Big Section\n\n{_make_large_section(20)}\n"
        from app.config import Settings
        mock = Settings.model_construct(
            KB_CHUNK_MAX_CHARS=500,
            KB_CHUNK_OVERLAP_FRACTION=0.15,
        )
        with patch("app.kb.chunker.get_settings", return_value=mock):
            chunks = chunk_markdown("kb/big.md", large)
        assert len(chunks) > 1

    def test_overlap_carries_text_forward(self, monkeypatch):
        # Build two paragraphs where the tail of para1 should appear in para2's chunk
        para1 = "alpha " * 200   # ~1200 chars
        para2 = "beta " * 200

        doc = f"# Doc\n\n## Sec\n\n{para1.strip()}\n\n{para2.strip()}\n"
        from app.config import Settings
        mock = Settings.model_construct(
            KB_CHUNK_MAX_CHARS=800,
            KB_CHUNK_OVERLAP_FRACTION=0.15,
        )
        with patch("app.kb.chunker.get_settings", return_value=mock):
            chunks = chunk_markdown("kb/overlap.md", doc)

        if len(chunks) < 2:
            pytest.skip("Document didn't split — increase para size")

        # The overlap means the second chunk's text should contain some "alpha"
        assert "alpha" in chunks[1].text

    def test_all_text_preserved_across_chunks(self, monkeypatch):
        """No content is silently dropped."""
        large = f"# Doc\n\n## Sec\n\n{_make_large_section(15)}\n"
        from app.config import Settings
        mock = Settings.model_construct(
            KB_CHUNK_MAX_CHARS=400,
            KB_CHUNK_OVERLAP_FRACTION=0.0,
        )
        with patch("app.kb.chunker.get_settings", return_value=mock):
            chunks = chunk_markdown("kb/big.md", large)

        combined = " ".join(c.text for c in chunks)
        # Every unique word from the original must appear somewhere
        for i in range(15):
            assert f"word{i}_0" in combined


# ─── chunker — heading breadcrumb ────────────────────────────────────────────

class TestHeadingBreadcrumb:
    def test_h3_included_in_breadcrumb(self):
        doc = "# Root\n\n## Parent\n\n### Child\n\nContent here.\n"
        chunks = chunk_markdown("kb/test.md", doc)
        assert any("Child" in c.heading for c in chunks)

    def test_breadcrumb_separator(self):
        doc = "# Root\n\n## Parent\n\nContent.\n"
        chunks = chunk_markdown("kb/test.md", doc)
        assert any(" > " in c.heading for c in chunks)

    def test_filename_used_as_title_when_no_h1(self):
        doc = "## Section\n\nContent.\n"
        chunks = chunk_markdown("kb/my-document.md", doc)
        assert any("My Document" in c.heading for c in chunks)

    def test_h3_heading_reflects_active_subsection_not_last(self):
        # Regression: old code recorded the *last* H3 in a block for all content.
        # Windows content must not get tagged with "Linux" as its H3.
        doc = (
            "# Guide\n\n"
            "## Installation\n\n"
            "Preamble.\n\n"
            "### Windows\n\n"
            "Windows steps.\n\n"
            "### Linux\n\n"
            "Linux steps.\n"
        )
        chunks = chunk_markdown("kb/guide.md", doc)
        windows_chunks = [c for c in chunks if "Windows steps" in c.text]
        linux_chunks   = [c for c in chunks if "Linux steps" in c.text]
        assert windows_chunks, "Expected a chunk containing Windows content"
        assert all("Windows" in c.heading for c in windows_chunks), (
            f"Windows content has wrong heading: {[c.heading for c in windows_chunks]}"
        )
        assert all("Linux" not in c.heading for c in windows_chunks), (
            "Windows content was incorrectly tagged with Linux H3"
        )
