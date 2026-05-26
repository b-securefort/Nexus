"""
Integration tests for the KB hybrid retrieval pipeline.
Mocks the Azure OpenAI embedder so no network calls are made.
"""

import hashlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import create_engine, text

from app.kb.chunker import chunk_markdown
from app.kb.vector_store import chunk_count, hybrid_search, all_indexed_paths


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rand_vec(dims: int = 1536) -> np.ndarray:
    v = np.random.randn(dims).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _mock_embed(texts):
    """Return deterministic unit vectors — one per text."""
    return [_rand_vec() for _ in texts]


FIXTURE_DOCS = {
    "kb/networking/vnet.md": """\
# Azure Virtual Network

## Overview

Azure VNet allows you to securely connect Azure resources.

## Peering

VNet peering connects two virtual networks seamlessly.

## NSG

Network Security Groups control inbound and outbound traffic.
""",
    "kb/compute/aks.md": """\
# Azure Kubernetes Service

## Cluster Setup

AKS simplifies deploying a managed Kubernetes cluster.

## Networking

AKS supports CNI and kubenet network plugins.
""",
}


def _build_test_engine():
    """Return an in-memory engine with kb schema (FTS only, no vec0)."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE kb_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_path TEXT NOT NULL,
                chunk_idx INTEGER NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_mtime REAL NOT NULL,
                source_url TEXT,
                source_instance TEXT,
                embed_model TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE VIRTUAL TABLE kb_chunks_fts USING fts5(
                text, heading,
                content='kb_chunks', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
        """))
        conn.execute(text("""
            CREATE TRIGGER kb_chunks_ai AFTER INSERT ON kb_chunks BEGIN
                INSERT INTO kb_chunks_fts(rowid, text, heading)
                VALUES (new.id, new.text, new.heading);
            END
        """))
        conn.execute(text("""
            CREATE TRIGGER kb_chunks_ad AFTER DELETE ON kb_chunks BEGIN
                INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, text, heading)
                VALUES('delete', old.id, old.text, old.heading);
            END
        """))
        # Stub vec table
        conn.execute(text(
            "CREATE TABLE kb_chunks_vec (rowid INTEGER PRIMARY KEY, embedding BLOB)"
        ))
        conn.commit()
    return engine


# ── Reindex pipeline ─────────────────────────────────────────────────────────

class TestReindexPipeline:
    """Test the chunk → embed → upsert pipeline with mocked embedder."""

    def test_chunks_inserted_for_all_docs(self, tmp_path):
        kb_dir = tmp_path / "kb"
        for rel, content in FIXTURE_DOCS.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        from app.kb.vector_store import upsert_file_chunks
        engine = _build_test_engine()

        with engine.connect() as conn:
            for rel, content in FIXTURE_DOCS.items():
                chunks = chunk_markdown(rel, content)
                vecs = _mock_embed([c.text for c in chunks])
                upsert_file_chunks(
                    conn, rel, chunks, vecs,
                    hashlib.sha256(content.encode()).hexdigest(),
                    1.0, "text-embedding-3-small:1536",
                )
            conn.commit()
            assert chunk_count(conn) > 0

    def test_all_paths_indexed(self, tmp_path):
        from app.kb.vector_store import upsert_file_chunks
        engine = _build_test_engine()

        with engine.connect() as conn:
            for rel, content in FIXTURE_DOCS.items():
                chunks = chunk_markdown(rel, content)
                vecs = _mock_embed([c.text for c in chunks])
                upsert_file_chunks(
                    conn, rel, chunks, vecs,
                    hashlib.sha256(content.encode()).hexdigest(),
                    1.0, "text-embedding-3-small:1536",
                )
            conn.commit()
            paths = all_indexed_paths(conn)

        assert "kb/networking/vnet.md" in paths
        assert "kb/compute/aks.md" in paths

    def test_heading_breadcrumbs_stored(self):
        from app.kb.vector_store import upsert_file_chunks
        engine = _build_test_engine()
        content = FIXTURE_DOCS["kb/networking/vnet.md"]
        chunks = chunk_markdown("kb/networking/vnet.md", content)

        with engine.connect() as conn:
            vecs = _mock_embed([c.text for c in chunks])
            upsert_file_chunks(
                conn, "kb/networking/vnet.md", chunks, vecs,
                "hash", 1.0, "model",
            )
            conn.commit()
            rows = conn.execute(
                text("SELECT heading FROM kb_chunks WHERE kb_path = 'kb/networking/vnet.md'")
            ).fetchall()

        headings = [r[0] for r in rows]
        assert any("Azure Virtual Network" in h for h in headings)
        assert any("Peering" in h for h in headings)

    def test_upsert_replaces_on_rehash(self):
        from app.kb.vector_store import upsert_file_chunks, get_stored_state
        engine = _build_test_engine()
        content_v1 = "# Doc\n\n## Old\n\nOld content.\n"
        content_v2 = "# Doc\n\n## New\n\nNew content.\n"

        with engine.connect() as conn:
            chunks_v1 = chunk_markdown("kb/doc.md", content_v1)
            upsert_file_chunks(
                conn, "kb/doc.md", chunks_v1,
                _mock_embed([c.text for c in chunks_v1]),
                hashlib.sha256(content_v1.encode()).hexdigest(), 1.0, "model",
            )
            conn.commit()

            chunks_v2 = chunk_markdown("kb/doc.md", content_v2)
            upsert_file_chunks(
                conn, "kb/doc.md", chunks_v2,
                _mock_embed([c.text for c in chunks_v2]),
                hashlib.sha256(content_v2.encode()).hexdigest(), 2.0, "model",
            )
            conn.commit()

            state = get_stored_state(conn, "kb/doc.md")
            rows = conn.execute(
                text("SELECT text FROM kb_chunks WHERE kb_path = 'kb/doc.md'")
            ).fetchall()

        assert state[0] == hashlib.sha256(content_v2.encode()).hexdigest()
        assert all("New" in r[0] or "Doc" in r[0] for r in rows)
        assert not any("Old content" in r[0] for r in rows)


# ── FTS search ────────────────────────────────────────────────────────────────

class TestHybridSearchFTSOnly:
    """Smoke-tests for the BM25 path (vec0 stubbed to empty)."""

    def _setup(self):
        from app.kb.vector_store import upsert_file_chunks
        engine = _build_test_engine()
        with engine.connect() as conn:
            for rel, content in FIXTURE_DOCS.items():
                chunks = chunk_markdown(rel, content)
                vecs = _mock_embed([c.text for c in chunks])
                upsert_file_chunks(conn, rel, chunks, vecs, "h", 1.0, "m")
            conn.commit()
        return engine

    def test_keyword_search_finds_vnet_content(self):
        engine = self._setup()
        with engine.connect() as conn:
            results = hybrid_search(conn, "VNet peering", np.zeros(1536, dtype=np.float32))
        assert results, "Expected at least one hit"
        assert any("vnet" in h.kb_path for h in results)

    def test_keyword_search_finds_aks_content(self):
        engine = self._setup()
        with engine.connect() as conn:
            results = hybrid_search(conn, "Kubernetes cluster AKS", np.zeros(1536, dtype=np.float32))
        assert results
        assert any("aks" in h.kb_path for h in results)

    def test_snippet_is_truncated_to_400_chars(self):
        from app.kb.vector_store import upsert_file_chunks
        engine = _build_test_engine()
        long_text = "word " * 200   # 1000 chars
        from app.kb.chunker import Chunk
        chunk = Chunk(kb_path="kb/long.md", chunk_idx=0, heading="Doc", text=long_text)
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/long.md", [chunk], [_rand_vec()], "h", 1.0, "m")
            conn.commit()
            results = hybrid_search(conn, "word", np.zeros(1536, dtype=np.float32))
        assert results
        assert len(results[0].snippet) <= 400

    def test_no_results_for_unrelated_query(self):
        engine = self._setup()
        with engine.connect() as conn:
            results = hybrid_search(conn, "xyzzy gobbledygook", np.zeros(1536, dtype=np.float32))
        assert results == []


# ── Reindex lock ─────────────────────────────────────────────────────────────

class TestReindexLock:
    def test_concurrent_reindex_skips_second_call(self, monkeypatch):
        """While reindex_all holds the lock, a second call skips immediately."""
        import app.kb.reindex as ri

        calls = []
        t1_running = threading.Event()   # set when t1 is inside _reindex
        t1_release = threading.Event()   # set to let t1 finish

        def slow_reindex(force=False):
            calls.append("start")
            t1_running.set()        # signal test that lock is held
            t1_release.wait(timeout=3)  # hold the lock until told to release
            calls.append("end")

        monkeypatch.setattr(ri, "_reindex", slow_reindex)

        t1 = threading.Thread(target=ri.reindex_all)
        t1.start()

        # Wait until t1 is inside _reindex (and holding the lock)
        assert t1_running.wait(timeout=3), "t1 never started _reindex"

        # Now try the second call — it must skip because the lock is held
        ri.reindex_all()
        assert calls.count("start") == 1, "Second call should not have entered _reindex"

        # Let t1 finish
        t1_release.set()
        t1.join(timeout=3)

        assert calls == ["start", "end"]
