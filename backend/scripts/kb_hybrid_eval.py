"""
KB hybrid search effectiveness eval.

Targets `search_kb_hybrid` (FTS5 BM25 + sqlite-vec dense embeddings -> RRF fusion,
chunk-level). Compares against the keyword fallback (`KBService.search`) on the
SAME probes so you can see where hybrid actually adds value.

Probes are designed around the gaps the keyword eval flagged:
  - body content retrieval     (keyword can't, hybrid should)
  - synonyms / paraphrase      (keyword can't, vectors should bridge)
  - acronym expansion          (deterministic acronyms.py map)
  - degenerate queries         (must degrade gracefully)

Reports per probe: keyword top-1, hybrid top-1, expected file in top-K (recall),
rank of expected file (MRR ingredient), and embedding-call cost / latency.

Run from backend/:
    python scripts/kb_hybrid_eval.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Make `app.*` importable when launched from anywhere.
BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

# Suppress SQLAlchemy echo (settings.APP_ENV='dev' enables it on the engine).
# We can't safely change APP_ENV here without tripping DEV_AUTH_BYPASS validation,
# so we drop the noisy logger records via a filter instead.
import logging

class _DropAll(logging.Filter):
    def filter(self, record):
        return False

for _name in ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
    _log = logging.getLogger(_name)
    _log.addFilter(_DropAll())
    _log.propagate = False
    _log.setLevel(logging.WARNING)

from app.kb import indexer
from app.kb.service import get_kb_service
from app.kb.embedder import embed_query
from app.kb.vector_store import chunk_count, hybrid_search
from app.kb.acronyms import expand_query
from app.db.engine import get_engine
from app.db.sqlite_vec_loader import hybrid_disabled, disabled_reason


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    name: str
    query: str
    expected_paths: list[str]      # any of these in top-K counts as recall hit
    category: str                  # body / paraphrase / acronym / title / edge / out-of-kb
    purpose: str


PROBES: list[Probe] = [
    # ---------- TITLE / DIRECT (sanity: both should win) ----------
    Probe(
        name="title:circuit-breaker",
        query="circuit breaker pattern",
        expected_paths=["kb/patterns/circuit-breaker.md"],
        category="title",
        purpose="Direct title match; both keyword and hybrid should top-rank it.",
    ),
    Probe(
        name="title:incident-response",
        query="incident response runbook",
        expected_paths=["kb/runbooks/incident-response.md"],
        category="title",
        purpose="Direct title match for the runbook.",
    ),

    # ---------- BODY-ONLY (keyword's biggest gap) ----------
    Probe(
        name="body:polly-library",
        query="Polly resilience library",
        expected_paths=["kb/patterns/circuit-breaker.md"],
        category="body",
        purpose="'Polly' appears only in the body of circuit-breaker.md. Keyword cannot find this; hybrid should.",
    ),
    Probe(
        name="body:tenacity-python",
        query="tenacity decorator python retry",
        expected_paths=["kb/patterns/circuit-breaker.md"],
        category="body",
        purpose="'tenacity' is in the body code example only.",
    ),
    Probe(
        name="body:pagerduty",
        query="PagerDuty alert acknowledgement",
        expected_paths=["kb/runbooks/incident-response.md"],
        category="body",
        purpose="'PagerDuty' is only in the body of the runbook.",
    ),
    Probe(
        name="body:sev1-definition",
        query="SEV1 response time for outages",
        expected_paths=["kb/runbooks/incident-response.md"],
        category="body",
        purpose="SEV1/SEV2 are only in the body table, not the metadata.",
    ),

    # ---------- PARAPHRASE / SYNONYM (vectors should bridge) ----------
    Probe(
        name="paraphrase:cascading-failures",
        query="how do I stop one failing service from taking down others",
        expected_paths=["kb/patterns/circuit-breaker.md"],
        category="paraphrase",
        purpose="No literal term overlap with title/tags/summary. Vector embedding is the only path.",
    ),
    Probe(
        name="paraphrase:production-outage",
        query="our production app just went down what do we do",
        expected_paths=["kb/runbooks/incident-response.md"],
        category="paraphrase",
        purpose="Conversational paraphrase of the runbook's content.",
    ),
    Probe(
        name="paraphrase:retry-logic",
        query="best practice for retrying flaky network requests",
        expected_paths=["kb/patterns/circuit-breaker.md"],
        category="paraphrase",
        purpose="'retry'/'flaky' don't appear in title; concept overlap requires vectors.",
    ),

    # ---------- ACRONYM EXPANSION ----------
    Probe(
        name="acronym:AAD",
        query="AAD identity setup",
        expected_paths=["kb/general/security-basics.md"],
        category="acronym",
        purpose="'AAD' maps to entra/azure active directory in acronyms.py; security-basics.md covers identity.",
    ),
    Probe(
        name="acronym:KV",
        query="KV secrets",
        expected_paths=["kb/general/security-basics.md"],
        category="acronym",
        purpose="'KV' expands to key vault.",
    ),
    Probe(
        name="acronym:NSG",
        query="NSG rules",
        expected_paths=["kb/general/networking-basics.md", "kb/general/security-basics.md"],
        category="acronym",
        purpose="'NSG' -> network security group; networking-basics is the likely target.",
    ),

    # ---------- OUT-OF-KB ----------
    Probe(
        name="ood:nonsense",
        query="quantum blockchain raccoon parade",
        expected_paths=[],
        category="out-of-kb",
        purpose="No KB content. Hybrid will still return SOMETHING (nearest neighbours always exist) - measure the score and whether it's low.",
    ),

    # ---------- DEGENERATE INPUTS ----------
    Probe(
        name="edge:single-stopword",
        query="the",
        expected_paths=[],
        category="edge",
        purpose="Single common word - hybrid will embed it and return something; document what.",
    ),
]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

@dataclass
class HybridResult:
    paths: list[str]      # unique file paths from chunks, in top-K order (de-duplicated)
    top_score: float
    raw_hits: list        # original SearchHit list
    embed_ms: float
    search_ms: float


@dataclass
class KeywordResult:
    paths: list[str]
    top_score: int
    elapsed_ms: float


def run_keyword(query: str, limit: int = 5) -> KeywordResult:
    kb = get_kb_service()
    start = time.perf_counter()
    results = kb.search(query, limit=limit)
    elapsed_ms = (time.perf_counter() - start) * 1000
    paths = [r.path for r in results]
    # Use the same scoring we have in service.py - just for display
    top = _kw_score(query, results[0]) if results else 0
    return KeywordResult(paths=paths, top_score=top, elapsed_ms=elapsed_ms)


def _kw_score(query, entry):
    tokens = [t for t in query.lower().split() if len(t) > 1]
    title_l, tags_l, summary_l = entry.title.lower(), " ".join(entry.tags).lower(), entry.summary.lower()
    score = 0
    for tok in tokens:
        if tok in title_l: score += 3
        if tok in tags_l: score += 2
        if tok in summary_l: score += 1
    return score


def run_hybrid(query: str, limit: int = 5) -> HybridResult:
    engine = get_engine()
    with engine.connect() as conn:
        start_e = time.perf_counter()
        qvec = embed_query(query)
        embed_ms = (time.perf_counter() - start_e) * 1000

        start_s = time.perf_counter()
        hits = hybrid_search(conn, query, qvec, limit=limit * 3)   # request extra so we can de-dupe at file level
        search_ms = (time.perf_counter() - start_s) * 1000

    # De-duplicate to file paths preserving order
    seen, paths = set(), []
    for h in hits:
        if h.kb_path not in seen:
            seen.add(h.kb_path)
            paths.append(h.kb_path)
        if len(paths) >= limit:
            break

    top_score = hits[0].score if hits else 0.0
    return HybridResult(paths=paths, top_score=top_score, raw_hits=hits, embed_ms=embed_ms, search_ms=search_ms)


def rank_of(target_paths: list[str], actual_paths: list[str]) -> int | None:
    """1-based rank of the first target appearance in actual; None if absent."""
    for i, p in enumerate(actual_paths, start=1):
        if p in target_paths:
            return i
    return None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _trim(s: str, n: int = 30) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def print_probe(probe: Probe, kw: KeywordResult, hy: HybridResult) -> dict:
    kw_rank = rank_of(probe.expected_paths, kw.paths) if probe.expected_paths else None
    hy_rank = rank_of(probe.expected_paths, hy.paths) if probe.expected_paths else None
    kw_hit = kw_rank is not None
    hy_hit = hy_rank is not None

    # For empty expected (out-of-KB / edge), neither "hit" is meaningful.
    print(f"\n[{probe.category}] {probe.name}")
    print(f"  query:    {probe.query!r}")
    print(f"  expected: {probe.expected_paths or '(no expected; informational)'}")
    print(f"  keyword:  top={_trim(kw.paths[0]) if kw.paths else '(empty)'}  rank={kw_rank}  score={kw.top_score}  ({kw.elapsed_ms:.2f}ms)")
    print(f"  hybrid:   top={_trim(hy.paths[0]) if hy.paths else '(empty)'}  rank={hy_rank}  score={hy.top_score:.4f}  ({hy.embed_ms:.0f}ms embed + {hy.search_ms:.2f}ms search)")

    if hy.raw_hits:
        h0 = hy.raw_hits[0]
        snippet = h0.snippet.replace("\n", " ").strip()[:140]
        print(f"  hybrid-top-chunk: heading={h0.heading!r} snippet=\"{snippet}...\"")

    return {
        "name": probe.name,
        "category": probe.category,
        "kw_hit": kw_hit,
        "kw_rank": kw_rank,
        "hy_hit": hy_hit,
        "hy_rank": hy_rank,
        "expected_blank": not probe.expected_paths,
        "embed_ms": hy.embed_ms,
        "search_ms": hy.search_ms,
        "kw_ms": kw.elapsed_ms,
    }


def summarise(rows: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    print(f"\n{'category':<14} {'n':<4} {'kw recall':<12} {'hy recall':<12} {'kw MRR':<10} {'hy MRR':<10}")
    print("-" * 78)
    for cat in ["title", "body", "paraphrase", "acronym", "out-of-kb", "edge"]:
        items = [r for r in by_cat.get(cat, []) if not r["expected_blank"]]
        if not items:
            n = len(by_cat.get(cat, []))
            print(f"{cat:<14} {n:<4} {'-':<12} {'-':<12} {'-':<10} {'-':<10}  (no labelled targets)")
            continue
        n = len(items)
        kw_recall = sum(1 for r in items if r["kw_hit"]) / n
        hy_recall = sum(1 for r in items if r["hy_hit"]) / n
        kw_mrr = sum(1 / r["kw_rank"] if r["kw_rank"] else 0 for r in items) / n
        hy_mrr = sum(1 / r["hy_rank"] if r["hy_rank"] else 0 for r in items) / n
        print(f"{cat:<14} {n:<4} {kw_recall:<12.2%} {hy_recall:<12.2%} {kw_mrr:<10.3f} {hy_mrr:<10.3f}")

    # Overall
    labelled = [r for r in rows if not r["expected_blank"]]
    n = len(labelled)
    kw_recall = sum(1 for r in labelled if r["kw_hit"]) / n
    hy_recall = sum(1 for r in labelled if r["hy_hit"]) / n
    kw_mrr = sum(1 / r["kw_rank"] if r["kw_rank"] else 0 for r in labelled) / n
    hy_mrr = sum(1 / r["hy_rank"] if r["hy_rank"] else 0 for r in labelled) / n
    print("-" * 78)
    print(f"{'OVERALL':<14} {n:<4} {kw_recall:<12.2%} {hy_recall:<12.2%} {kw_mrr:<10.3f} {hy_mrr:<10.3f}")

    avg_embed = sum(r["embed_ms"] for r in rows) / len(rows)
    avg_search = sum(r["search_ms"] for r in rows) / len(rows)
    avg_kw = sum(r["kw_ms"] for r in rows) / len(rows)
    print(f"\nlatency averages: keyword={avg_kw:.2f}ms  hybrid={avg_embed:.0f}ms embed + {avg_search:.2f}ms search")
    print(f"embedding calls made: {len(rows)} (one per query)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if hybrid_disabled():
        print(f"FATAL: hybrid disabled - {disabled_reason()}")
        return 2

    engine = get_engine()
    with engine.connect() as conn:
        total = chunk_count(conn)
    print(f"Index state: {total} chunks indexed")
    if total == 0:
        print("FATAL: hybrid index is empty. Run reindex first.")
        return 2

    # The keyword baseline uses a module-level _index that must be populated
    # by load_index() before kb.search() can return anything. Without this the
    # keyword path is silently broken (0% recall on everything).
    kw_entries = indexer.load_index()
    print(f"Keyword baseline index: {len(kw_entries)} entries loaded from kb_index.json + disk scan")

    # Show what acronym expansion does to one representative query
    print(f"\nAcronym expansion sanity:")
    for q in ["AAD identity setup", "circuit breaker pattern", "AKS networking"]:
        print(f"  expand_query({q!r}) = {expand_query(q)}")

    print("\n" + "=" * 78)
    print("PER-PROBE RESULTS")
    print("=" * 78)

    rows = []
    for probe in PROBES:
        kw = run_keyword(probe.query, limit=5)
        hy = run_hybrid(probe.query, limit=5)
        rows.append(print_probe(probe, kw, hy))

    summarise(rows)

    # Where keyword and hybrid disagree on the labelled hits
    print("\n" + "=" * 78)
    print("KEYWORD vs HYBRID DELTA (labelled probes only)")
    print("=" * 78)
    for r in rows:
        if r["expected_blank"]:
            continue
        if r["kw_hit"] != r["hy_hit"]:
            verdict = "hybrid wins" if r["hy_hit"] else "keyword wins"
            print(f"  [{r['category']}] {r['name']}: {verdict} "
                  f"(kw_rank={r['kw_rank']}, hy_rank={r['hy_rank']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
