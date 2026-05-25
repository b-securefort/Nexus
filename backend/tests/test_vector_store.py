"""
Unit tests for app.kb.vector_store.
Uses an in-memory SQLite with sqlite-vec loaded where available;
falls back to FTS-only tests when the extension is unavailable.
"""

import struct
import numpy as np
import pytest
from sqlalchemy import create_engine, text

from app.kb.vector_store import (
    _build_fts_query,
    _rrf,
    _serialise_vec,
    all_indexed_paths,
    chunk_count,
    delete_chunks_for_path,
    get_stored_state,
    upsert_file_chunks,
    hybrid_search,
    SearchHit,
)
from app.kb.chunker import Chunk


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_engine(with_vec: bool = False):
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        if with_vec:
            try:
                import sqlite_vec
                conn.connection.enable_load_extension(True)
                sqlite_vec.load(conn.connection)
                conn.connection.enable_load_extension(False)
            except Exception:
                pytest.skip("sqlite-vec extension not available")

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
        conn.execute(text("""
            CREATE TRIGGER kb_chunks_au AFTER UPDATE ON kb_chunks BEGIN
                INSERT INTO kb_chunks_fts(kb_chunks_fts, rowid, text, heading)
                VALUES('delete', old.id, old.text, old.heading);
                INSERT INTO kb_chunks_fts(rowid, text, heading)
                VALUES (new.id, new.text, new.heading);
            END
        """))

        if with_vec:
            conn.execute(text(
                "CREATE VIRTUAL TABLE kb_chunks_vec USING vec0(embedding float[1536])"
            ))
        else:
            # Stub so upsert doesn't fail in non-vec tests
            conn.execute(text("""
                CREATE TABLE kb_chunks_vec (
                    rowid INTEGER PRIMARY KEY,
                    embedding BLOB
                )
            """))

        conn.commit()
    return engine


def _rand_vec(dims: int = 1536) -> np.ndarray:
    v = np.random.randn(dims).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _make_chunk(kb_path: str, idx: int, text: str = "content") -> Chunk:
    return Chunk(
        kb_path=kb_path,
        chunk_idx=idx,
        heading=f"Doc > Section {idx}",
        text=text,
        source_url=None,
    )


# ── FTS query builder ────────────────────────────────────────────────────────

class TestBuildFtsQuery:
    def test_single_word(self):
        assert _build_fts_query(["kubernetes"]) == "kubernetes"

    def test_multi_word_phrase_quoted(self):
        q = _build_fts_query(["azure kubernetes service"])
        assert '"azure kubernetes service"' in q

    def test_multiple_terms_joined_with_or(self):
        q = _build_fts_query(["aks", "kubernetes"])
        assert " OR " in q

    def test_strips_special_chars(self):
        # Special chars stripped; remaining multi-word term gets phrase quotes
        q = _build_fts_query(['key:vault test'])
        assert "key" in q
        assert ":" not in q

    def test_empty_input_returns_empty_query(self):
        q = _build_fts_query([])
        assert q == '""'

    def test_blank_terms_skipped(self):
        q = _build_fts_query(["", "  ", "kubernetes"])
        assert "kubernetes" in q


# ── RRF ─────────────────────────────────────────────────────────────────────

class TestRRF:
    # `_rrf` now returns (ranked_list, sources_by_rid, distance_by_rid).
    # Dense input is list[tuple[rowid, vec_distance]] — distance threaded through
    # so callers can build a confidence signal.

    def test_item_in_both_lists_scores_higher(self):
        bm25 = [1, 2, 3]
        dense = [(2, 0.1), (4, 0.2), (5, 0.3)]
        ranked, sources, _ = _rrf(bm25, dense, k=60)
        result = dict(ranked)
        assert result[2] > result[1]
        assert result[2] > result[4]
        assert sources[2] == 2
        assert sources[1] == 1
        assert sources[4] == 1

    def test_result_sorted_descending(self):
        ranked, _, _ = _rrf([1, 2, 3], [(3, 0.1), (2, 0.2), (1, 0.3)], k=60)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_empty_bm25(self):
        ranked, sources, distances = _rrf([], [(10, 0.1), (20, 0.2), (30, 0.3)], k=60)
        result = dict(ranked)
        assert len(result) == 3
        assert result[10] > result[20] > result[30]
        assert all(s == 1 for s in sources.values())
        assert distances == {10: 0.1, 20: 0.2, 30: 0.3}

    def test_empty_dense(self):
        ranked, sources, distances = _rrf([10, 20], [], k=60)
        result = dict(ranked)
        assert result[10] > result[20]
        assert distances == {}
        assert all(s == 1 for s in sources.values())

    def test_both_empty_returns_empty(self):
        ranked, sources, distances = _rrf([], [], k=60)
        assert ranked == []
        assert sources == {}
        assert distances == {}

    def test_k_parameter_affects_scores(self):
        r_low = dict(_rrf([1, 2], [(1, 0.1), (2, 0.2)], k=1)[0])
        r_high = dict(_rrf([1, 2], [(1, 0.1), (2, 0.2)], k=100)[0])
        # With lower k, top-rank items get disproportionately higher scores
        ratio_low = r_low[1] / r_low[2]
        ratio_high = r_high[1] / r_high[2]
        assert ratio_low > ratio_high


# ── Serialisation ────────────────────────────────────────────────────────────

class TestSerialiseVec:
    def test_returns_bytes(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = _serialise_vec(v)
        assert isinstance(b, bytes)
        assert len(b) == 3 * 4  # 3 float32s × 4 bytes

    def test_roundtrip(self):
        v = np.array([0.1, 0.5, -0.3], dtype=np.float32)
        b = _serialise_vec(v)
        unpacked = struct.unpack("3f", b)
        np.testing.assert_allclose(unpacked, v, rtol=1e-6)


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestCRUD:
    def test_upsert_and_count(self):
        engine = _make_engine()
        chunks = [_make_chunk("kb/a.md", 0, "azure kubernetes service")]
        vecs = [_rand_vec()]
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/a.md", chunks, vecs, "hash1", 1.0, "model:v1")
            conn.commit()
            assert chunk_count(conn) == 1

    def test_get_stored_state_returns_hash_and_model(self):
        engine = _make_engine()
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/a.md",
                               [_make_chunk("kb/a.md", 0)], [_rand_vec()],
                               "abc123", 1.0, "model:v1")
            conn.commit()
            state = get_stored_state(conn, "kb/a.md")
        assert state == ("abc123", "model:v1")

    def test_get_stored_state_returns_none_for_unknown_path(self):
        engine = _make_engine()
        with engine.connect() as conn:
            assert get_stored_state(conn, "kb/missing.md") is None

    def test_all_indexed_paths(self):
        engine = _make_engine()
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/a.md",
                               [_make_chunk("kb/a.md", 0)], [_rand_vec()],
                               "h1", 1.0, "m")
            upsert_file_chunks(conn, "kb/b.md",
                               [_make_chunk("kb/b.md", 0)], [_rand_vec()],
                               "h2", 1.0, "m")
            conn.commit()
            paths = all_indexed_paths(conn)
        assert paths == {"kb/a.md", "kb/b.md"}

    def test_delete_removes_chunks(self):
        engine = _make_engine()
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/a.md",
                               [_make_chunk("kb/a.md", 0)], [_rand_vec()],
                               "h1", 1.0, "m")
            conn.commit()
            delete_chunks_for_path(conn, "kb/a.md")
            conn.commit()
            assert chunk_count(conn) == 0

    def test_upsert_replaces_existing_chunks(self):
        engine = _make_engine()
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/a.md",
                               [_make_chunk("kb/a.md", 0), _make_chunk("kb/a.md", 1)],
                               [_rand_vec(), _rand_vec()], "h1", 1.0, "m")
            conn.commit()
            # Re-upsert with only 1 chunk
            upsert_file_chunks(conn, "kb/a.md",
                               [_make_chunk("kb/a.md", 0)],
                               [_rand_vec()], "h2", 2.0, "m")
            conn.commit()
            assert chunk_count(conn) == 1


# ── FTS search (no vec0 required) ────────────────────────────────────────────

class TestFTSSearch:
    def test_bm25_finds_keyword_match(self, monkeypatch):
        engine = _make_engine()
        chunks = [
            _make_chunk("kb/net.md", 0, "Azure Virtual Network VNet peering configuration"),
            _make_chunk("kb/aks.md", 0, "Kubernetes cluster deployment with AKS"),
        ]
        with engine.connect() as conn:
            upsert_file_chunks(conn, "kb/net.md", [chunks[0]], [_rand_vec()], "h1", 1.0, "m")
            upsert_file_chunks(conn, "kb/aks.md", [chunks[1]], [_rand_vec()], "h2", 1.0, "m")
            conn.commit()

            # Monkeypatch vec0 search to return empty (not available without extension)
            from app.kb import vector_store as vs
            original = vs._serialise_vec
            def mock_vec_search(*a, **kw):
                raise Exception("vec0 not available")

            # Patch conn.execute to fail on vec0 query
            real_execute = conn.execute
            def patched_execute(stmt, *args, **kwargs):
                q = str(stmt.text) if hasattr(stmt, 'text') else str(stmt)
                if "kb_chunks_vec" in q:
                    raise Exception("vec0 not in test")
                return real_execute(stmt, *args, **kwargs)
            monkeypatch.setattr(conn, "execute", patched_execute)

            results = hybrid_search(conn, "VNet peering", np.zeros(1536, dtype=np.float32))

        assert any("net.md" in h.kb_path for h in results)
