# DSPy — evaluated and deferred

**Status as of 2026-05-25: DSPy is not being adopted in Nexus.** This file
previously held a 4-row use-case tracker; that tracker has been retired
because the evaluation below concluded none of those use cases currently
earn the framework cost. DESIGN.md §7 points here for the conclusion and
the re-evaluation trigger.

---

## Evaluation summary

Walked through five candidate Nexus LLM-call sites against what DSPy
actually provides (typed signatures, modules like `Predict` / `ChainOfThought`,
optimisers like `BootstrapFewShot`, an LM abstraction). The honest scoring:

| Candidate | Verdict | Why |
|---|---|---|
| Compaction summarizer (`compaction.py`) | Skip | Works well; not broken |
| Query expansion (`search_kb_semantic` helpers, `kb_tools.py`) | Skip | On the deprecation path (§5 2026-05-16); `search_kb_hybrid` deliberately doesn't expand queries |
| Drawio codegen `AzureGeneric` constraint | Skip | AST guard (4 lines, deterministic) beats a probabilistic prompt-optimisation fix on the same problem |
| Narration-instead-of-tool-call (`orchestrator.py`) | Skip | Lives in the hot path that §5 2026-04-22 explicitly rules out for frameworks; narration nudge already handles 60–80% |
| Hybrid LLM-judge rerank (`kb/reranker.py`) | Strongest candidate, still skip | Best technical fit (small scope, real metric, eval set exists) but no demonstrated pain; the cheap falsification test (hand-picked few-shots) wasn't run; using `kb_hybrid_eval_50.py` as both training and validation overfits |

## Why DSPy was deferred (not rejected)

1. **No demonstrated user pain on any existing LLM-call site.** Optimising
   what isn't broken violates the principle stated by the user during this
   evaluation: don't bring an ingredient unless you know exactly what it
   does and you need what it does.
2. **Framework creep risk.** DESIGN.md §5 2026-04-22 already rejected
   LangChain/LangGraph because "the loop *is* the product surface."
   Adopting DSPy for one file becomes the precedent for everywhere
   similar — exactly the dynamic that decision was meant to prevent.
3. **Compiled-artefact opacity.** DSPy `BootstrapFewShot` produces an opaque
   JSON of auto-selected few-shot examples that breaks `git diff` and
   `git blame` as review/debugging tools — a permanent review tax.
4. **Re-compile drift is silent.** Model swap, chunker change, or eval-set
   evolution invalidate the compiled artefact with no auto-detection
   (unlike `kb_chunks.embed_model` for embeddings).
5. **The cheap experiment wasn't run.** Hand-picking 5 few-shot examples
   from the eval set and pasting them into the existing prompt is the
   falsification test for the DSPy hypothesis. If it doesn't help,
   `BootstrapFewShot` won't either. That experiment should run *before*
   any DSPy investment is considered.

## Quick fixes that took DSPy's place

The original tracker proposed DSPy as the eventual home for fixes to a
few documented problems. Those fixes now stand independently:

- **AzureGeneric drawio imports**: ship the 4-line AST guard in
  `_validate_ast` standalone — when AzureGeneric recurrence becomes
  user-visible pain. See memory entry `parked-dspy-tool-constraints`.
- **Narration-instead-of-tool-call**: keep the narration nudge
  (`_DEFERRED_ACTION_PATTERN` + `NARRATION_NUDGE_ENABLED`) as the
  permanent fix, not as interim. Widen the regex if a recurring miss
  pattern surfaces. DESIGN.md §5 2026-05-20 documents the mechanism.
- **All other prompt-quality concerns**: address with prompt iteration +
  hand-picked few-shots first. Cheap, reversible, no new dependency.

## When to re-evaluate DSPy

Revisit this conclusion when **Nexus has been in production with
significant scenario coverage** AND at least one of:

1. A specific LLM-call site produces documented user pain that prompt
   iteration + hand-picked few-shot examples cannot fix.
2. A new multi-step LLM pipeline emerges in Nexus where joint optimisation
   across steps would beat per-step tuning, AND a programmable quality
   metric for the end-to-end output exists.
3. Real training data from production conversations is available (with a
   genuine train/test split), not synthetic eval scripts.

Until at least one of these is true, the default answer is "no." Don't
re-run the evaluation from scratch — point at this conclusion.

## Related decisions

- DESIGN.md §5 2026-04-22 — Hand-rolled orchestrator over LangChain / LangGraph (same framework-creep reasoning).
- DESIGN.md §5 2026-05-20 — Orchestrator nudges on narration-instead-of-action (the narration nudge, originally framed as interim until DSPy; now permanent).
- DESIGN.md §5 2026-05-16 — Golden set A/B quality check for search_kb_hybrid vs search_kb_semantic (the eval-set is `kb_hybrid_eval_50.py`, not a training set).
- Memory entries `project-dspy-direction` and `parked-dspy-tool-constraints`.
