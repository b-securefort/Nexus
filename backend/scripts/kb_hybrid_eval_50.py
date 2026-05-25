"""
KB hybrid search 50-probe evaluation.

Acts as the equivalent of a search-quality SME: ground-truth labels are derived
from reading every .md file in the KB and identifying distinctive vocabulary,
body-only terms, paraphrases, and likely failure modes.

Reports:
  - per-probe hit@5 and rank of expected file
  - which chunk (heading + snippet) surfaced
  - keyword baseline side-by-side
  - score distribution: true hits vs out-of-KB
  - failure pattern grouping (with recommendations)

Run from backend/:
    python scripts/kb_hybrid_eval_50.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

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
from app.kb.embedder import embed_query_for_search
from app.kb.vector_store import chunk_count, hybrid_search
from app.kb.acronyms import expand_query
from app.db.engine import get_engine
from app.db.sqlite_vec_loader import hybrid_disabled, disabled_reason


# ---------------------------------------------------------------------------
# Probe data model
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    name: str
    query: str
    expected_paths: list[str]
    category: str
    purpose: str
    notes: str = ""


# ---------------------------------------------------------------------------
# 50 probes — ground truth built from reading every .md file in the KB.
# Convention: an empty expected_paths means "informational / no labelled
# target" (used for out-of-KB and degenerate-input probes).
# ---------------------------------------------------------------------------

PROBES: list[Probe] = [
    # ===== A. Title / direct sanity (3) =====
    Probe("A1.title-circuit-breaker", "circuit breaker pattern",
          ["kb/patterns/circuit-breaker.md"], "title",
          "Direct title hit, should be trivial."),
    Probe("A2.title-incident-runbook", "incident response runbook",
          ["kb/runbooks/incident-response.md"], "title",
          "Direct title hit."),
    Probe("A3.title-platform-overview", "azure platform overview",
          ["kb/platform/azure-overview.md"], "title",
          "Direct title hit on the platform doc."),

    # ===== B. Body-only rare terms (8) — keyword can't see these =====
    Probe("B1.body-polly", "Polly resilience library",
          ["kb/patterns/circuit-breaker.md"], "body-only",
          "'Polly' is only inside the Implementation chunk."),
    Probe("B2.body-tenacity", "tenacity Python decorator example",
          ["kb/patterns/circuit-breaker.md"], "body-only",
          "'tenacity' is in the code block only."),
    Probe("B3.body-pagerduty", "PagerDuty acknowledge alert",
          ["kb/runbooks/incident-response.md"], "body-only",
          "'PagerDuty' is in the step-by-step body only."),
    Probe("B4.body-sev1", "SEV1 complete outage response time",
          ["kb/runbooks/incident-response.md"], "body-only",
          "'SEV1' is in the severity table body."),
    Probe("B5.body-rfc1918", "RFC 1918 private address ranges",
          ["kb/general/networking-basics.md"], "body-only",
          "RFC 1918 only inside networking-basics."),
    Probe("B6.body-fido2", "FIDO2 hardware security key",
          ["kb/general/security-basics.md"], "body-only",
          "FIDO2 in security-basics MFA paragraph."),
    Probe("B7.body-dora-mttr", "DORA metrics MTTR change failure rate",
          ["kb/general/devops-practices.md"], "body-only",
          "DORA table is body-only."),
    Probe("B8.body-let-bindings", "let bindings KQL Resource Graph",
          ["kb/recipes/resource-graph.md"], "body-only",
          "'let bindings not supported' is a specific gotcha in body."),

    # ===== C. Specific code/config values (5) =====
    Probe("C1.config-cost-center", "CC-1234 cost center tag",
          ["kb/platform/azure-overview.md"], "config-value",
          "Specific tag value 'CC-1234'."),
    Probe("C2.config-rg-naming", "team-architect-dev-rg resource group",
          ["kb/platform/azure-overview.md"], "config-value",
          "Specific RG name example."),
    Probe("C3.config-failure-threshold", "5 failures in 30 seconds threshold",
          ["kb/patterns/circuit-breaker.md"], "config-value",
          "Specific circuit-breaker threshold."),
    Probe("C4.config-error-budget", "43.8 minutes per month error budget",
          ["kb/general/devops-practices.md"], "config-value",
          "Specific numeric error-budget value."),
    Probe("C5.config-regions", "East US 2 and West Europe active-active",
          ["kb/adrs/adr-001-multi-region.md"], "config-value",
          "Specific region pair in the ADR."),

    # ===== D. Paraphrase / natural-language questions (10) =====
    Probe("D1.paraphrase-cascade", "how do I stop one failing service from taking down the rest",
          ["kb/patterns/circuit-breaker.md"], "paraphrase",
          "Conversational paraphrase of circuit-breaker purpose."),
    Probe("D2.paraphrase-outage", "our production app just went down what should we do first",
          ["kb/runbooks/incident-response.md"], "paraphrase",
          "Casual phrasing of runbook need."),
    Probe("D3.paraphrase-rpo-rto", "what is the difference between RPO and RTO",
          ["kb/general/cloud-fundamentals.md"], "paraphrase",
          "Concept comparison."),
    Probe("D4.paraphrase-secrets", "how to manage secrets in Azure CI/CD pipelines",
          ["kb/general/devops-practices.md", "kb/general/security-basics.md"], "paraphrase",
          "Multi-doc viable; devops-practices has the explicit table."),
    Probe("D5.paraphrase-zero-trust", "how does zero trust security work",
          ["kb/general/security-basics.md"], "paraphrase",
          "Concept question."),
    Probe("D6.paraphrase-az", "when should I use availability zones",
          ["kb/general/cloud-fundamentals.md"], "paraphrase",
          "Concept question, term appears in body."),
    Probe("D7.paraphrase-multi-region", "we want to deploy across multiple regions for high availability",
          ["kb/adrs/adr-001-multi-region.md"], "paraphrase",
          "Should map to the ADR. cloud-fundamentals also discusses HA."),
    Probe("D8.paraphrase-iac-choice", "should I use Terraform or Bicep for Azure",
          ["kb/general/devops-practices.md"], "paraphrase",
          "Concrete choice question against the IaC table."),
    Probe("D9.paraphrase-shared-resp", "what is the shared responsibility model in cloud",
          ["kb/general/cloud-fundamentals.md"], "paraphrase",
          "Direct concept paraphrase."),
    Probe("D10.paraphrase-scaling", "what is horizontal scaling vs vertical scaling",
          ["kb/general/cloud-fundamentals.md"], "paraphrase",
          "Concept comparison."),

    # ===== E. Acronym / abbreviation queries (5) =====
    Probe("E1.acronym-AAD", "AAD identity setup",
          ["kb/general/security-basics.md"], "acronym",
          "AAD -> Entra/AAD in acronym map; security-basics covers identity."),
    Probe("E2.acronym-NSG-rules", "NSG rules best practice",
          ["kb/general/networking-basics.md", "kb/drawio/azure_architecture_semantics.md"], "acronym",
          "NSG -> network security group. Networking-basics has the firewalls section."),
    Probe("E3.acronym-AKS", "AKS cluster networking",
          ["kb/general/cloud-fundamentals.md", "kb/drawio/azure_architecture_semantics.md", "kb/python_diagrams/README.md"], "acronym",
          "AKS -> kubernetes. Multiple plausible files; ambiguous."),
    Probe("E4.acronym-KV", "KV secret rotation",
          ["kb/general/devops-practices.md", "kb/general/security-basics.md"], "acronym",
          "KV -> key vault. Either doc is a fair answer."),
    Probe("E5.acronym-AFD-PE", "AFD with private link to PE",
          ["kb/drawio/azure_architecture_semantics.md"], "acronym",
          "Pattern A is explicitly about AFD + PE."),

    # ===== F. Cross-cutting / multi-doc (4) =====
    Probe("F1.cross-rbac-kql", "RBAC role assignments KQL query",
          ["kb/recipes/resource-graph.md"], "cross-cutting",
          "Recipes file has the KQL example; security-basics defines RBAC."),
    Probe("F2.cross-monitor-outside", "Azure Monitor must be placed outside the VNet",
          ["kb/drawio/azure_architecture_semantics.md", "kb/drawio/layoutfixing.md"], "cross-cutting",
          "Two files cover this rule from different angles."),
    Probe("F3.cross-l4-l7", "layer 4 versus layer 7 load balancer",
          ["kb/general/networking-basics.md"], "cross-cutting",
          "Direct concept in networking-basics."),
    Probe("F4.cross-encryption", "encryption at rest versus in transit",
          ["kb/general/security-basics.md"], "cross-cutting",
          "Direct concept in security-basics."),

    # ===== G. Drawio / diagram-specific (6) =====
    Probe("G1.drawio-vnet-color", "what hex color is the VNet container fill",
          ["kb/drawio/ms_reference_style.md"], "drawio",
          "ms_reference_style has the exact hex (#EFF6FC)."),
    Probe("G2.drawio-badge-xml", "numbered green badge XML pattern",
          ["kb/drawio/patterns.md"], "drawio",
          "Pattern #1 in patterns.md."),
    Probe("G3.drawio-aws4-stencil", "AWS4 icon stencil shape syntax",
          ["kb/drawio/REFERENCE.md", "kb/drawio/layoutfixing.md"], "drawio",
          "Both files cover the 'mxgraph.aws4.*' rule."),
    Probe("G4.drawio-f5-pattern", "F5 BIG-IP hub-spoke NVA pattern",
          ["kb/drawio/azure_architecture_semantics.md"], "drawio",
          "Pattern C explicitly."),
    Probe("G5.drawio-graphviz-nodesep", "Graphviz nodesep ranksep tuning",
          ["kb/python_diagrams/README.md"], "drawio",
          "Specific Graphviz attrs in python_diagrams."),
    Probe("G6.drawio-firewall-subnet-name", "what exact name does AzureFirewallSubnet need",
          ["kb/drawio/azure_architecture_semantics.md"], "drawio",
          "Subnet-resident table mentions exact name."),

    # ===== H. Out-of-KB negatives (4) — hybrid should give low scores =====
    Probe("H1.ood-graphql", "GraphQL schema federation Apollo",
          [], "out-of-kb",
          "Not in KB. Hybrid should produce low-confidence results."),
    Probe("H2.ood-salesforce", "Salesforce CRM lightning integration",
          [], "out-of-kb",
          "Not in KB."),
    Probe("H3.ood-quantum", "quantum cryptography lattice algorithm",
          [], "out-of-kb",
          "Not in KB."),
    Probe("H4.ood-stripe", "Stripe webhook signature verification",
          [], "out-of-kb",
          "Not in KB."),

    # ===== I. Misspellings / typos (3) =====
    Probe("I1.typo-circuit", "circiut braker pattern",
          ["kb/patterns/circuit-breaker.md"], "typo",
          "Two-char swap typo."),
    Probe("I2.typo-polly", "Polley dotnet library retry",
          ["kb/patterns/circuit-breaker.md"], "typo",
          "Polley vs Polly."),
    Probe("I3.typo-incident", "incidnet repsonse runbook",
          ["kb/runbooks/incident-response.md"], "typo",
          "Two-word misspell."),

    # ===== J. Edge cases (2) =====
    Probe("J1.edge-empty", "",
          [], "edge",
          "Empty query — must not return results."),
    Probe("J2.edge-all-stopwords", "the of and in to",
          [], "edge",
          "All-stopword query, should be very low confidence."),
]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

@dataclass
class HybridResult:
    paths: list[str]
    top_score: float
    raw_hits: list
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
    if not query.strip():
        # Treat empty as the tool does — search would error / return nothing meaningful
        return HybridResult(paths=[], top_score=0.0, raw_hits=[], embed_ms=0.0, search_ms=0.0)
    engine = get_engine()
    with engine.connect() as conn:
        start_e = time.perf_counter()
        qvec = embed_query_for_search(query)
        embed_ms = (time.perf_counter() - start_e) * 1000

        start_s = time.perf_counter()
        hits = hybrid_search(conn, query, qvec, limit=limit * 3)
        search_ms = (time.perf_counter() - start_s) * 1000

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
    for i, p in enumerate(actual_paths, start=1):
        if p in target_paths:
            return i
    return None


def _trim(s: str, n: int = 28) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_row_table(rows: list[dict]) -> None:
    print(f"{'probe':<32} {'cat':<13} {'kw r':<5} {'hy r':<5} {'score':<8} {'conf':<7} {'src':<4} {'vdist':<7} {'top file':<28}")
    print("-" * 116)
    for r in rows:
        kw_r = str(r["kw_rank"]) if r["kw_rank"] is not None else "-"
        hy_r = str(r["hy_rank"]) if r["hy_rank"] is not None else "-"
        top = _trim(r["hy_top_path"] or "(empty)", 26)
        vd = f"{r['hy_top_vec_distance']:.3f}" if r['hy_top_vec_distance'] is not None else "-"
        print(f"{_trim(r['name'], 30):<32} {r['category']:<13} {kw_r:<5} {hy_r:<5} {r['hy_score']:<8.4f} {r['hy_top_confidence'] or '-':<7} {r['hy_top_sources_hit']:<4} {vd:<7} {top:<28}")


def summarise(rows: list[dict]) -> None:
    print("\n" + "=" * 100)
    print("RECALL / MRR BY CATEGORY")
    print("=" * 100)
    print(f"{'category':<16} {'n':<4} {'kw R@5':<10} {'hy R@5':<10} {'kw MRR':<10} {'hy MRR':<10} {'hy top@1':<10}")
    print("-" * 100)

    order = ["title", "body-only", "config-value", "paraphrase", "acronym",
             "cross-cutting", "drawio", "typo", "out-of-kb", "edge"]
    by_cat = {c: [r for r in rows if r["category"] == c] for c in order}

    for cat in order:
        items = [r for r in by_cat[cat] if not r["expected_blank"]]
        n = len(by_cat[cat])
        if not items:
            print(f"{cat:<16} {n:<4} {'-':<10} {'-':<10} {'-':<10} {'-':<10} {'-':<10}  (no labels; informational)")
            continue
        kw_recall = sum(1 for r in items if r["kw_hit"]) / len(items)
        hy_recall = sum(1 for r in items if r["hy_hit"]) / len(items)
        kw_mrr = sum((1 / r["kw_rank"] if r["kw_rank"] else 0) for r in items) / len(items)
        hy_mrr = sum((1 / r["hy_rank"] if r["hy_rank"] else 0) for r in items) / len(items)
        hy_top1 = sum(1 for r in items if r["hy_rank"] == 1) / len(items)
        print(f"{cat:<16} {len(items):<4} {kw_recall:<10.0%} {hy_recall:<10.0%} {kw_mrr:<10.3f} {hy_mrr:<10.3f} {hy_top1:<10.0%}")

    labelled = [r for r in rows if not r["expected_blank"]]
    n = len(labelled)
    kw_recall = sum(1 for r in labelled if r["kw_hit"]) / n
    hy_recall = sum(1 for r in labelled if r["hy_hit"]) / n
    kw_mrr = sum((1 / r["kw_rank"] if r["kw_rank"] else 0) for r in labelled) / n
    hy_mrr = sum((1 / r["hy_rank"] if r["hy_rank"] else 0) for r in labelled) / n
    hy_top1 = sum(1 for r in labelled if r["hy_rank"] == 1) / n
    print("-" * 100)
    print(f"{'OVERALL':<16} {n:<4} {kw_recall:<10.0%} {hy_recall:<10.0%} {kw_mrr:<10.3f} {hy_mrr:<10.3f} {hy_top1:<10.0%}")


def score_distribution(rows: list[dict]) -> None:
    print("\n" + "=" * 100)
    print("SCORE / CONFIDENCE DISTRIBUTION — true positives vs out-of-KB")
    print("=" * 100)
    hits = [r["hy_score"] for r in rows if r.get("hy_hit") and not r["expected_blank"]]
    ood = [r["hy_score"] for r in rows if r["expected_blank"] and r["category"] == "out-of-kb"]
    edge = [r["hy_score"] for r in rows if r["expected_blank"] and r["category"] == "edge"]

    def stats(name, arr):
        if not arr:
            print(f"  {name:<32} (n=0)")
            return
        a = sorted(arr)
        print(f"  {name:<32} n={len(a)}  min={a[0]:.4f}  median={a[len(a)//2]:.4f}  max={a[-1]:.4f}")

    print("  RRF score:")
    stats("    true positives", hits)
    stats("    out-of-KB queries", ood)
    stats("    edge / degenerate queries", edge)

    print("\n  Confidence label by group:")
    def conf_breakdown(group_rows):
        labels = [r["hy_top_confidence"] for r in group_rows if r["hy_top_confidence"]]
        from collections import Counter
        return dict(Counter(labels))

    tp_rows = [r for r in rows if r.get("hy_hit") and not r["expected_blank"]]
    ood_rows = [r for r in rows if r["expected_blank"] and r["category"] == "out-of-kb"]
    edge_rows = [r for r in rows if r["expected_blank"] and r["category"] == "edge"]
    print(f"    true positives:        {conf_breakdown(tp_rows)}")
    print(f"    out-of-KB queries:     {conf_breakdown(ood_rows)}")
    print(f"    edge / degenerate:     {conf_breakdown(edge_rows)}")

    # Did the low_confidence_only envelope flag fire for the right queries?
    low_only_ood = sum(1 for r in ood_rows if r["all_low_confidence"])
    low_only_tp  = sum(1 for r in tp_rows if r["all_low_confidence"])
    print(f"\n  envelope flag 'low_confidence_only=true' fired on:")
    print(f"    out-of-KB queries:  {low_only_ood}/{len(ood_rows)}  (want: high)")
    print(f"    true positives:     {low_only_tp}/{len(tp_rows)}  (want: 0 — would be a false silence)")


def failure_analysis(rows: list[dict]) -> None:
    print("\n" + "=" * 100)
    print("FAILURE DETAILS — labelled probes where hybrid missed or ranked > 1")
    print("=" * 100)
    failures = [r for r in rows if not r["expected_blank"] and (not r["hy_hit"] or r["hy_rank"] > 1)]
    if not failures:
        print("  (none — every labelled probe top-ranked the expected file)")
        return
    for r in failures:
        verdict = "MISS" if not r["hy_hit"] else f"hit at rank {r['hy_rank']}"
        print(f"\n  [{r['category']}] {r['name']}: {verdict}")
        print(f"     query:         {r['query']!r}")
        print(f"     expected:      {r['expected_paths']}")
        print(f"     hy top file:   {r['hy_top_path']}")
        print(f"     hy top chunk:  {r['hy_top_heading']!r}")
        print(f"     hy top snippet:{r['hy_top_snippet'][:140]!r}")
        if r["notes"]:
            print(f"     probe notes:   {r['notes']}")


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
    if total == 0:
        print("FATAL: hybrid index is empty. Run reindex first.")
        return 2

    print(f"Index: {total} chunks across the KB")
    kw_entries = indexer.load_index()
    print(f"Keyword baseline index: {len(kw_entries)} entries\n")

    rows = []
    for i, probe in enumerate(PROBES, 1):
        kw = run_keyword(probe.query, limit=5)
        hy = run_hybrid(probe.query, limit=5)
        kw_rank = rank_of(probe.expected_paths, kw.paths) if probe.expected_paths else None
        hy_rank = rank_of(probe.expected_paths, hy.paths) if probe.expected_paths else None
        h0 = hy.raw_hits[0] if hy.raw_hits else None
        rows.append({
            "name": probe.name,
            "query": probe.query,
            "category": probe.category,
            "expected_paths": probe.expected_paths,
            "expected_blank": not probe.expected_paths,
            "notes": probe.notes,
            "kw_rank": kw_rank,
            "kw_hit": kw_rank is not None,
            "hy_rank": hy_rank,
            "hy_hit": hy_rank is not None,
            "hy_score": hy.top_score,
            "hy_top_path": hy.paths[0] if hy.paths else "",
            "hy_top_heading": h0.heading if h0 else "",
            "hy_top_snippet": h0.snippet if h0 else "",
            "hy_top_confidence": h0.confidence if h0 else "",
            "hy_top_sources_hit": h0.sources_hit if h0 else 0,
            "hy_top_vec_distance": h0.vec_distance if h0 else None,
            "all_low_confidence": bool(hy.raw_hits) and all(h.confidence == "low" for h in hy.raw_hits[:5]),
            "embed_ms": hy.embed_ms,
            "search_ms": hy.search_ms,
            "kw_ms": kw.elapsed_ms,
        })
        progress = "+" if (probe.expected_paths and hy_rank == 1) else ("." if hy.raw_hits else "?")
        print(f"  [{i:>2}/{len(PROBES)}] {progress} {probe.name}", flush=True)

    print("\n" + "=" * 100)
    print("PER-PROBE TABLE")
    print("=" * 100)
    print_row_table(rows)

    summarise(rows)
    score_distribution(rows)
    failure_analysis(rows)

    # Save raw rows for follow-up analysis
    out_path = BACKEND_DIR / "scripts" / "kb_hybrid_eval_50_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"\nRaw results saved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
