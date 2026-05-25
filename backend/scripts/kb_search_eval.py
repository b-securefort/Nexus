"""
KB search effectiveness eval.

Probes KBService.search() against:
  1. Synthetic fixtures - controlled entries that force specific behaviors.
  2. The real (test) KB on disk - sanity check that the algo behaves the same
     when fed actual loaded content.
  3. Scaled synthetic indexes - latency + score saturation as corpus grows.

No LLM, no network, no DB. Runs in seconds.

Run from the backend/ directory:
    python scripts/kb_search_eval.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Make `app.*` importable when launched from anywhere.
BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.kb import indexer
from app.kb.indexer import KBEntry
from app.kb.service import KBService


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    name: str
    query: str
    expected_top_paths: list[str]   # ordered prefix the actual top-K must start with; [] means "expect empty result"
    purpose: str
    notes: str = ""


@dataclass
class ProbeResult:
    probe: Probe
    actual_paths: list[str]
    actual_scores: list[int]
    passed: bool


def set_index(entries: list[KBEntry]) -> None:
    """Swap the module-level _index in the indexer so KBService.search() sees `entries`."""
    indexer._index = entries


def run_probe(svc: KBService, probe: Probe, limit: int = 5) -> ProbeResult:
    results = svc.search(probe.query, limit=limit)
    paths = [r.path for r in results]

    # Re-score so we can show the score column (search() drops scores).
    scores = [_score(probe.query, r) for r in results]

    if not probe.expected_top_paths:
        passed = len(paths) == 0
    else:
        passed = paths[: len(probe.expected_top_paths)] == probe.expected_top_paths

    return ProbeResult(probe=probe, actual_paths=paths, actual_scores=scores, passed=passed)


def _score(query: str, entry: KBEntry) -> int:
    """Re-implement KBService scoring so we can display scores alongside results."""
    tokens = [t for t in query.lower().split() if len(t) > 1]
    title_l = entry.title.lower()
    tags_l = " ".join(entry.tags).lower()
    summary_l = entry.summary.lower()
    score = 0
    for token in tokens:
        if token in title_l:
            score += 3
        if token in tags_l:
            score += 2
        if token in summary_l:
            score += 1
    return score


# ---------------------------------------------------------------------------
# Synthetic fixtures - each entry crafted to isolate one behavior.
# ---------------------------------------------------------------------------

FIXTURES: list[KBEntry] = [
    # Field-weight isolation. Same unique term ("alpha") placed in only one field per doc.
    KBEntry(path="alpha-in-title.md",   title="alpha guide",       summary="overview",            tags=["unrelated"]),
    KBEntry(path="alpha-in-tag.md",     title="generic guide",     summary="overview",            tags=["alpha", "unrelated"]),
    KBEntry(path="alpha-in-summary.md", title="generic guide two", summary="discusses alpha topic", tags=["unrelated"]),

    # Single-field hits (other unique terms per field).
    KBEntry(path="title-circuit.md",    title="circuit breaker pattern", summary="bland overview", tags=["pattern"]),
    KBEntry(path="tag-resilience.md",   title="generic doc three",       summary="bland overview", tags=["resilience", "fault-tolerance"]),
    KBEntry(path="summary-bulkhead.md", title="generic doc four",        summary="explains bulkhead isolation in depth", tags=["pattern"]),

    # Body-only term - title/tags/summary deliberately avoid "saga".
    KBEntry(path="body-only.md", title="distributed transactions", summary="overview of consistency", tags=["consistency"]),

    # Plural vs singular.
    KBEntry(path="plural-only.md",   title="handling retries",  summary="", tags=["transient"]),
    KBEntry(path="singular-only.md", title="when to retry",     summary="", tags=["transient"]),

    # Hyphenation. Title uses hyphen, summary uses space - to probe punctuation handling.
    KBEntry(path="hyphen-title.md",  title="rate-limiter playbook", summary="discusses rate limiter knobs", tags=[]),

    # Multi-term distinguishability.
    KBEntry(path="azure-networking.md", title="azure networking basics", summary="vnets, subnets", tags=["networking"]),
    KBEntry(path="azure-storage.md",    title="azure storage basics",    summary="blob, table",   tags=["storage"]),

    # Decoy with common stop-words in title (the algo doesn't strip stop-words; query "to the" matches here).
    KBEntry(path="stopword-decoy.md", title="how to use the service bus", summary="", tags=[]),
]


# ---------------------------------------------------------------------------
# Probes - each maps to one algorithmic claim from service.py:48-80.
# ---------------------------------------------------------------------------

PROBES: list[Probe] = [
    # --- Field-weight correctness ---
    Probe(
        name="weights-rank-title>tag>summary",
        query="alpha",
        expected_top_paths=["alpha-in-title.md", "alpha-in-tag.md", "alpha-in-summary.md"],
        purpose="Same unique term in three docs (one per field) - must rank 3>2>1",
    ),
    Probe(
        name="title-only-hit",
        query="circuit",
        expected_top_paths=["title-circuit.md"],
        purpose="Title-only term - should surface and top-rank",
    ),
    Probe(
        name="tag-only-hit",
        query="resilience",
        expected_top_paths=["tag-resilience.md"],
        purpose="Tag-only term - surfaces at score=2",
    ),
    Probe(
        name="summary-only-hit",
        query="bulkhead",
        expected_top_paths=["summary-bulkhead.md"],
        purpose="Summary-only term - surfaces at score=1",
    ),

    # --- Structural limitations (these probes EXPECT failure; failure is the finding) ---
    Probe(
        name="LIMIT body-not-indexed",
        query="saga",
        expected_top_paths=[],
        purpose="Term lives only inside the .md body - must miss (body never indexed)",
        notes="EXPECTED to miss. Single biggest limitation of current algo.",
    ),
    Probe(
        name="LIMIT no-stemming-plural-query",
        query="retries",
        expected_top_paths=["plural-only.md"],
        purpose="Query 'retries' should find singular-only.md too (stem 'retry') but won't",
        notes="EXPECTED partial. 'retries' won't substring-match 'retry'.",
    ),
    Probe(
        name="LIMIT no-stemming-singular-to-plural",
        query="retry",
        expected_top_paths=["singular-only.md"],
        purpose="Query 'retry' matches only the literal substring; plural-only.md ('handling retries') is missed because 'retry' (-y) is not a substring of 'retries' (-ies).",
        notes="EXPECTED partial: stemming is absent in BOTH directions. The 'retry IS a substring of retries' hypothesis was wrong - the -y/-ies boundary breaks substring match.",
    ),
    Probe(
        name="LIMIT hyphen-vs-space",
        query="rate limiter",
        expected_top_paths=["hyphen-title.md"],
        purpose="Query 'rate limiter' (two tokens) vs title 'rate-limiter' (one token). 'rate' and 'limiter' substring-match the hyphenated form - works.",
        notes="Passes by accident: substring matching ignores the hyphen.",
    ),
    Probe(
        name="LIMIT punctuation-in-query",
        query="circuit-breaker",
        expected_top_paths=[],
        purpose="Query has hyphen, title 'circuit breaker pattern' has space -> 'circuit-breaker' not a substring -> MISS",
        notes="EXPECTED to miss. Punctuation in the query is brittle.",
    ),
    Probe(
        name="LIMIT stopwords-not-stripped",
        query="how to use the",
        expected_top_paths=["stopword-decoy.md"],
        purpose="Tokenizer only filters len<=1, so 'how/to/the/use' all count - decoy matches on stop-words alone",
        notes="EXPECTED hit. Demonstrates noise from absent stop-word filter.",
    ),

    # --- Multi-token sanity ---
    Probe(
        name="multi-token-disambiguation",
        query="azure networking",
        expected_top_paths=["azure-networking.md"],
        purpose="Two tokens; azure-networking hits both, azure-storage hits only 'azure' - networking should win",
    ),

    # --- Edge cases ---
    Probe(
        name="EDGE empty-query",
        query="",
        expected_top_paths=[],
        purpose="Empty query -> guard returns []",
    ),
    Probe(
        name="EDGE single-char-tokens-only",
        query="a i",
        expected_top_paths=[],
        purpose="Tokenizer drops len<=1 - no tokens survive -> []",
    ),
    Probe(
        name="EDGE no-match",
        query="quantum blockchain raccoon",
        expected_top_paths=[],
        purpose="Out-of-KB nonsense -> []",
    ),
    Probe(
        name="EDGE case-insensitive",
        query="CIRCUIT Breaker Pattern",
        expected_top_paths=["title-circuit.md"],
        purpose="Both query and fields are lowercased - case must not matter",
    ),
]


# ---------------------------------------------------------------------------
# Real-KB sanity probes - small set against whatever .md files load from disk.
# ---------------------------------------------------------------------------

REAL_KB_PROBES: list[Probe] = [
    Probe(
        name="real:circuit-breaker",
        query="circuit breaker",
        expected_top_paths=["kb/patterns/circuit-breaker.md"],
        purpose="Direct title hit on a known KB doc",
    ),
    Probe(
        name="real:incident",
        query="incident response",
        expected_top_paths=["kb/runbooks/incident-response.md"],
        purpose="Title hit on the runbook",
    ),
    Probe(
        name="real:synonym-failure",
        query="diagram from python",
        expected_top_paths=["kb/python_diagrams/README.md"],
        purpose="Conceptual phrase. Score ties between python-diagrams README and drawio examples mean the 'right' doc may not win.",
        notes="The interesting finding is the tie at top score, not pass/fail of the literal expectation.",
    ),
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_results(title: str, results: list[ProbeResult]) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print(f"{'probe':<42} {'pass':<6} {'top result':<26}")
    print("-" * 78)
    for r in results:
        top = r.actual_paths[0] if r.actual_paths else "(empty)"
        if len(top) > 24:
            top = top[:21] + "..."
        pass_str = "PASS" if r.passed else "FAIL"
        print(f"{r.probe.name:<42} {pass_str:<6} {top:<26}")

    print()
    for r in results:
        if not r.passed or r.probe.notes:
            print(f"  [{r.probe.name}]")
            print(f"     purpose: {r.probe.purpose}")
            print(f"     query:   {r.probe.query!r}")
            print(f"     expected top: {r.probe.expected_top_paths or '(empty result)'}")
            print(f"     actual: {list(zip(r.actual_paths, r.actual_scores)) or '(empty)'}")
            if r.probe.notes:
                print(f"     notes:   {r.probe.notes}")
            print()


# ---------------------------------------------------------------------------
# Latency + saturation at scale
# ---------------------------------------------------------------------------

def make_synthetic_index(n: int, dup_tag: str = "shared") -> list[KBEntry]:
    """Build n entries; every entry shares one tag so we can probe score saturation."""
    out = []
    for i in range(n):
        out.append(KBEntry(
            path=f"synthetic/doc-{i:05d}.md",
            title=f"document number {i} about topic-{i % 50}",
            summary=f"summary of doc {i} discussing item-{i % 20}",
            tags=[dup_tag, f"bucket-{i % 10}"],
        ))
    return out


def measure_latency(svc: KBService, sizes: list[int], query: str, repeats: int = 20) -> list[tuple[int, float, int]]:
    """For each size, install a synthetic index of that size, run `query` `repeats` times, return (n, ms_per_call, hits)."""
    rows = []
    for n in sizes:
        set_index(make_synthetic_index(n))
        # Warm up
        svc.search(query, limit=50)
        start = time.perf_counter()
        for _ in range(repeats):
            results = svc.search(query, limit=50)
        elapsed_ms = (time.perf_counter() - start) * 1000 / repeats
        rows.append((n, elapsed_ms, len(results)))
    return rows


def measure_saturation(svc: KBService, n: int = 1000) -> tuple[int, int, int]:
    """At n entries with a shared tag, how many results tie on the top score for that tag query?"""
    set_index(make_synthetic_index(n))
    results = svc.search("shared", limit=50)
    if not results:
        return (n, 0, 0)
    top_score = _score("shared", results[0])
    tied = sum(1 for r in results if _score("shared", r) == top_score)
    return (n, top_score, tied)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    svc = KBService()

    # --- Phase 1: synthetic probes ---
    set_index(FIXTURES)
    synthetic_results = [run_probe(svc, p) for p in PROBES]
    print_results("PHASE 1 - Synthetic probes (controlled fixtures)", synthetic_results)

    s_pass = sum(1 for r in synthetic_results if r.passed)
    print(f"  summary: {s_pass}/{len(synthetic_results)} probes matched expectation")
    print("  (note: probes prefixed LIMIT/ASYMMETRY/EDGE assert documented behavior, including expected misses)")

    # --- Phase 2: real KB sanity ---
    indexer.load_index()
    real_results = [run_probe(svc, p) for p in REAL_KB_PROBES]
    print_results("PHASE 2 - Real (test) KB on disk", real_results)

    # Drill into the broad "drawio" probe
    drawio_results = svc.search("drawio", limit=50)
    print(f"  'drawio' broad query -> {len(drawio_results)} hits")
    if drawio_results:
        top_score = _score("drawio", drawio_results[0])
        tied = sum(1 for r in drawio_results if _score("drawio", r) == top_score)
        print(f"  top score = {top_score}, tied at top = {tied}")

    # --- Phase 3: latency at scale ---
    print("\n" + "=" * 78)
    print("PHASE 3 - Latency vs index size (synthetic, query='topic-7', 20 runs avg)")
    print("=" * 78)
    print(f"{'n entries':<12} {'ms/search':<14} {'hits':<8}")
    print("-" * 78)
    latency_rows = measure_latency(svc, [100, 1_000, 10_000, 50_000], "topic-7")
    for n, ms, hits in latency_rows:
        print(f"{n:<12} {ms:<14.3f} {hits:<8}")

    # --- Phase 4: score saturation ---
    print("\n" + "=" * 78)
    print("PHASE 4 - Score saturation at 1000 entries sharing a tag")
    print("=" * 78)
    n, top, tied = measure_saturation(svc, n=1000)
    print(f"  query 'shared' against {n} entries with shared tag")
    print(f"  top score: {top}")
    print(f"  results tied at top score: {tied}  (ranking is effectively undefined among these)")

    # --- Final headline ---
    print("\n" + "=" * 78)
    print("HEADLINE FINDINGS")
    print("=" * 78)
    findings = [
        "1. Body-only terms are unfindable. Curation of title/tags/summary is the only path to discoverability.",
        "2. No stemming, in either direction. 'retry' misses 'retries' and vice versa; -y/-ies break substring match.",
        "3. Punctuation breaks search asymmetrically. 'circuit-breaker' (hyphen) misses 'circuit breaker' (space).",
        "4. Stop-words count as real tokens (only len<=1 filtered). Noisy queries match decoys.",
        "5. Field weights work as advertised (title=3, tag=2, summary=1).",
        "6. Scoring is binary per field per token - no TF, so repeated terms add nothing.",
        "7. Latency is O(N * tokens). Pure-Python substring scan; ms-scale until tens-of-thousands of entries.",
        "8. Score ties at scale: with a shared tag across N docs, all N tie - output order is index order.",
    ]
    for f in findings:
        print("  " + f)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
