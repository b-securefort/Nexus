# KB hybrid search — deferred improvements

**Date parked**: 2026-05-25
**Source**: 50-probe ground-truth eval of `search_kb_hybrid` against the test KB
**Source eval scripts**: [backend/scripts/kb_hybrid_eval_50.py](../backend/scripts/kb_hybrid_eval_50.py), [backend/scripts/kb_hybrid_eval_50_results.json](../backend/scripts/kb_hybrid_eval_50_results.json)
**Headline from eval**: 100% R@5, 86% top@1, MRR 0.92 — strong baseline. Two production gaps were fixed in-line (acronym-expanded embedding + confidence signal). The items below are quality polish to revisit when the real KB lands or when failure modes from production usage start to bite.

---

## Already shipped (do not re-do)

- **Embed the acronym-expanded query** — fixed. The embedded text is now the joined acronym expansion, not the raw user query. Lifts E1 / E4-style short acronym queries.
- **Confidence signal** — fixed. `search_kb_hybrid` results now expose a per-hit `confidence` (high / medium / low). The envelope adds a `low_confidence_only=true` flag when no hit clears the medium bar.
- **LLM-judge re-ranker** — shipped 2026-05-25. `app/kb/reranker.py` scores the top-K RRF candidates 0.0-1.0 against the query via the existing Azure OpenAI chat deployment, and reorders + relabels confidence accordingly. The confidence thresholds (`KB_RERANK_HIGH_THRESHOLD`, `KB_RERANK_MEDIUM_THRESHOLD`) are now on a calibrated relevance scale, so they no longer need per-KB tuning — the per-corpus distance threshold from the previous fix is preserved only as a fallback for when the LLM call fails.

- **Full-chunk text passed to the reranker** — shipped 2026-05-25. The first cut of the reranker passed only the 400-char `snippet` to the judge, which silently broke ranking for any chunk whose key sentence sat past char 400 (C4 "43.8 minutes" and B6 "FIDO2" were classic examples — the relevant line was buried inside an H2 section). The judge correctly scored 0.0 on the preview, and RRF order won. Added a `text` field to `SearchHit` carrying the full chunk content; reranker now passes up to 2000 chars per chunk to the judge.

  **If a chunk is ever still too long for the judge to see fully** (a single H2/H3 section past the per-chunk judge cap on real data): raise `_JUDGE_CONTEXT_CHARS` in [reranker.py](../backend/app/kb/reranker.py), or lower `KB_CHUNK_MAX_CHARS` in [config.py](../backend/app/config.py) so the chunker's paragraph-split fires sooner, or restructure the doc with more H3s. These two integers cover the entire failure surface — no special chunker tier needed.

- **Bidirectional acronym expansion** — shipped 2026-05-25. `acronyms.py` now also expands phrase → abbreviation: e.g. a query containing "key vault" picks up "kv", "managed identity" picks up "msi" and "mi", "azure kubernetes service" picks up "aks". Useful when KB docs use the abbreviation form the user didn't type. No effect on this test KB (docs use full names consistently); benefit is for real KBs with mixed naming.

- **Multi-doc diversity cap** — shipped 2026-05-25. `KB_DIVERSITY_MAX_PER_FILE=2` (default). After rerank, the top-K is filtered to at most 2 chunks per file, with the best chunk per file preserved. Soft cap — falls back to repeats if needed to honour the requested `limit`. Improves the top-5 spread for cross-cutting queries; not measurable in recall@5 but visible to the agent reading multiple results.

- **Procedural-doc boost** — shipped 2026-05-25. `hybrid_search` now applies a small RRF boost (~+0.008) to chunks from `kb/recipes/`, `kb/runbooks/`, `kb/drawio/patterns.md`, or chunks containing fenced code blocks — but only when the query contains a procedural cue word (`exact`, `command`, `code`, `syntax`, `example`, `step`, `snippet`, or the phrase `how to` / `step by step`). The boost is small enough that the LLM reranker still has final say; its main role is to ensure procedural docs make it into the rerank window even when their pure-vector score is lower than a conceptual doc. Mostly a safety net at this corpus size; matters more at scale.

  **Cumulative eval delta** (all four post-LLM-reranker fixes applied): overall MRR **0.985**, top@1 **98%** (43/44), recall@5 **100%**. Confidence: **44/44 true positives high**, 4/4 OOD low. **TP-min rerank score 0.70 vs OOD-max 0.00 — clean +0.70 separation** (was +0.50 before this batch — the diversity / procedural / bidirectional changes tightened the OOD reject signal). Latency ~2s p50 (mostly the LLM rerank call).

  **Only remaining failure**: E1 `"AAD identity setup"` ranks the drawio Managed-Identity-misuse chunk over security-basics. Judge call is defensible (the drawio chunk genuinely covers Managed Identity placement); the ground-truth label may be too strict. Recall@5 stays 100%.

---

## Deferred — ordered by ROI when the real KB lands

### 8. Calibrate `KB_BM25_TOP_K` / `KB_VEC_TOP_K` / `KB_RRF_K` on real KB scale
**Problem**: current defaults (50 / 50 / 60) on a 149-chunk test KB mean BM25 and vec each return essentially every chunk that matches. RRF then functions more like a vote count than a true fusion. On a 10k-chunk real KB the choices will matter much more.

**Fix**: when the real KB lands, sweep `KB_BM25_TOP_K` and `KB_VEC_TOP_K` in `{20, 50, 100, 200}` and `KB_RRF_K` in `{20, 60, 100}`, measure recall@5 and MRR on a real-KB labelled set. Pick the smallest values that don't hurt recall.

**Where to apply**: [backend/app/config.py](../backend/app/config.py) defaults. Probably also worth making them per-skill overrideable.

**Retire criteria**: documented sweep in this file's "shipped" section above.

---

## Notes for whoever picks this up

- Re-run [backend/scripts/kb_hybrid_eval_50.py](../backend/scripts/kb_hybrid_eval_50.py) **before** changing anything — gives you the current baseline numbers to beat.
- Items 3-5 and 7 have shipped. Item 6 was dropped (existing knobs cover the failure surface — see the full-chunk-text entry above). Item 8 is meaningful only after the real KB is in place.
- Cross-reference [[parked_agent_memory_gaps]] — broader retrieval-architecture exploration parked separately.
