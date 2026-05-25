"""
KB vector store — CRUD and hybrid search over kb_chunks, kb_chunks_fts, kb_chunks_vec.

All functions take a raw SQLAlchemy connection so callers control transactions.
Hybrid search pipeline: FTS5 BM25 top-50 + vec0 cosine top-50 → RRF → top-N.
"""

from __future__ import annotations

import hashlib
import logging
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import text

from app.kb.acronyms import expand_query
from app.config import get_settings

logger = logging.getLogger(__name__)

# Characters that break FTS5 query parsing
_FTS_STRIP_RE = re.compile(r'[^\w\s]', re.UNICODE)


# ── FTS helpers ──────────────────────────────────────────────────────────────

def _build_fts_query(terms: list[str]) -> str:
    """Build a safe FTS5 OR query from a list of expanded terms.

    Single-word terms are bare tokens; multi-word terms are phrase-quoted.
    """
    parts: list[str] = []
    for term in terms:
        clean = _FTS_STRIP_RE.sub(' ', term).strip()
        clean = ' '.join(clean.split())   # normalise whitespace
        if not clean:
            continue
        if ' ' in clean:
            parts.append(f'"{clean}"')
        else:
            parts.append(clean)
    return ' OR '.join(parts) if parts else '""'


# ── RRF ─────────────────────────────────────────────────────────────────────

def _rrf(
    bm25: list[int],                 # rowids in BM25 rank order (best first)
    dense: list[tuple[int, float]],  # (rowid, vec_distance) in distance order (closest first)
    k: int,
) -> tuple[
    list[tuple[int, float]],   # (rowid, rrf_score) sorted best-first
    dict[int, int],            # rowid -> sources_hit (1 if one list, 2 if both)
    dict[int, float],          # rowid -> vec_distance (only for rowids in dense)
]:
    """Reciprocal Rank Fusion. Also returns per-rowid sources_hit count and vec distance.

    Tracking sources_hit lets the caller distinguish "found by both BM25 and
    vectors" (strong signal) from "single-source-only" (weaker). vec_distance
    lets the caller spot the OOD-query failure mode: vec0 always returns a
    nearest neighbour, but for an out-of-KB query that neighbour has a large
    cosine distance — a usable confidence discriminator.
    """
    scores: dict[int, float] = {}
    sources: dict[int, int] = {}
    distances: dict[int, float] = {}
    for rank, rowid in enumerate(bm25):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (rank + k)
        sources[rowid] = sources.get(rowid, 0) + 1
    for rank, (rowid, dist) in enumerate(dense):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (rank + k)
        sources[rowid] = sources.get(rowid, 0) + 1
        distances[rowid] = dist
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked, sources, distances


# ── vec0 serialisation ───────────────────────────────────────────────────────

def _serialise_vec(arr: np.ndarray) -> bytes:
    """Pack a float32 numpy array into the bytes format sqlite-vec expects."""
    return struct.pack(f"{len(arr)}f", *arr.astype(np.float32).tolist())


# ── CRUD ────────────────────────────────────────────────────────────────────

def get_stored_state(conn, kb_path: str) -> tuple[str, str] | None:
    """Return (content_hash, embed_model) for the most recent chunk of kb_path, or None."""
    row = conn.execute(
        text("SELECT content_hash, embed_model FROM kb_chunks WHERE kb_path = :p LIMIT 1"),
        {"p": kb_path},
    ).fetchone()
    return (row[0], row[1]) if row else None


def all_indexed_paths(conn) -> set[str]:
    """All distinct kb_paths currently stored in kb_chunks."""
    rows = conn.execute(text("SELECT DISTINCT kb_path FROM kb_chunks")).fetchall()
    return {r[0] for r in rows}


def chunk_count(conn) -> int:
    """Total number of chunks in the index."""
    return conn.execute(text("SELECT COUNT(*) FROM kb_chunks")).scalar() or 0


def delete_chunks_for_path(conn, kb_path: str) -> None:
    """Delete all chunks and their vec0 embeddings for a given kb_path."""
    rows = conn.execute(
        text("SELECT id FROM kb_chunks WHERE kb_path = :p"), {"p": kb_path}
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        return
    placeholders = ",".join(str(i) for i in ids)
    conn.execute(text(f"DELETE FROM kb_chunks_vec WHERE rowid IN ({placeholders})"))
    # FTS5 triggers fire on DELETE so kb_chunks_fts is cleaned up automatically
    conn.execute(text("DELETE FROM kb_chunks WHERE kb_path = :p"), {"p": kb_path})


def upsert_file_chunks(
    conn,
    kb_path: str,
    chunks,                        # list[app.kb.chunker.Chunk]
    embeddings: list[np.ndarray],
    content_hash: str,
    file_mtime: float,
    embed_model: str,
) -> None:
    """Replace all chunks for kb_path with the new set."""
    delete_chunks_for_path(conn, kb_path)
    now = datetime.now(timezone.utc).isoformat()
    for chunk, vec in zip(chunks, embeddings):
        result = conn.execute(
            text(
                """
                INSERT INTO kb_chunks
                  (kb_path, chunk_idx, heading, text, content_hash,
                   file_mtime, source_url, embed_model, created_at)
                VALUES
                  (:kb_path, :chunk_idx, :heading, :text, :content_hash,
                   :file_mtime, :source_url, :embed_model, :created_at)
                """
            ),
            {
                "kb_path": chunk.kb_path,
                "chunk_idx": chunk.chunk_idx,
                "heading": chunk.heading,
                "text": chunk.text,
                "content_hash": content_hash,
                "file_mtime": file_mtime,
                "source_url": chunk.source_url,
                "embed_model": embed_model,
                "created_at": now,
            },
        )
        row_id = result.lastrowid
        conn.execute(
            text("INSERT INTO kb_chunks_vec (rowid, embedding) VALUES (:rid, :emb)"),
            {"rid": row_id, "emb": _serialise_vec(vec)},
        )


# ── Hybrid search ────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    kb_path: str
    chunk_idx: int
    heading: str
    snippet: str          # first 400 chars of chunk text
    source_url: str | None
    score: float          # RRF score
    sources_hit: int      # 1 if found by one list, 2 if found by both BM25 and vec
    vec_distance: float | None  # cosine distance from vec0, None if not in vec top-K
    confidence: str       # "high" | "medium" | "low" — see _confidence_label()


# Cosine-distance ceiling above which a vec-only hit is treated as the
# nearest-neighbour of an unrelated query rather than a real semantic match.
# text-embedding-3-small returns distance in roughly [0, 2]; on the test KB,
# legitimate single-source vec hits cluster at 0.68-1.15 and out-of-KB
# queries cluster at 1.15+. The threshold is corpus-calibrated and may need
# adjustment when a substantially different KB is indexed; see
# IdeasTodo/kb-hybrid-search-improvements.md item #8.
_VEC_DISTANCE_OOD = 1.15


def _confidence_label(score: float, sources_hit: int, vec_distance: float | None) -> str:
    """Derive a human-readable confidence tier.

    high   = found by both BM25 and vec lists — strong dual-source signal.
    medium = single-source hit with no OOD warning (BM25-only literal match,
             or vec-only at a plausible semantic distance).
    low    = vec-only AND cosine distance above the OOD threshold; this is
             the "vec0 always returns a nearest neighbour" failure mode.
    """
    if sources_hit >= 2:
        return "high"
    # Single-source from here.
    if vec_distance is None:
        return "medium"   # BM25 literal-term match; reliable.
    if vec_distance > _VEC_DISTANCE_OOD:
        return "low"
    return "medium"


def hybrid_search(
    conn,
    query: str,
    query_vec: np.ndarray,
    limit: int | None = None,
) -> list[SearchHit]:
    """BM25 + dense vector search fused via RRF. Returns up to KB_RESULT_LIMIT hits."""
    settings = get_settings()
    top_k = limit or settings.KB_RESULT_LIMIT
    bm25_k = settings.KB_BM25_TOP_K
    vec_k = settings.KB_VEC_TOP_K
    rrf_k = settings.KB_RRF_K

    # ── BM25 via FTS5 ────────────────────────────────────────────────────────
    expanded = expand_query(query)
    fts_query = _build_fts_query(expanded)
    bm25_rows: list[int] = []
    try:
        rows = conn.execute(
            text(
                "SELECT rowid FROM kb_chunks_fts "
                "WHERE kb_chunks_fts MATCH :q ORDER BY rank LIMIT :k"
            ),
            {"q": fts_query, "k": bm25_k},
        ).fetchall()
        bm25_rows = [r[0] for r in rows]
    except Exception as e:
        logger.warning("FTS5 query failed for %r: %s", fts_query, e)

    # ── Dense via vec0 ───────────────────────────────────────────────────────
    vec_rows: list[tuple[int, float]] = []
    try:
        rows = conn.execute(
            text(
                "SELECT rowid, distance FROM kb_chunks_vec "
                "WHERE embedding MATCH :v ORDER BY distance LIMIT :k"
            ),
            {"v": _serialise_vec(query_vec), "k": vec_k},
        ).fetchall()
        vec_rows = [(r[0], float(r[1])) for r in rows]
    except Exception as e:
        logger.warning("vec0 search failed: %s", e)

    if not bm25_rows and not vec_rows:
        return []

    # ── RRF fusion ───────────────────────────────────────────────────────────
    fused, sources_by_rid, distance_by_rid = _rrf(bm25_rows, vec_rows, rrf_k)
    fused = fused[:top_k]
    if not fused:
        return []

    # ── Hydrate from kb_chunks ───────────────────────────────────────────────
    rowid_to_score = dict(fused)
    placeholders = ",".join(str(rid) for rid, _ in fused)
    rows = conn.execute(
        text(
            f"SELECT id, kb_path, chunk_idx, heading, text, source_url "
            f"FROM kb_chunks WHERE id IN ({placeholders})"
        )
    ).fetchall()

    hits: list[SearchHit] = []
    for row in rows:
        rid, kb_path, chunk_idx, heading, chunk_text, source_url = row
        score = rowid_to_score.get(rid, 0.0)
        sources_hit = sources_by_rid.get(rid, 1)
        vec_distance = distance_by_rid.get(rid)
        hits.append(SearchHit(
            kb_path=kb_path,
            chunk_idx=chunk_idx,
            heading=heading,
            snippet=chunk_text[:400],
            source_url=source_url,
            score=score,
            sources_hit=sources_hit,
            vec_distance=vec_distance,
            confidence=_confidence_label(score, sources_hit, vec_distance),
        ))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
