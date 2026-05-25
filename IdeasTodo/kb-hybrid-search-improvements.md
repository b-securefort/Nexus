# KB hybrid search — deferred improvements

**Date parked**: 2026-05-25
**Source**: 50-probe ground-truth eval of `search_kb_hybrid` against the test KB
**Source eval scripts**: [backend/scripts/kb_hybrid_eval_50.py](../backend/scripts/kb_hybrid_eval_50.py), [backend/scripts/kb_hybrid_eval_50_results.json](../backend/scripts/kb_hybrid_eval_50_results.json)
**Headline from eval**: 100% R@5, 86% top@1, MRR 0.92 — strong baseline. Two production gaps were fixed in-line (acronym-expanded embedding + confidence signal). The items below are quality polish to revisit when the real KB lands or when failure modes from production usage start to bite.

---

## Already shipped (do not re-do)

- **Embed the acronym-expanded query** — fixed. The embedded text is now the joined acronym expansion, not the raw user query. Lifts E1 / E4-style short acronym queries.
- **Confidence signal** — fixed. `search_kb_hybrid` results now expose a per-hit `confidence` (high / medium / low) derived from sources-hit + RRF score + vector cosine distance. The envelope adds a `low_confidence_only=true` flag when no hit clears the medium bar, so the agent can say "I don't see a documented answer" instead of returning junk.

---

## Deferred — ordered by ROI when the real KB lands

### 3. Cross-encoder / LLM-judge rerank for top-K
**Problem**: 6/44 labelled probes had the right file in top-5 but not at rank 1. All shared the same failure mode: vector similarity rewards chunks with broad topical overlap, not chunks containing the specific answer.

**Examples from the eval**:
- `"43.8 minutes per month error budget"` → cloud-fundamentals (Cost Management) at #1, devops-practices (Error Budget) at #2
- `"RBAC role assignments KQL query"` → security-basics (IAM concepts) at #1, recipes/resource-graph (literal KQL example) at #2
- `"what exact name does AzureFirewallSubnet need"` → ms_reference_style (Bastion Subnet) at #1, azure_architecture_semantics (correct answer) at #2

**Fix**: After RRF returns top-N, do a second-pass rerank over the top-10. Two options:
- Lightweight cross-encoder (e.g. `ms-marco-MiniLM-L-12-v2`) — local, ~50ms latency, no extra LLM cost.
- LLM-judge — reuse the existing Azure OpenAI deployment. Higher cost per query but no new dependency.

**Where to apply**: in [backend/app/kb/vector_store.py](../backend/app/kb/vector_store.py) after `_rrf`, or inside [SearchKBHybridTool.execute](../backend/app/tools/generic/kb_tools.py).

**Retire criteria**: top@1 on the 50-probe eval goes from current baseline (post-fix) to ≥ 95%, AND latency stays under 1s p95.

---

### 4. Procedural-doc boost for fact-lookup queries
**Problem**: queries with procedural cues (`"exact name"`, `"command"`, `"query"`, `"value"`, `"how to"`) get pulled toward conceptual files instead of recipes/runbooks/patterns where the literal answer lives. F1 (RBAC KQL), F2 (Monitor placement), G6 (AzureFirewallSubnet name) all fail this way.

**Fix**: post-RRF re-rank that applies a small positive boost (~+0.005 RRF) to chunks from:
- `kb/recipes/` (procedural recipes)
- `kb/runbooks/` (step-by-step actions)
- `kb/drawio/patterns.md` (copy-paste fragments)
- Chunks containing fenced code blocks (signal: the chunk has runnable content)

Gate the boost on the query containing a procedural cue word (small allowlist).

**Where to apply**: a new step in [hybrid_search()](../backend/app/kb/vector_store.py) between RRF and hydration.

**Retire criteria**: F1 / F2 / G6 all reach rank 1 in the eval.

---

### 5. Bidirectional acronym expansion
**Problem**: [acronyms.py](../backend/app/kb/acronyms.py) only expands abbrev → full. A query for `"Key Vault"` does NOT get expanded to `"KV"`. Won't matter on the test KB (consistent full names), but will hurt on a real KB where some docs use abbreviations.

**Fix**: at module load, build the reverse map (`"key vault" → ["kv"]`, `"managed identity" → ["msi", "mi"]`, etc.). When `expand_query()` sees a multi-word phrase from the reverse map, add the abbreviation. Cap total expansions to 6 as today.

**Where to apply**: [backend/app/kb/acronyms.py](../backend/app/kb/acronyms.py).

**Retire criteria**: a new probe set with abbrev-only docs shows the abbrev-form query finds them.

---

### 6. Tighter chunks for short docs
**Problem**: default `KB_CHUNK_MAX_CHARS=6000` means several test-KB files are one chunk each (adr-001 is ~26 lines; circuit-breaker is ~31 lines). Specific-fact queries (`"43.8 minutes"`, `"AzureFirewallSubnet"`) lose ranking weight when the rare term is one sentence in a 5KB chunk that scores on overall similarity.

**Fix**: introduce a second chunk-size tier. Files under ~2KB get 1500-char chunks (with the same overlap fraction). Files above stay at 6000.

**Where to apply**: [backend/app/kb/chunker.py](../backend/app/kb/chunker.py). Requires a `force_rebuild()` after the change.

**Trade-off**: more chunks (~3× for small docs), more storage, slightly slower search. Worth it for fact-lookup quality.

**Retire criteria**: C4 / G6 / specific-numeric-value probes reach rank 1 without rerank.

---

### 7. Multi-doc diversity in top-K
**Problem**: D4 (`"how to manage secrets in Azure pipelines"`) and other cross-cutting queries have legitimate answers in 2-3 different files (devops-practices secrets table, security-basics key-management section, platform/azure-overview). Current top-5 often contains multiple chunks from the same file rather than spanning the relevant docs.

**Fix**: after RRF, apply a "max-N-chunks-per-file" cap (e.g. 2) during top-K selection. Implementation: iterate through the fused list, skip chunks whose file is already at cap, fill the remainder from the next-best candidates.

**Where to apply**: end of [hybrid_search()](../backend/app/kb/vector_store.py) before hydration.

**Retire criteria**: D4 and similar cross-cutting probes show top-5 paths spanning ≥ 2 files.

---

### 8. Calibrate `KB_BM25_TOP_K` / `KB_VEC_TOP_K` / `KB_RRF_K` on real KB scale
**Problem**: current defaults (50 / 50 / 60) on a 149-chunk test KB mean BM25 and vec each return essentially every chunk that matches. RRF then functions more like a vote count than a true fusion. On a 10k-chunk real KB the choices will matter much more.

**Fix**: when the real KB lands, sweep `KB_BM25_TOP_K` and `KB_VEC_TOP_K` in `{20, 50, 100, 200}` and `KB_RRF_K` in `{20, 60, 100}`, measure recall@5 and MRR on a real-KB labelled set. Pick the smallest values that don't hurt recall.

**Where to apply**: [backend/app/config.py](../backend/app/config.py) defaults. Probably also worth making them per-skill overrideable.

**Retire criteria**: documented sweep in this file's "shipped" section above.

---

## Notes for whoever picks this up

- Re-run [backend/scripts/kb_hybrid_eval_50.py](../backend/scripts/kb_hybrid_eval_50.py) **before** changing anything — gives you the current baseline numbers to beat.
- Items 3-7 are independent; can be done in any order. Item 8 is meaningful only after the real KB is in place.
- Cross-reference [[parked_agent_memory_gaps]] — broader retrieval-architecture exploration parked separately.
