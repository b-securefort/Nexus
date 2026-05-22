# Diagrams — Glossary of Abbreviations

> Quick reference for the acronyms and shorthand labels used in the L1 + Drill 1–5 component diagrams. For *Nexus-specific terminology* (Skill, Conversation, Tool, Approval, Question, Compaction, Learning, etc.), see the main [GLOSSARY.md](../GLOSSARY.md).

---

## Identity & access

| Abbrev. | Expansion | What it means in the diagrams |
|---|---|---|
| **Entra ID** | Microsoft Entra ID (formerly Azure AD) | Microsoft's cloud identity provider. Issues the JWT the frontend acquires via MSAL; the `roles` claim in that JWT drives Nexus's RBAC. |
| **JWT** | JSON Web Token | The signed token Entra returns. Carries the user's `oid`, `email`, and `roles` claims that the backend validates on every request. |
| **MSAL** | Microsoft Authentication Library | The frontend library used by [AuthProvider.tsx](../../frontend/src/auth/AuthProvider.tsx) to acquire tokens from Entra ID — both the API-access token and the ARM token. |
| **ARM** | Azure Resource Manager | The Azure control plane (`https://management.azure.com/`). Tools like `az_cli`, `az_resource_graph` ultimately hit ARM. |
| **CV** | ContextVar (Python `contextvars.ContextVar`) | Per-async-task storage. Used to propagate the ARM token and active skill name from the orchestrator into tool-execution threads. |
| **RBAC** | Role-Based Access Control | The mechanism in [app/auth/rbac.py](../../backend/app/auth/rbac.py) that filters which skills + tools a user can see/use based on their Entra App Roles. |

---

## Storage & retrieval

| Abbrev. | Expansion | What it means in the diagrams |
|---|---|---|
| **KB** | Knowledge Base | The corpus of markdown files synced from Git into `kb_data/kb/`. See [GLOSSARY.md](../GLOSSARY.md#kb-knowledge-base). |
| **ADO** | Azure DevOps | A possible KB source (ADO wiki + Repos). The "KB Git repo" node could be ADO, GitHub, or any Git host. |
| **FTS5** | Full-Text Search version 5 | SQLite's built-in full-text index. Backs the `kb_chunks_fts` and `agent_learnings_fts` virtual tables. Tokenizer set to `unicode61 no porter` per [DESIGN.md §5 2026-05-15](../DESIGN.md). |
| **BM25** | Best Matching 25 (ranking function) | The classic keyword-relevance scoring used by FTS5. Produces the keyword-side ranked list that RRF fuses. |
| **vec0** | sqlite-vec virtual-table module | The SQLite extension that owns ANN-friendly storage of embedding vectors. Backs `kb_chunks_vec` and `agent_learnings_vec`. |
| **RRF** | Reciprocal Rank Fusion | The algorithm that merges BM25 and vec0 rank lists into one ordering (`score = sum(1 / (rank + K))`, K=60). See [GLOSSARY.md](../GLOSSARY.md). |
| **WAL** | Write-Ahead Logging | SQLite journal mode enabled on every connection so background re-indexing doesn't block in-flight chat reads. |
| **AOAI** | Azure OpenAI | Shorthand for the Azure-hosted OpenAI service (chat completions + embeddings). |

---

## API surface & runtime

| Abbrev. | Expansion | What it means in the diagrams |
|---|---|---|
| **SSE** | Server-Sent Events | The streaming protocol `POST /api/chat` uses to push token / tool / approval / done events to the frontend on one connection. |
| **LLM** | Large Language Model | The chat-completion model behind Azure OpenAI (`gpt-5.4-mini` per current config). |
| **PAT** | Personal Access Token | Not directly shown but referenced in deployment — Git credentials for KB sync. |
| **CDN** | Content Delivery Network | Where the Devicon SVGs in the diagrams are hosted (jsdelivr). Not part of Nexus runtime. |

---

## Algorithm + code shorthand

| Term | What it means |
|---|---|
| **`_OVERRIDE_PATTERNS`** | Regex constant in [app/agent/learnings.py](../../backend/app/agent/learnings.py) that catches self-poisoning phrasings ("ignore the validator", "skip the check", etc.). Gate 1 of the learnings write defense. |
| **`_ACCESS_MAP`** | In-process dict in [app/auth/rbac.py](../../backend/app/auth/rbac.py) mapping `role → {skills, tools}`. Loaded once at startup from App Configuration (or hardcoded defaults). |
| **`pending_approvals`** | DB table queueing `requires_approval=True` tool calls awaiting user yes/no. |
| **`pending_questions`** | DB table queueing `ask_user` clarifications awaiting user answer. |
| **`skill_snapshot_json`** | JSON column on `conversations` capturing the full skill config at conversation creation. The orchestrator resolves tools from this frozen snapshot, never from the live skill — invariant: changing a skill later cannot change an existing conversation's behaviour. |
| **`_rrf_fuse`** | Function in [app/kb/retrieval.py](../../backend/app/kb/retrieval.py) and [app/agent/learnings.py](../../backend/app/agent/learnings.py) that implements RRF. |
| **`[CANONICAL]` / `[PROVISIONAL]`** | Status markers prepended to retrieved learnings when injected into the system prompt. CANONICAL = `status='active'` (auto-promoted after 3 validations). PROVISIONAL = freshly written, not yet validated. |
| **vec0 `MATCH ORDER BY distance`** | The sqlite-vec query syntax for nearest-neighbour vector search. Used by both `search_kb_hybrid` and `retrieve_relevant_learnings`. |
| **FTS5 `MATCH`** | The SQLite full-text query syntax. Used in BM25 stages. |

---

## Numbers that appear in labels

| Number | What it refers to |
|---|---|
| **`<=15` iters** | Max LLM iterations per chat turn (orchestrator's hard cap). |
| **`ThreadPool(64)`** | Worker count for the lazy-singleton ThreadPoolExecutor that runs tool subprocesses. |
| **`Semaphore(4)/user`** | Per-user concurrency cap — one chatty user can have at most 4 tool calls in flight simultaneously. |
| **`1536`** (in `float[1536]`) | Embedding dimensions for `text-embedding-3-small`. |
| **`15m`** | KB Git sync interval (default `KB_SYNC_INTERVAL_SECONDS=900`). |
| **`auto-promote at 3`** | A provisional learning becomes `active` after 3 successful validations. |
| **`auto-archive at 3`** | An active learning is archived after 3 failures that exceed its validation count. |
| **`>2 KB`** | Threshold above which a tool result is routed through the LLM summariser instead of being passed back raw. |
| **`~14 keys`** | Approximate size of the explicit env-var allowlist `_run_az()` builds for subprocess calls. |

---

## Where to dig deeper

- **[DESIGN.md](../DESIGN.md)** — full architecture document; each diagram's underlying decision is logged in the §5 Decision log.
- **[GLOSSARY.md](../GLOSSARY.md)** — Nexus domain glossary (Skill, Tool, Approval, Learning, etc.).
- **Per-diagram appendices** — every diagram doc in this folder ends with a node-by-node and edge-by-edge explanation.
