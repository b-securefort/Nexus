# Agent memory gaps — exploration parked

**Date parked**: 2026-05-17
**Pick back up**: ~2026-06-17 or later
**Status**: Decisions made, implementation not started

---

## Why this exists

Brainstorming session on agent memory architecture. The premise we worked from: classic RAG (chunk + vector search) is insufficient for agents that do real work, because they rediscover the same context across runs. The industry trend is toward multiple retrieval shapes — fuzzy prose, structured documents, tabular data, and graph relationships — rather than a single vector-search-everything approach.

We mapped that broader framing against Nexus's current architecture to figure out which gaps actually apply to our use case and which are noise.

---

## Where Nexus already covers the broader framing

| Concept | Nexus equivalent | Files |
|---|---|---|
| Hybrid search beyond pure vectors | `search_kb_hybrid` — BM25 + sqlite-vec cosine + RRF | `backend/app/tools/generic/kb_tools.py:265`, `backend/app/kb/vector_store.py` |
| Persistent memory across runs | `learn.md` + `read_learnings` / `update_learnings` | `backend/app/tools/generic/learn_tool.py` |
| Structured/tabular retrieval | `az_resource_graph` (KQL against ARG) | `bundles/azure/az_resource_graph.py` |
| Tiered retrieval with fallback | `search_kb` → `search_kb_semantic` → `search_kb_hybrid` | `backend/app/tools/generic/kb_tools.py` |
| Appropriate context, not max context | Asymmetric compaction — user msgs verbatim, scaffolding compressed | DESIGN.md §2 + decision 2026-05-14 |
| Multi-strategy retry | 3 escalating strategies before giving up | `backend/app/agent/orchestrator.py` |

Nexus is already ahead of most architectures we discussed. The hybrid search and learnings system cover the two largest gaps we identified.

---

## The four gaps we identified — verdict for Nexus

| Gap | Verdict | Why |
|---|---|---|
| **Pre-compiled bundles** (assemble agent context ahead of time, cache it) | **Skip** | ADRs already appear in the KB index summary injected into every system prompt. The agent knows they exist; `search_kb_hybrid` retrieves them in ~50 ms. Compaction preserves outcomes within a conversation. Cross-conversation rediscovery is a real concern but the fix is rediscovery metrics first, not pre-compilation. |
| **Source authority tracking** (mark which sources are ground truth vs. merely relevant) | **Do** | Today every KB chunk ranks by RRF score alone. The agent can't tell an ADR from a stale wiki page. ADRs are the obvious authoritative class. |
| **Graph retrieval** (relational queries over entities) | **Skip** | Nexus KB is hundreds of docs, not a web of entities. RRF already handles cross-document relevance well enough. |
| **Rediscovery metrics** (track how often agent re-fetches the same content) | **Do, cheaply** | `/metrics` endpoint already exists. Counting repeated `read_kb_file` and `search_kb_hybrid` calls per `kb_path` across conversations costs almost nothing. Builds on the existing Prometheus scrape target. |

---

## Decision in flight: Source authority — Option C (heading breadcrumb prefix)

### How it works

When a chunk is indexed, the chunker inspects the source (front-matter, path, or title pattern) and prepends an authority tag to the breadcrumb stored on `kb_chunks.heading`:

```
Today:    heading = "Multi-Region DR > HA Strategy > RPO Targets"
With C:   heading = "[ADR] Multi-Region DR > HA Strategy > RPO Targets"
          heading = "[POLICY] Tagging Standards > Required Tags"
          heading = "[WIKI] Onboarding > Day 1 Setup"
```

The agent sees the tag in `search_kb_hybrid` results because `heading` is already returned in the JSON ([kb_tools.py:343-350](../backend/app/tools/generic/kb_tools.py#L343-L350)). One sentence added to the relevant skill prompts tells the model to prefer `[ADR]` and `[POLICY]` over `[WIKI]` when they conflict.

### Why C over the alternatives

| Option | What | Verdict |
|---|---|---|
| A: New `authority_tier` column on `kb_chunks`, path-based rule in reindexer | Requires the KB to have predictable paths first. Wiki-based hierarchy isn't structured enough yet. | **Later** — once KB structuring work lands |
| B: Per-file `authority:` front-matter field | Requires annotating every existing doc | Too much manual work |
| C: Heading breadcrumb prefix derived from front-matter/path/title | No schema change. No migration besides a reindex. Works on whatever metadata exists today. | **Now** |

Sequencing:
- **Now**: Option C — front-matter/title-driven breadcrumb prefix
- **Later**: Option A — `kb_chunks.authority_tier` column, used for query-time boosting/filtering once KB paths are predictable

### Files that will change

| File | Change |
|---|---|
| `backend/app/kb/chunker.py` | Add `_authority_tag(kb_path, frontmatter, title)` helper. Modify `_heading_breadcrumb()` to prepend the tag when present. |
| `backend/app/kb/reindex.py` | No change needed if chunker handles it at construction time. Verify the reindex pipeline re-runs cleanly. |
| `backend/kb_data/skills/shared/architect/SKILL.md` | Add one sentence: "When KB results carry `[ADR]` or `[POLICY]` tags, treat those as authoritative — prefer them over `[WIKI]` when they conflict." |
| `backend/kb_data/skills/shared/azure-principal-architect/SKILL.md` | Same sentence. |
| `backend/kb_data/skills/shared/chat-with-kb/SKILL.md` | Same sentence. |
| `Documentation/DESIGN.md` §5 | Add decision entry once shipped. |
| `Documentation/GLOSSARY.md` | Add ADR row (see below). Optionally add "authority tag" / "source authority" if it becomes load-bearing terminology. |

### Open question that must be answered before starting

**What does the front-matter on a real ADR file look like today?** Specifically, is there a `type:` field, a `title:` that starts with `ADR-NNN`, or anything else the chunker can reliably detect?

Quick check: open `backend/kb_data/kb/adrs/adr-001-multi-region.md` and look at the YAML header. Then compare to a wiki page under `backend/kb_data/kb/ado_wiki/`. If front-matter consistently differentiates them, Option C ships in a day. If not, the KB structuring work has to land first or Option C falls back to title-pattern matching only (less reliable).

---

## Decision in flight: Rediscovery metrics

### What to track

A counter in `backend/app/agent/orchestrator.py` or in the tool execution path that increments per `(kb_path, user_oid)` whenever `read_kb_file` or `search_kb_hybrid` returns a chunk from that path. Exported via the existing `/metrics` endpoint.

### Specific Prometheus metrics to add

```
nexus_kb_path_reads_total{kb_path="kb/adrs/adr-001-multi-region.md", user_oid="..."} counter
nexus_kb_path_search_hits_total{kb_path="...", user_oid="..."} counter
```

After a month of usage, query: which `kb_path`s have the highest per-user read counts across distinct conversations? Those are the candidates for "always include in skill prompt" (lightweight pre-compilation) or for skill-specific KB pinning.

### Why this is cheap

- `prometheus-client` is already a dependency (DESIGN.md §3)
- `/metrics` endpoint already exists (DESIGN.md §6)
- No schema change
- No migration
- Counter increments are O(1)

### Files that will change

| File | Change |
|---|---|
| `backend/app/tools/generic/kb_tools.py` | Increment counter at the end of `ReadKBFileTool.execute` and `SearchKBHybridTool.execute` |
| `backend/app/api/health.py` (or wherever `/metrics` lives) | Register the new counters |
| `Documentation/DESIGN.md` §6 | Document the new metrics in the operations section |

---

## GLOSSARY proposal to land alongside this work

ADR is used throughout this thread and the codebase but isn't in GLOSSARY.md. Add:

```markdown
| **ADR (Architecture Decision Record)** | A markdown document under `kb_data/kb/adrs/` that captures one architectural decision: context, the choice made, alternatives considered, and trade-offs accepted. Distinct from a design document — an ADR explains *why* a thing is built the way it is, not *what* it is. ADRs are authoritative KB content and the canonical example of a source-authority tier. | "design doc", "architecture note" |
```

---

## How to resume

1. Read this file.
2. Open one ADR file (`backend/kb_data/kb/adrs/adr-001-multi-region.md`) and one wiki page front-matter. Confirm whether the type can be reliably inferred today.
3. If yes — start with Option C. Land it in a single PR alongside the ADR glossary entry.
4. Rediscovery metrics can ship as a separate PR, independent of Option C.
5. Defer Option A and pre-compiled bundles until rediscovery metrics give you 4+ weeks of real data.

---

## Conversation reference

Original exploration was a grill-with-docs brainstorming session on agent memory architecture and where Nexus has gaps. The relevant DESIGN.md sections are §2 (KB retrieval), §3 (sqlite-vec dep), §5 (decision log — see 2026-05-15 "Azure OpenAI text-embedding-3-small"), and §7 (Open questions / future work). No DESIGN.md §5 entry has been added yet — that lands when the implementation does.
