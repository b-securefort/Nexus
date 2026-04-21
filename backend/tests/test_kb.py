"""Tests for KB service — path traversal guard and search."""

import pytest
from pathlib import Path
from app.kb.service import KBService
from app.kb.indexer import KBEntry, _extract_first_h1


class TestPathTraversal:
    """Test read_file rejects path traversal attempts."""

    def test_reject_dotdot(self):
        kb = KBService()
        with pytest.raises(PermissionError):
            kb.read_file("../etc/passwd")

    def test_reject_dotdot_in_middle(self):
        kb = KBService()
        with pytest.raises(PermissionError):
            kb.read_file("kb/adrs/../../etc/passwd")

    def test_reject_absolute_path(self):
        kb = KBService()
        with pytest.raises(PermissionError):
            kb.read_file("/etc/passwd")

    def test_reject_backslash_absolute(self):
        kb = KBService()
        with pytest.raises(PermissionError):
            kb.read_file("\\etc\\passwd")

    def test_valid_path_reads_file(self):
        kb = KBService()
        content = kb.read_file("kb/adrs/adr-001-multi-region.md")
        assert "Multi-Region" in content

    def test_nonexistent_file(self):
        kb = KBService()
        with pytest.raises(FileNotFoundError):
            kb.read_file("kb/nonexistent.md")


class TestKBSearch:
    """Test search functionality."""

    def test_search_finds_matching_entries(self):
        from app.kb.indexer import load_index
        load_index()
        kb = KBService()
        results = kb.search("multi-region")
        assert len(results) >= 1
        assert any("multi-region" in r.path for r in results)

    def test_search_case_insensitive(self):
        from app.kb.indexer import load_index
        load_index()
        kb = KBService()
        results = kb.search("CIRCUIT BREAKER")
        assert len(results) >= 1

    def test_search_limit(self):
        from app.kb.indexer import load_index
        load_index()
        kb = KBService()
        results = kb.search("", limit=2)  # matches everything
        assert len(results) <= 2


class TestIndexer:
    """Test KB indexer helpers."""

    def test_extract_first_h1(self, tmp_path):
        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello World\nSome content\n## Sub heading")
        assert _extract_first_h1(md_file) == "Hello World"

    def test_extract_first_h1_no_heading(self, tmp_path):
        md_file = tmp_path / "test.md"
        md_file.write_text("No heading here\nJust text")
        assert _extract_first_h1(md_file) == ""
