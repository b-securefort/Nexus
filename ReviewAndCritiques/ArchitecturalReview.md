# Nexus Agent — Architectural Review

**Reviewer:** Andrej Karpathy (Persona)
**Date:** May 2026

## 1. Overall Rating and Assessment

Nexus is an aggressively pragmatic, 10x-developer style tool. It defers complexity (favoring SQLite over heavy infra) while solving real problems like context-window bloat and LLM memory poisoning. 

**Overall Score:** 8.5/10

### Feature Ratings
*   **Conversation Compaction (9/10):** The asymmetric compaction model (verbatim user messages + collapsed tool scaffolding) is brilliant. It directly attacks the core problem of agent latency.
*   **RAG / KB Retrieval (8/10):** The Phase 2 evolution to `sqlite-vec` with Azure OpenAI 1536-dim embeddings keeps the operational footprint small while providing state-of-the-art semantic retrieval.
*   **Execution & Approvals (8/10):** The `X-ARM-Token` user-identity passthrough is a very smart way to handle RBAC without the complexity of OBO (On-Behalf-Of) flows.
*   **Agent Learnings (7/10):** Moving to an LLM Judge prevents self-poisoning, but introduces a synchronous point of failure in the write path. (See `LearningModuleImprovements.md` for specific resolutions).

## 2. Critical Findings Summary

During the interactive design session, several structural bottlenecks were identified that will break at scale. Separate resolution documents have been created for each:
- **Database Persistence** (SQLite WAL on Azure Files corruption risk)
- **Agent Execution Sandbox** (`run_shell` security vulnerability)
- **Context Window & Latency Optimization** (LLM slowdown on heavy tool turns)
- **Stateful Approvals & Container Restarts** (Chat hangs on pod crash)
- **Concurrency Exhaustion** (Thread pool limits with parallel tools)
