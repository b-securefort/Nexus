# Agent Learnings Module Improvements

Based on the architectural review, the Agent Learnings feature (currently rated 7/10) has a few structural bottlenecks. The following improvements are proposed to elevate it to a 9/10 or 10/10:

## 1. Asynchronous LLM Judge (Remove Synchronous Bottleneck)
**Problem:** The "Three-gate write defense" uses an LLM Judge (`app/agent/learn_judge.py`) to evaluate the learning before saving it. This happens in the orchestrator's critical write path during a chat turn, adding 1-4 seconds of latency to the user's experience.
**Solution:** Move validation to the background using FastAPI's `BackgroundTasks` (or a dedicated task queue). Return the final response to the user immediately, and let the LLM Judge grade the learning and update the SQLite database out-of-band.

## 2. Agent Proposes, Judge Disposes (Restore Agent Nuance)
**Problem:** The orchestrator currently derives the learning automatically from the "success-after-failure" state to prevent poisoning. This loses the agent's nuanced reasoning (e.g., *why* a specific flag was needed to bypass a bug).
**Solution:** Allow the agent to *propose* the learning text internally when a retry succeeds. Pass this proposal to the async LLM Judge. If it violates `_OVERRIDE_PATTERNS` or tries to suppress a validator, the judge drops it. This safely restores nuance without restoring tool-level poisoning.

## 3. Hybrid Retrieval for Learnings (Fix Syntax Matching)
**Problem:** `agent_learnings` retrieval relies solely on dense embeddings (`agent_learnings_vec`). Dense embeddings (`text-embedding-3-small`) are notoriously bad at matching exact error codes, specific CLI flags, or acronyms.
**Solution:** Apply the exact same Reciprocal Rank Fusion (RRF) and FTS5 (keyword search) setup used for the Knowledge Base to the `agent_learnings` table. This ensures exact matches (like `AuthorizationFailed`) instantly retrieve the correct learning via BM25, while retaining semantic matching capabilities.

## 4. Human-in-the-Loop Curation UI
**Problem:** The SQLite table will fill with hundreds of learnings over time. Obsolete learnings (e.g., from patched bugs) will artificially constrain the agent's behavior if they are continually retrieved.
**Solution:** Build a simple "Learnings Admin" tab in the React frontend (via a new `/api/learnings` endpoint). This allows senior engineers to audit, edit, and delete outdated learnings, keeping the "hive mind" memory clean and relevant.
