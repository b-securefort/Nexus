# Nexus — Architecture & Design

> **Reading this**: a moderator should be able to read this doc in 15 minutes
> and answer "what does Nexus do, how is it built, why these choices, what
> depends on what." If something is unclear after this doc, that's a bug in
> the doc — open a PR.

## 0. How to update this document

This is a **living document**. Every PR that changes architecture, dependencies,
tools, data model, or makes a non-obvious decision MUST update the relevant
section in the same PR. The PR template has a checkbox for this.

Update protocol:

| Change | Update section |
|---|---|
| New Python/JS dependency | §3 Dependencies — add a row; explain *why this one* |
| New table or column | §4 Data model |
| New tool exposed to the agent | §2 Components → Tools table |
| Architectural / design decision worth defending later | §5 Decision log — date, decision, why, trade-offs |
| New operational concern (background task, env var) | §6 Operations |
| Item retired or replaced | strike-through with a §5 entry pointing to the replacement |

If a change makes an old decision obsolete, **don't delete the old entry** —
add a new §5 entry referencing it. The history is the value.

---

## 1. What Nexus is

Nexus is a self-hosted AI assistant for any IT team. It combines an LLM
(Azure OpenAI) with a team knowledge base (KB) synced from Git, a switchable
"skills" system (named personas with scoped toolsets), and approval-gated
execution of real tools (`az` CLI, PowerShell, Azure Resource Graph queries,
Azure REST). Unlike a chat-only assistant, Nexus *runs commands* — it learns
from mistakes via a persistent `learn.md`, retries failed commands with
three different strategies, and produces architecture diagrams alongside
text answers.

```
┌─────────────┐    chat (SSE)     ┌─────────────────────────────┐
│  Frontend   │ ────────────────► │   Backend (FastAPI)         │
│  React+Vite │ ◄──── tokens ──── │   ┌─ Orchestrator (agent)─┐ │
└─────────────┘                   │   │  Compaction           │ │      ┌──────────┐
                                  │   │  Tool execution       │ │ ───► │  Azure   │
       ┌─ KB sync ─►┌───────────┐ │   │  Approvals / askuser  │ │      │  OpenAI  │
       │            │ kb_data/  │ │   └───────────────────────┘ │      └──────────┘
       │            │ kb/*.md   │ │   ┌─ Tools ────────────────┐│      ┌──────────┐
   ┌───────┐        │ skills/   │ │   │  az_*, run_shell,      ││ ───► │  Azure   │
   │ ADO   │        │ learn.md  │ │   │  read_kb_file,         ││      │  CLI /   │
   │ wiki  │        └───────────┘ │   │  search_kb_*,          ││      │  ARM     │
   │ + git │ ──────►               │   │  generate_file,        ││      └──────────┘
   │ + pdf │       (ingest/        │   │  ms_docs, learnings    ││
   └───────┘        runner.py)     │   └────────────────────────┘│
                                  │   ┌─ SQLite app.db ────────┐ │
                                  │   │  users, conversations, │ │
                                  │   │  messages, approvals,  │ │
                                  │   │  questions, kb_chunks  │ │
                                  │   └────────────────────────┘ │
                                  └─────────────────────────────┘
```

---

## 2. Components

### Chat orchestrator
**Files**: [backend/app/agent/orchestrator.py](../backend/app/agent/orchestrator.py)

The agent loop. Receives a user message via SSE, composes the system prompt
(skill prompt + tool hierarchy + retry policy + KB index summary + learnings
+ Azure context + pinned original-task block), calls Azure OpenAI streaming,
executes any tool calls (with approval gates and ask-user prompts), feeds
results back, and loops up to 15 iterations. Tool failures trigger a
multi-strategy retry escalation; success-after-failure prompts a learning
record. Every main-loop call carries per-skill decoder tuning
(`reasoning_effort` / `verbosity`, resolved once per turn by
`_resolve_tuning_kwargs` — see §2 Skills system); the iteration-cap
wrap-up call forces `reasoning_effort=low` since it only summarizes the
turn.

### Conversation compaction
**Files**: [backend/app/agent/compaction.py](../backend/app/agent/compaction.py)

Solves the "context window bloats during long tool-heavy turns and the agent
forgets the original ask" problem. Asymmetric strategy: **every user message
is preserved verbatim** (with two exceptions cached on the `messages` row:
long pastes > 3 KB get a high-quality summary in `text_summary`; older
images get a vision-LLM description in `image_summary`; the latest image
always stays as a real image). **Assistant + tool scaffolding between user
messages** is the part that gets compressed — each gap collapses into one
synthetic `[Outcomes from intermediate tool work]` bullet message. Recent
N messages stay verbatim. Cumulative outcome cache lives on
`Conversation.summary_text` so re-summarization is paid only once per
compression event.

### KB retrieval — two parallel paths (Phase 2 shipped)
**Files**: [backend/app/kb/](../backend/app/kb), [backend/app/tools/generic/kb_tools.py](../backend/app/tools/generic/kb_tools.py)

Path A (existing, cloud): `search_kb_semantic` — keyword index + Azure OpenAI
query expansion + Azure OpenAI rerank. File-level results. Stays as-is until
Path B is validated against real content.

Path B (local hybrid, shipped): `search_kb_hybrid` — markdown chunked at H2/H3
boundaries, SQLite FTS5 (BM25) + sqlite-vec (cosine over **1536-dim** Azure OpenAI
`text-embedding-3-small` embeddings), Reciprocal Rank Fusion. No local ONNX
models, no cross-encoder reranker. Runs on-device except for the single
Azure OpenAI embed call per query (~50 ms). Returns chunk-level results with
`source_url` cite. Falls back to keyword search (same result schema) while the
index is warming on first start.

### KB ingestion (Phase 2a, shipped)
Pulls content from ADO wikis, ADO repos (already handled by `git_sync.py`),
and PDF link lists (SharePoint / open web). Normalizes everything to
markdown with front-matter (`source_url`, `last_synced`, `source`,
`original_path`). Pilot scope: 50-200 documents.

### KB content layout and file-level index
**Files**: [backend/app/kb/indexer.py](../backend/app/kb/indexer.py)

KB content lives at `<KB_REPO_LOCAL_PATH>/kb/` — a fixed subdirectory under the
synced KB repo root. The indexer recursively scans `kb/**/*.md` and produces
the in-memory `_index` that drives `search_kb` (the keyword search tool) and
the **KB summary block** the orchestrator injects into every system prompt.
The summary lists `path — title — summary (tags)` for every file the agent
can read, so the agent knows the inventory without having to query.

`<KB_REPO_LOCAL_PATH>/kb_index.json` is **optional curator metadata**, not the
source of truth. When present, its `summary` and `tags` fields are merged
onto matching disk-scanned entries; files not curated in the json still
appear in the index with a minimal entry (title from first H1, empty summary).
Curated entries pointing at non-`.md` files (e.g. `.drawio` reference
patterns) are kept if the file exists on disk. Curated entries pointing at
files that no longer exist are logged as drift warnings at startup and
skipped — so a team that deletes content but forgets to update the json
notices, but the live index stays consistent with reality.

Implication for the [§5 2026-05-15 inner-source fork model](#5-decision-log):
a team that adopts Nexus and commits `.md` files into their forked KB repo
gets a working `search_kb` and KB summary immediately — `kb_index.json`
curation is enrichment, not a gate. The `kb_chunks` table (Phase 2 hybrid
retrieval) is rebuilt from disk by `reindex_all()` on the same schedule, so
both indexes stay consistent across periodic git syncs (`load_index()` is
called after every `sync_repo()` + `reindex_all()` cycle, not just at
startup).

### Skills system
**Files**: [backend/app/skills/](../backend/app/skills/), `kb_data/skills/shared/<skill>/SKILL.md`

A skill is a YAML-frontmatter markdown file specifying a `display_name`,
`description`, `system_prompt`, and a `tools:` allowlist. Switching skills
swaps the agent's persona and scoped toolset. Personal skills live in the
`personal_skills` table; shared skills live in the synced KB repo.

Optional decoder-tuning frontmatter (2026-06-12): `reasoning_effort:`
(`minimal|low|medium|high`) and `verbosity:` (`low|medium|high`) are passed
to the main-loop chat completion call. Unset keys fall back to the
`CHAT_REASONING_EFFORT` (default unset = model default) and `CHAT_VERBOSITY`
(default `low`) config settings; an empty effective value omits the parameter
entirely (pre-gpt-5 deployments reject it with a 400). Invalid frontmatter
values are dropped with a WARNING, not a load failure. Both fields are
carried in the conversation's skill snapshot, so older snapshots (keys
absent) inherit the config defaults. Current assignments: `kb-searcher`
runs at `low` effort (read-only Q&A); both architect skills set
`verbosity: medium` so ADR-length deliverables aren't clipped by the
global `low`.

### Tools

| Tool | Approval | Purpose |
|---|---|---|
| `read_kb_file` | No | Read a KB file by relative path |
| `search_kb` | No | Token-scored search over titles/summaries/tags |
| `search_kb_semantic` | No | **Cloud** path: Azure-OpenAI query expansion + rerank over file-level index. Kept side-by-side with `search_kb_hybrid`. |
| `search_kb_hybrid` | No | **Local** path: chunked hybrid retrieval, one embed call per query — preferred over `search_kb_semantic` |
| `fetch_ms_docs` | No | Microsoft Learn doc search — the correct tool for `learn.microsoft.com` content (see `web_fetch`) |
| `az_resource_graph` | No | KQL queries against Azure Resource Graph |
| `az_cost_query` | No | Cost Management API queries |
| `az_monitor_logs` | No | Log Analytics KQL queries |
| `az_advisor` / `az_policy_check` | No | Advisor recs and policy compliance |
| `az_cli` | **Yes** | General Azure CLI commands |
| `az_rest_api` | GET=No / mutations=Yes | Direct ARM REST calls |
| `az_devops` | Read=No / mutations=Yes | ADO pipelines/PRs/builds |
| `execute_script` | **Yes** | Run a `.ps1`/`.sh` script that already exists under `output/scripts/`. Path-only — no inline command surface. |
| `network_test` | No | DNS / TCP / ping diagnostics |
| `generate_file` | No | Write artifacts (bicep, csv, etc.) to `output/` sandbox |
| `read_file` | No | Read a file from the `output/` sandbox (symmetric with `generate_file`) |
| `validate_drawio` / `render_drawio` / `patch_drawio_cell` | No | Diagram authoring + validation |
| `generate_python_diagram` / `generate_drawio_from_python` | No | Diagram-as-code → drawio |
| `web_fetch` | No | HTTP GET + text extraction for a *known* URL. Returns `Error` on JS/auth-wall stubs (no JS rendering); short-circuits `learn.microsoft.com` to `fetch_ms_docs` |
| `web_search` | No | General web search via DuckDuckGo (`ddgs`), optional `site:` scoping — Reddit, Tech Community, blogs not covered by the other search tools. Returns a ranked list of links + snippets (not page content; pair with `web_fetch`) |
| `search_github` / `search_stack_overflow` | No | Targeted GitHub and Stack Overflow search |
| `search_azure_updates` | No | Azure service-update / roadmap announcement search |
| `ask_user` | No (pauses for UI) | Surface options to the user via the UI; resumes on answer |
| `sleep` | No | Pause the agent loop 1–120s, then continue, so the model can wait out a rate-limit/throttle window and retry the same action. Blocks a tool-executor thread (occupies one concurrency slot as backpressure); capped per call so each wait is a visible step |
| `search_conversation` | No | Keyword search over the **current** conversation's full stored `messages` rows — the recovery path for details compaction evicted or truncated from the window. Conversation-scoped via the request ContextVar; cannot read another conversation |

#### Tool failure-signalling contract (load-bearing)

The orchestrator decides whether a tool call **failed** by inspecting the
returned string: a result that begins with `Error` (or a JSON envelope with
`status: "error"`) is a failure; anything else is success (`_tool_control_outcome`
in `orchestrator.py`). That single signal (`is_error`) drives three behaviours:
multi-strategy **retry**, the **success-after-failure learning** capture, and the
`nexus_tool_calls_total{outcome}` **metric**.

The trap: a tool that completes but whose *operation* failed, yet returns a
non-`Error` string, is silently treated as **success** — no retry, no learning,
mislabelled metric. This was violated three ways and fixed 2026-06-04: `az_cli`
and `execute_script` returned a bare `Exit code: 2` on non-zero exit, and
`web_fetch` returned a JS/auth-wall stub with HTTP 200. **Every tool that can
fail must return an `Error`-prefixed string (or `status:error`) on failure** —
`AzureToolBase._run_az()` already does this (`Error (label) [exit N]: …`), which
is why `az_resource_graph` and the other base-runner tools never had the bug.
A new hand-rolled `subprocess`/`httpx` tool that forgets this will have its
failures silently swallowed.

### Auth
**Files**: [backend/app/auth/](../backend/app/auth/)

Microsoft Entra ID JWT validation. `DEV_AUTH_BYPASS=true` in dev short-circuits
this to a fake `dev-user` identity so local development doesn't need a tenant.

**User-identity Azure passthrough**: The frontend acquires a second MSAL token
scoped to `https://management.azure.com/user_impersonation` and sends it as
`X-ARM-Token`. The auth layer extracts and light-validates it (audience check +
tenant check, no signature re-verification), attaches it to `User.arm_token`.
The orchestrator calls `set_arm_token(user.arm_token)` at the start of every
chat turn, setting a `ContextVar` that `AzureToolBase._run_az()` and
`AzCliTool` read to inject `AZURE_ACCESS_TOKEN` into every subprocess env.
Result: all Azure tool calls (`az_cli`, `az_resource_graph`, `az_cost_query`,
`az_monitor_logs`, `az_rest_api`, `az_advisor`, `az_policy_check`, `az_devops`,
`network_test`) run as the signed-in user, not the server identity.
If the token is absent, behaviour depends on the environment (see §5
2026-06-01). The orchestrator probes for `CONTAINER_APP_NAME` — set by Azure
Container Apps in every replica — to detect a deployed environment. When
running locally (env var unset) tools fall back to whatever credentials are in
the server's local `az` CLI session — no error, just no user identity. When
deployed (env var set) a missing token is a **hard stop**: the B3 pre-flight
short-circuits the Azure tool call and tells the user to sign in, rather than
silently running as the server identity. Expired / near-expiry tokens always
drive the `token_refresh_required` MSAL silent-refresh flow regardless of
environment.

### Frontend
**Files**: [frontend/src/](../frontend/src/)

React 19 + Vite + Tailwind v4 + Zustand + React-Query. SSE consumer for chat
streaming. Conversation list, skill picker, approval cards, ask-user question
cards, message bubbles with image attachments.

---

## 3. Dependencies

Only the *non-obvious* deps and *why this one specifically* — not a copy of
`requirements.txt`. If a dep is "the standard for X" we still record why
it's a fit for our constraints (Windows-supported, single DB, local-only
retrieval, etc.).

| Library | Used in | Why this one (vs alternatives) |
|---|---|---|
| `fastapi` | Backend HTTP | SSE streaming is first-class, async-native, type hints flow into OpenAPI |
| `sqlmodel` | ORM | Sits on SQLAlchemy + Pydantic — one model class drives table + API schema |
| `sqlite` (stdlib) | DB | Single-file, no infra, WAL mode supports our concurrency. Backup is `cp app.db`. |
| `openai` | Azure OpenAI client | Official; supports streaming + tool calling with the OpenAI tool-call schema |
| `gitpython` | KB git sync | Wraps system git; same auth options as the CLI |
| `msal` | Entra auth | Microsoft's official ID library — required for parity with prod IT policy |
| `pydantic-settings` | Config | Env-var-first config with type validation |
| `prometheus-client` | Metrics | Standard scrape target; aligns with the rest of the Azure shop |
| `pypdf` *(Phase 2a)* | PDF text extraction | Pure-Python, no Java/Tesseract, sufficient for born-digital PDFs which is what we have |
| `sqlite-vec` *(Phase 2)* | Local vector search | Runs as a SQLite extension *in the existing app.db* — no separate vector DB, no Node runtime. Ships Win/Linux wheels. |
| `numpy` *(Phase 2)* | Vector math | L2-normalise the 1536-dim embeddings returned by Azure OpenAI before storing in vec0 (cosine similarity requires unit vectors) |
| `diagrams` | python_diagram tool | Renders Azure architecture diagrams as code; only used by the diagram-authoring tools |

**Rejected (and why)** — short list of options we considered and didn't take:
- `langchain` / `langgraph` — would have abstracted the agent loop but most of what makes Nexus distinctive (approvals, ask_user, SSE protocol, multi-strategy retry, compaction) would live in custom callbacks or subclasses anyway. See §5 2026-04-22 "Hand-rolled orchestrator" entry.
- `qmd` — Node.js + 2 GB GGUF models. Too much infra for what `sqlite-vec` + Azure OpenAI embeddings give us in Python.
- `onnxruntime` / `sentence-transformers` / local ONNX models — considered for bge-small-en-v1.5 embeddings, rejected: bge-small (384 dim) is lower quality than `text-embedding-3-small` (1536 dim); local models add ~412 MB of download and an air-gapped install procedure; Azure OpenAI is already our trusted endpoint for every chat turn. See §5 "Azure OpenAI text-embedding-3-small" decision.
- `Mem0` — paid cross-session memory service. Violates the local-only constraint and we already have `learn.md` for system-wide mistake memory.
- `Faiss` — better at billions of vectors, but separate-file index and harder ops. We're at thousands of chunks.

---

## 4. Data model

All in `backend/app.db` (SQLite). Schema changes go in
`_apply_lightweight_migrations` in [main.py](../backend/app/main.py) only —
add the column to the SQLModel model and add an `ALTER TABLE` guard in that
function. The shim runs on every startup and is a no-op on fresh installs
(`SQLModel.metadata.create_all` handles those). Alembic migration files exist
in `backend/app/db/migrations/versions/` for reference but are not the active
migration path (see §5 decision log).

| Table | Columns (short) | Who writes | Who reads | Retention |
|---|---|---|---|---|
| `users` | oid, email, display_name, last_seen_at, credit_cap_usd | auth middleware; admin API (`credit_cap_usd`) | everywhere | forever |
| `conversations` | user_oid, title, skill_id, skill_snapshot_json, summary_text, summary_through_message_id | api/conversations, compaction | orchestrator | until user deletes |
| `messages` | conversation_id, role, content, tool_calls_json, tool_call_id, attachments_json, text_summary, image_summary | orchestrator, compaction | orchestrator (history + compaction) | until conversation deleted |
| `pending_approvals` | tool_name, tool_args_json, reason, status | orchestrator | api/chat | expire via sweeper after 10 min |
| `pending_questions` | conversation_id, questions_json, status, answers_json | orchestrator (ask_user) | api/chat | expire via sweeper |
| `personal_skills` | user_oid, name, system_prompt, tools_json | api/skills | skills loader | until user deletes |
| **`kb_chunks`** *(Phase 2)* | kb_path, chunk_idx, heading, text, content_hash, file_mtime, source_url, embed_model | KB reindexer | search_kb_hybrid | until source file removed/changed |
| **`kb_chunks_fts`** *(virtual)* | FTS5 over `kb_chunks.text + heading`, `tokenize=unicode61` | triggers on `kb_chunks` | search_kb_hybrid (BM25 stage) | n/a |
| **`kb_chunks_vec`** *(virtual)* | vec0(float[1536]), joined by rowid==kb_chunks.id — 1536 dims matches Azure OpenAI `text-embedding-3-small` | reindexer (explicit) | search_kb_hybrid (vector stage) | n/a |
| **`agent_learnings`** | type (semantic\|procedural), category, tool_name, summary, details, status (provisional\|active\|archived\|rejected), validation_count, failure_count, judge_verdict_json, originating_conversation_id, content_hash, embed_model, last_validated_at, last_retrieved_at | orchestrator success-after-failure path (via `app/agent/learnings.py`) | `retrieve_relevant_learnings` (system-prompt build), `mark_learning_outcome` (post-tool-call) | active forever; archived rows retained for audit |
| **`agent_learnings_vec`** *(virtual)* | vec0(float[1536]), joined by rowid==agent_learnings.id — same Azure OpenAI embedding deployment as KB | `learnings.reembed_dirty()` (inline after writes + lifespan sweep) | `retrieve_relevant_learnings` (vector stage) | n/a |
| **`usage_events`** *(spend ledger)* | user_oid, conversation_id, deployment, prompt_tokens, cached_tokens, completion_tokens, created_at — one append-only row per LLM call; per-user spend is a windowed `SUM`, dollars derived at read time from a config price table (tokens+deployment stored, not dollars) | orchestrator (per LLM call, on `done`) | pre-flight cap gate + per-iteration check; admin/reporting API | **prune > 90 days** (sweeper) |

WAL mode is enabled on every new SQLite connection by
[sqlite_vec_loader.py](../backend/app/db/sqlite_vec_loader.py) so periodic
KB re-indexing doesn't block in-flight chat reads.

---

## 5. Decision log

A chronological record. Newest decisions at the **bottom**. Each entry: date,
decision, why, trade-offs accepted.

### 2026-04-22 — Hand-rolled orchestrator over LangChain / LangGraph

Built the agent loop directly on the OpenAI SDK + FastAPI streaming when the
orchestrator was first scaffolded, rather than wrapping LangChain `AgentExecutor`
or LangGraph. The loop *is* the product surface — approval gating, `ask_user`
DB-persisted pauses, the typed SSE event protocol, multi-strategy retry with
docs lookup, prompt-cache-aware system-prompt layout, orphan-safe history
reconstruction, and ARM-token `ContextVar` propagation are all behaviours we
tune turn-by-turn; a framework would either re-implement them in callbacks or
accept defaults that erase those differentiators.
**Trade-off**: no free LangSmith tracing, no LangGraph checkpointing, hand-rolled
compaction and retries. Accepted because Nexus is single-process internal tooling
at ~1200 orchestrator lines; if multi-agent graph workflows become a requirement,
LangGraph is the place to revisit, not LangChain itself.

### 2026-05-13 — Pin the original user task in the system prompt
Long tool-heavy turns push the original user message past the 50-message
history window or compress it away. We append a fixed `[Original task from
user]` block to the system prompt every turn, drawn from the conversation's
first user message. Capped at 2000 chars to bound the prompt.
**Trade-off**: a small fixed system-prompt cost on every turn; in exchange,
the agent reliably stays on-task across long iterations.

### 2026-05-13 — Conversation compaction: summarize older messages with a Conversation-row cache
When history exceeds 30 messages or ~12 KB, summarize the older half into a
single synthetic assistant message. Cache the summary on the Conversation row
(`summary_text` + `summary_through_message_id`) so we don't re-summarize from
scratch every turn.
**Trade-off**: one extra Azure OpenAI call when the cache is invalidated; in
exchange, dramatic reduction in prompt size on long conversations.

### 2026-05-14 — Asymmetric compaction: preserve all user messages, only compress scaffolding
**Replaces the 2026-05-13 "summarize the older half" decision.** User
messages are short (20-200 tokens, high info density, anchor intent); tool
outputs are long (thousands of tokens, mostly noise after the conclusion is
reached). Compress asymmetrically: every user message stays verbatim; the
assistant+tool scaffolding between consecutive user messages collapses into
one `[Outcomes from intermediate tool work]` bullet. Long pastes (>3 KB) and
older images on user messages get cached `text_summary` / `image_summary`
columns. The latest image-bearing user message keeps its actual images.
**Trade-off**: more total tokens preserved (because every user message
survives), but the agent never loses sight of *what the user asked for*
across long turns. The model-visible prompt remains compact because tool
output is what gets compressed.

### 2026-05-14 — Local hybrid KB retrieval (Phase 2) over qmd
**Chose** `sqlite-vec` + `onnxruntime` in Python over the qmd Node.js
runtime. qmd's architecture (BM25 + dense vectors + reranker) is correct;
its runtime cost (Node ≥ 22 + ~2 GB GGUF models) isn't justified when we
can have the same architecture in our existing Python stack with a 220 MB
install.
**Trade-off**: one-time ONNX export work; in exchange, single-language
stack, single DB, no extra process.

### 2026-05-14 — Skipped OCR for PDF ingestion (Phase 2a)
The target PDF corpus (~100 docs) is all born-digital text. `pypdf` is
sufficient. **Trade-off**: scanned PDFs (if any later appear) will produce
empty/garbage content; we'd add Tesseract OCR then.

### 2026-05-14 — Keep cloud `search_kb_semantic` alongside new local `search_kb_hybrid` during POC
Don't delete the existing cloud path. Run both in parallel; eyeball top-3
agreement on a hand-picked golden set before retiring the cloud path.
**Trade-off**: two tools the agent sees in skills' tool lists during the
POC — minor cognitive load.

### 2026-05-15 — `unicode61 remove_diacritics 2` FTS5 tokenizer, no porter stemming
Porter stemming truncates "kubernetes" → "kubernet", "azure" → "azur",
hurting matches against technical jargon. `unicode61` plain (with diacritic
folding) preserves the distinctive token. **Trade-off**: slightly less recall
on natural-language morphology variations; acceptable for a technical KB.

### 2026-05-15 — `embed_model` column on `kb_chunks`
If we ever swap embedding models (different name or different dim), the
existing chunks become invalid. Storing the model name per row lets the
reindexer auto-detect the swap and force-reembed without manual DB cleanup.
**Trade-off**: a few extra bytes per row.

### 2026-05-15 — `kb_chunks_vec` is a sqlite-vec `vec0` virtual table, joined by rowid
Rejected the alternative of storing the embedding as a BLOB column on
`kb_chunks` directly, because querying nearest neighbors on a BLOB column
requires a full table scan + `vec_distance_cosine()` in Python. vec0 owns
its own ANN-friendly layout and supports `MATCH '[...]' ORDER BY distance`
natively. **Trade-off**: the reindexer must explicitly write to both
tables (FTS triggers handle their own sync, but vec0 doesn't because
embeddings are computed in Python).

### ~~2026-05-15 — Conditional cross-encoder reranking~~
**Superseded by the 2026-05-15 "Azure OpenAI text-embedding-3-small" decision.**
The planned `bge-reranker-base` cross-encoder was dropped: with 1536-dim
`text-embedding-3-small` embeddings, BM25 + vector + RRF ranking quality at
pilot corpus scale is sufficient without an extra reranker pass. The
`KB_RERANK_CONFIDENCE_GAP` config setting was removed. If retrieval quality
proves insufficient at larger corpus scale, a reranker can be added then.

### 2026-05-15 — Living `Documentation/DESIGN.md` is the source of truth for architecture
This document. Update lives in the same PR as the code change. PR template
has a checkbox. **Trade-off**: small PR-author overhead; in exchange, the
project remains explainable to a new moderator without re-reading commits.

### 2026-05-15 — Removed deploy-backend / deploy-frontend / local-runner / python-diagrammer skills
These four Nexus skills were self-referential: they described how to deploy or
run *Nexus itself*, which is IDE work, not agent work. Replaced by three
`.claude/commands/` files (`deploy-backend.md`, `deploy-frontend.md`,
`run-local.md`) that Claude Code picks up as `/deploy-backend`,
`/deploy-frontend`, `/run-local` slash commands. `python-diagrammer` was
superseded by `drawio-from-python` which produces an editable `.drawio` rather
than a static PNG. **Trade-off**: the commands are only accessible inside
Claude Code IDE, not through the Nexus chat UI — acceptable because deploying
Nexus is always a developer action, never a user action.

### 2026-05-15 — Tool auto-registration via `__init_subclass__`
Every `Tool` subclass that defines a `name` attribute is automatically inserted
into `TOOL_REGISTRY` at class-definition time via Python's `__init_subclass__`
hook — no explicit `register_tool()` call required. This means importing a
tool module is the act of registration; `init_tools()` just imports all modules
under `app/tools/`.
**Trade-off**: magic registration is non-obvious to contributors (a new tool
"appears" without an explicit step), but it eliminates a whole class of bugs
where a tool is implemented but never wired in. The alternative — an explicit
registry list — drifts out of sync in practice.

### 2026-05-15 — Shell injection blocks only backtick and NUL, not pipe/ampersand
`check_shell_injection()` in `base.py` deliberately allows `|`, `&`, `;`, `<`,
`>`, and `$` in tool arguments. These characters are valid inside KQL queries,
JSON bodies, and file paths; blocking them broke `az_resource_graph` and
`az_monitor_logs`. Only backtick (PowerShell escape character) and NUL (C
string truncator) survive `list2cmdline()` quoting and are blocked.
**Trade-off**: narrower injection surface than a traditional blocklist, but
the primary protection is `subprocess` with a list argument (not a shell
string), which quotes each element before cmd.exe sees it.

### 2026-05-15 — Hardcoded blocked prefixes in `az_cli` bypass the approval gate
Six `az` subcommand sequences (`account clear`, `ad app/sp create/delete`,
`role assignment/definition delete`) are permanently rejected in `_is_blocked()`
even after the user grants Approval. These operations can wipe credentials or
lock the team out of Azure — consequences that cannot be undone by the
next approval cycle.
**Trade-off**: the approval gate is bypassed for these specific operations,
which slightly undermines the "approval covers everything" mental model. The
alternative — trusting a user to deny a credential-wipe approval under pressure
— was judged unacceptable.

### 2026-05-15 — Learning override-pattern guard prevents agent self-poisoning
`update_learnings` rejects entries whose text matches `_OVERRIDE_PATTERNS` — a
regex that catches phrases like "ignore the validator", "too noisy", "skip the
check". The same filter is applied at read time to strip entries that slipped
through. Without this, the agent can observe a tool hint it dislikes, write
"ignore that hint" as a learning, and on the next run the system prompt
silently suppresses the tool's guidance.
**Trade-off**: the agent cannot record factually correct observations about
tool behaviour if phrased as "ignore X" — it must rephrase them as "X flags Y
when condition Z". This is intentional friction.

### 2026-05-15 — Output sandbox defense-in-depth with path-traversal regex
`generate_file` and the diagram tools restrict writes to `backend/output/`.
Beyond the `Path.resolve().relative_to(sandbox)` check, a regex
(`_DANGEROUS_PATTERNS`) rejects `..`, absolute paths, and shell-special
characters in filenames before any filesystem call is made.
**Trade-off**: two overlapping checks for the same invariant. The regex is
the fast path that catches obvious attacks without touching the filesystem;
the `relative_to` check is the definitive guard. Belt and suspenders is
intentional here — a path-traversal in a file-write tool is high severity.

### 2026-05-15 — SSE event protocol: typed events over a single stream
The `POST /api/chat` endpoint emits multiple distinct event types rather than a
single `data` stream, so the frontend can render approval gates, question
cards, tool status, and streaming text from one connection without polling.
Each event carries a `type` discriminator and a `data` payload; the frontend
switches on `type` to decide whether to append a token, show an approval
card, or mark a tool as running. The current set is defined in
`app/agent/streaming.py` and listed in GLOSSARY.md.
**Trade-off**: multiple event types are more surface area than a simple
`token`/`done` pair, but the alternative — polling separate endpoints for
approval and question state — introduces race conditions and extra round-trips.

### 2026-05-15 — Single schema migration path: lightweight startup shim only

Schema changes are applied exclusively via `_apply_lightweight_migrations` in
`main.py`, which runs on every startup and is a no-op on fresh installs
(`SQLModel.metadata.create_all` handles those). Alembic migration files exist
in `db/migrations/versions/` but are not the active path.
**Trade-off**: no rollback support and no version-tracking across deployments;
accepted because Nexus is a self-hosted internal tool with a single DB file
and infrequent schema changes. If multi-instance deployment or complex
data-transform migrations arise, Alembic can be reinstated as the active path.

### 2026-05-15 — Tool bundles within one repo for internal team separation

Azure-specific tools are grouped under `app/tools/azure/` and loaded only
when `TOOL_BUNDLE_AZURE_ENABLED=true` in `.env`. Generic tools
(`app/tools/generic/`) are always loaded. Future team bundles (`aws/`,
`ad/`, `dns/`) follow the same pattern: add a directory, add an enable
flag, PR into core. All teams in this organisation have access to the core
repo, so separate tool repositories were considered and rejected: they would
require core to be a versioned pip package, a compatibility matrix between
core and tool-repo versions, and separate CI pipelines — significant overhead
for a problem solvable with a directory and one env flag.
**Trade-off**: adding a new team's tools requires a PR to the core repo;
acceptable because tool implementations change infrequently compared to
skills and KB content, which teams already own via their KB Git repo.

### 2026-05-15 — Inner-source fork model for multi-team adoption

Internal teams adopt Nexus by forking the repo and adding their tools
exclusively under `app/tools/<teamname>/`. Core files (`app/tools/generic/`,
`app/agent/`, `app/api/`, `app/db/`, `app/kb/`) are never modified in forks.
This keeps the upstream merge surface clean — a team's bundle directory
does not exist in the upstream repo, so `git pull` never touches it.
Teams pull upstream when they want core improvements; their private tools
and KB repo remain unaffected. A central plugin registry was considered
and rejected: it requires core to be a versioned package and adds a
compatibility matrix not justified for internal teams.
**Trade-off**: teams own their fork's operational burden; core improvements
are opt-in (pull), not automatic. Acceptable for an internal tool where
teams control their own deployment cadence.

### 2026-05-15 — bge-small asymmetric query prefix for hybrid retrieval

When searching the KB, the embedder adds a special instruction prefix to the
**query** text before computing its vector, but **not** to the document (chunk)
text that was stored at index time. The prefix is:
`"Represent this sentence for searching relevant passages: "`.

**Why this matters for beginners**: an embedding model turns text into a list of
~384 numbers (a "vector") that encodes its meaning. The closer two vectors are
(cosine distance), the more semantically similar the texts. The bge-small model
was trained on pairs of (query, relevant-document) where the query always had
this prefix and the document never did — this is called *asymmetric* prompting
because query and document are treated differently. If you use the same prefix
for both (or no prefix for either), the model still produces plausible-looking
numbers but retrieval quality drops measurably (~3 MTEB benchmark points). This
is not obvious from the model name or the ONNX file — it is documented in the
model's README on HuggingFace.

**Trade-off**: the embedder must know *at call time* whether it is embedding a
query or a document and branch accordingly. A future model swap must verify
whether the replacement model also uses asymmetric prompting, uses a different
prefix, or expects the same text for both sides — this check belongs in the
PR that changes `AZURE_OPENAI_EMBED_DEPLOYMENT`.

### 2026-05-15 — Azure OpenAI text-embedding-3-small for KB hybrid retrieval, no local ONNX models

**Partially replaces the 2026-05-14 "Local hybrid KB retrieval over qmd" decision** for
the embedding component. The overall architecture (sqlite-vec FTS5 + vec0 + RRF) is
unchanged; only the embedding source and the reranker decision change.

**What changed and why** (explained for a reader unfamiliar with ML):

*Embeddings* are lists of numbers that encode the *meaning* of a piece of text —
similar texts produce similar numbers, which lets us find relevant KB chunks even
when they share no keywords with the search query. The original plan used a local
ONNX model (`bge-small-en-v1.5`, 384 numbers per text, downloaded from HuggingFace)
to produce these numbers entirely on-device. We switched to Azure OpenAI's
`text-embedding-3-small` API, which produces 1536 numbers per text and is measurably
higher quality. Since Azure OpenAI is already our trusted endpoint for every chat
turn, sending KB chunks to it for indexing introduces no new third-party service
and no new data-residency concern.

*Cost*: indexing the full corpus (~1000 docs) costs ~$0.15 one time; incremental
re-indexing (only changed files) costs ~$0.01–$0.05/month. Per-query embedding
(one API call, ~30 tokens) costs ~$0.000001. Total is negligible compared to
chat-completion spend.

*Reranker dropped*: the original plan included a cross-encoder reranker
(`bge-reranker-base`, 279 MB ONNX) to correct ranking mistakes made by the smaller
bge-small embeddings. With `text-embedding-3-small` at 1536 dims, the base ranking
from BM25 + vector + RRF is already high enough quality at pilot corpus scale
(hundreds to low-thousands of chunks) that the reranker pass is unnecessary.
It can be added later if retrieval quality proves insufficient.

**Trade-offs accepted**:
- KB indexing requires an active Azure OpenAI connection (no offline re-indexing).
- Each `search_kb_hybrid` call adds one Azure OpenAI embedding API call (~50 ms).
- `onnxruntime`, `tokenizers`, `huggingface-hub` are removed from requirements
  (saves ~412 MB of model files and the air-gapped install procedure).
- `kb_chunks_vec` is now `float[1536]`; changing to a different dimension later
  requires dropping and recreating the virtual table (all embeddings lost, full
  re-index required).

### 2026-05-16 — Golden set A/B quality check: search_kb_hybrid vs search_kb_semantic

Three representative queries run against the live pilot corpus (15 KB files) on
first deployment with the Azure OpenAI API key configured.

| Query | hybrid top-1 | semantic top-1 | Agreement |
|---|---|---|---|
| "RTO vs RPO, which DR tier for 1-hour recovery?" | `cloud-fundamentals.md > HA and DR` | same | ✓ |
| "Prevent lateral movement after host compromise" | `security-basics.md > Zero Trust Model` (no keyword overlap) | same | ✓ |
| "NSG rules for AKS subnet" | No relevant result (content not in KB) | No relevant result | ✓ both honest |

**Observations**: hybrid and semantic agree on top-1 for all three queries. Hybrid
correctly surfaces content via semantic similarity with zero keyword overlap (query 2).
Both correctly return no relevant result for query 3, confirming the corpus gap
rather than hallucinating. Result 4 on query 3 (drawio styling guide) is noise
expected at 15-file corpus scale — will improve with more content.
**Trade-off**: at small corpus, BM25 and vector results may include noise because
few documents compete. Quality improves monotonically as corpus grows.
`search_kb_semantic` retirement deferred until corpus reaches 50+ documents and
a larger golden set confirms hybrid consistency.

### 2026-05-17 — Move azure bundle from app/tools/azure/ to top-level bundles/azure/

**Refines the 2026-05-15 "Tool bundles within one repo" decision.** Teams forking Nexus saw `app/tools/azure/` sitting inside `app/` and felt responsible for code that was irrelevant to them — even with `TOOL_BUNDLE_AZURE_ENABLED=false`. Moving it to `bundles/azure/` makes `app/` unambiguously core (never touch it) and `bundles/` visually optional (ignore what doesn't apply to your team). The `init_tools()` loader was updated to scan `bundles.<name>` instead of `app.tools.<name>`; all import paths in the azure tools, orchestrator, and tests were updated to `bundles.azure.*`.
**Trade-off**: any future bundle a team adds lives in `bundles/<teamname>/` rather than `app/tools/<teamname>/` — a minor convention change from the original decision, but the same single-repo, env-flag model otherwise.

### 2026-05-15 — User-identity ARM token passthrough via X-ARM-Token header
Azure tool calls previously ran as the server's managed identity / service
principal. Changed to user-identity: frontend acquires
`https://management.azure.com/user_impersonation` from MSAL, passes it as
`X-ARM-Token`, backend attaches it to `User.arm_token`, orchestrator sets a
`ContextVar`, and `AzureToolBase._run_az()` injects `AZURE_ACCESS_TOKEN` into
every subprocess environment. **Why this over OBO (On-Behalf-Of)**:
OBO requires a client secret on the backend app registration and an extra
token-exchange call per request. Header passthrough needs only one delegated
permission added to the existing app registration and no new secrets.
**Trade-off**: the ARM token travels from the frontend to the backend over
HTTPS — acceptable for a self-hosted internal tool. If a client secret becomes
available, OBO is a drop-in replacement that keeps the ARM token purely
server-side. **Graceful degradation**: if the user hasn't consented to the ARM
scope (or the permission hasn't been granted yet), `arm_token` is `None` and
tools fall back to server-side credentials with no error surfaced to the user.

### 2026-05-17 — Consolidate shared skills into a 3-tier model

**Replaces `chat-with-kb`, `architect`, `azure-principal-architect`, and `kb-searcher` with three tiers using the same slugs.** `kb-searcher` becomes "Default" (read-only tools + KB only); `chat-with-kb` becomes "Azure Engineer" (full execute access, all 25 tools, "execute don't suggest" framing); `architect` becomes "Azure Architect" (same 25 tools, ADR + trade-off framing, Well-Architected Framework section available on request). The four original skills had near-identical 24-tool lists differing only in system-prompt framing, which the agent could not consistently distinguish; the new tiers separate "what tools are available" from "what response style to apply" and align with the planned role-based access model. Slugs were intentionally NOT renamed to avoid cascading changes in `SkillPicker.tsx`, `test_loader.py`, `test_compaction.py`, and `SkillPicker.test.tsx`; what users see is the `display_name` in frontmatter. The two drawio skills (`drawio-diagrammer`, `drawio-from-python`) remain as specialized skills with their own tool sets. **Trade-off**: existing conversations keep their original frozen `skill_snapshot_json` (the invariant holds), but the slug-to-display-name mismatch is now a maintenance smell to be cleaned up in a follow-up PR.

### 2026-05-17 — Role-based skill/tool access via Azure App Configuration

Gate which shared skills users see in `GET /api/skills`, and which tools they can include in personal skills via `GET /api/tools` and `POST /api/skills/personal`, based on Entra App Roles extracted from the JWT. The role→access mapping (skills + tools per role) is stored as a single JSON value under key `Nexus:RoleAccessMap` in Azure App Configuration; the backend reads it once at startup with `DefaultAzureCredential`, validates the shape, and replaces the in-process `_ACCESS_MAP`. The KB Git repo is writable by the same engineers whose tool access needs restricting, so a `kb_data/roles.yaml` would be a privilege-escalation path; App Configuration provides an RBAC-gated, auditable store separate from the KB and the container image. If unreachable, malformed, or the endpoint env var is unset, the backend falls back to hardcoded conservative defaults in `app/auth/rbac.py` (no-role users get the Default skill only; engineer/architect roles keep their full tier sets), logging WARNING — a config outage can only restrict access, never escalate it. Blocking happens at **both** the skill level (visibility filter on `GET /api/skills`) and the tool level (allow-list filter on `GET /api/tools` plus a 403 gate in `POST /api/skills/personal`); the skill-snapshot invariant holds because users only ever pick from skills they are entitled to. **Trade-off**: adds a new operational dependency (App Configuration endpoint + Managed Identity assignment of `App Configuration Data Reader` per environment) and the role→access mapping lives in two places (code defaults + App Config) that must be kept in sync; server-side validation in the personal-skill save endpoint is non-negotiable because UI filtering of `GET /api/tools` is not a security boundary.

### 2026-05-18 — Token usage piggy-backs on the `done` SSE event

The frontend context-usage indicator needs the last LLM call's prompt/completion/cached token counts plus the model's context-window denominator (new `AZURE_OPENAI_CONTEXT_WINDOW_TOKENS` config). We extended the existing `done` event payload with an optional `usage` object instead of introducing a new SSE event type or adding per-message token columns to the DB. No new event type — the 2026-05-15 "SSE event protocol: typed events over a single stream" decision still holds — only one optional field is added to the existing `done` payload. **Trade-off**: usage is not persisted, so switching to a historical conversation clears the indicator until the next reply fires; per-message DB columns were rejected as a schema change disproportionate to a UI accessory, and a separate `usage` event was rejected as additional surface area for the same information that `done` already marks as "this turn is complete".

### 2026-05-19 — Architect absorbs drawio-from-python; retire that skill

**Refines the 2026-05-17 "Consolidate shared skills into a 3-tier model" decision.** Architect gains `generate_drawio_from_python`, `render_drawio`, and `ask_user`, plus the Phase 1–6 architect-to-architect ceremony in its system prompt, so diagram work happens inline without a skill switch — `Conversation.skill_snapshot_json` is frozen at conversation creation, so a true hand-off forced losing context. The `drawio-from-python` shared skill is retired as a duplicate (SKILL.md deleted; removed from `rbac.py` and `test_rbac.py`); `drawio-diagrammer` stays, because its hand-written-XML + `patch_drawio_cell` identity is genuinely different. Engineer loses inline diagrams entirely and hands off to Architect for any diagram request — `validate_drawio` and the phantom `diagram_gen` are dropped from its allowlist. **Trade-off**: Architect's prompt grows by the Phase 1–6 ceremony (more system-prompt tokens per turn even on non-diagram work); accepted because the alternative — three near-identical drawio prompts to keep in sync (Architect + drawio-from-python skill + KB docs) — bit-rots faster, and the 2026-05-17 entry's specific failure ("Architect and Engineer indistinguishable on diagram tools") is what prompted this.

### 2026-05-20 — Agent learnings move to SQLite + vec0

**Replaces the single `kb_data/learnings/learn.md` file with a SQLite `agent_learnings` table plus a companion `agent_learnings_vec` virtual table (sqlite-vec) for retrieval embeddings.** The file-based model had no per-entry attribution, no validation history, no scope, and `get_learnings_content()` silently truncated at 4 KB while the file itself grew to 29.6 KB. The new schema reuses the existing sqlite-vec / Azure OpenAI embedding stack — same dimensions as `kb_chunks_vec` — so there's no new third-party dependency. On first startup `migrate_legacy_learn_md` imports legacy entries as `provisional` rows; the original file stays in the KB repo as an archive but is no longer read at runtime. **Trade-off**: the schema is now a hard-to-reverse boundary (the legacy file path is dead, migration is one-way), and every retrieval pays one Azure OpenAI embedding round-trip per turn that the old always-inject path didn't.

### 2026-05-20 — Orchestrator-owned learning writes; agent tools removed

**Replaces the agent-callable `update_learnings` / `read_learnings` tools with an orchestrator-internal write path (`app/agent/learnings.py::record_validated_learning`) called only from the success-after-failure detector in `orchestrator.py`.** Observed failure mode that motivated this: GPT-class models wrote entries like *"the drawio validator is too strict — ignore overlap warnings"* to suppress inconvenient tool output (memory-poisoning via hint-suppression, documented in 2025-2026 LLM agent research). The `ReadLearningsTool` and `UpdateLearningsTool` classes have been deleted, so the agent has no learning-write path at all — a structural defense (Voyager-pattern) rather than a prompt-only constraint. **Trade-off**: agents can no longer record arbitrary opinions as learnings — which is the point; the entry content is now derived from tracked failure→success state, losing some nuance the agent might have added in free text.

### 2026-05-20 — Three-gate write defense for learnings

**Adds three sequential gates to `record_validated_learning`: (1) the existing `_OVERRIDE_PATTERNS` regex, (2) a new environment-specific name guard (rejects GUIDs and `<service>-<env>-<region>-<num>` patterns), (3) a new LLM judge (`app/agent/learn_judge.py`).** Regex alone is brittle to paraphrase — *"ignore the validator"* is caught, but *"the layout looks correct so overlap warnings can be skipped"* is not; the LLM judge catches both. The judge fails closed — any error or timeout returns `approve=False` — so a broken or unreachable judge can only reject legitimate writes, never let a poisoned one through. Rejected entries are persisted with `status='rejected'` and the full verdict for audit, but cannot be re-activated via the admin API. **Trade-off**: each write costs one extra Azure OpenAI completion (the judge call); accepted because the regex-only defense was already shown to miss real attacks.

### 2026-05-20 — Retrieval-on-context replaces always-on injection

**Replaces `_compose_system_prompt`'s unconditional injection of `learn.md` with a per-turn embedding-based retrieval (`retrieve_relevant_learnings(query, top_k=5)`).** The old path always injected the first 4 KB regardless of relevance, then truncated with a message telling the agent to call `read_learnings` — which the orchestrator separately instructed the agent *not* to call; that contradiction is now gone. Retrieved entries appear in the prompt with `[CANONICAL]` / `[PROVISIONAL]` markers, and if zero entries are relevant the section is omitted entirely (correct degraded state, not a misleading empty header). The orchestrator threads the retrieved IDs through the turn so `mark_learning_outcome` can update validation/failure counters as tool calls resolve. **Trade-off**: each chat turn now pays one Azure OpenAI embedding round-trip for the retrieval query; accepted because the old always-on injection cost grew linearly with file size and contradicted itself.

### 2026-05-20 — Auto-promote/auto-archive via validation tracking

**Provisional learnings auto-promote to `active` when `validation_count` reaches 3; entries auto-archive when `failure_count` reaches 3 and exceeds validations.** Counters update when retrieved learnings are in scope and the subsequent tool call resolves — success increments validation, error increments failure. The signal is heuristic — the agent may have ignored the retrieved entry — but across many turns it directionally promotes load-bearing entries and archives drifted ones (e.g. Azure API changes that invalidate a once-correct workaround). Architects can override the counters via PATCH on the admin API, which is why thresholds are 3 and not 1 — single-turn flukes shouldn't promote, and architects can fast-path a promotion when needed. **Trade-off**: a learning that's correct but rarely retrieved (its embedding sits far from any real user query) never promotes; accepted because the alternative — auto-promote on age — would canonize unvalidated entries by default.

### 2026-05-20 — Architect-gated admin API for agent-learnings

**Adds a CRUD admin surface (`/api/learnings`: list, detail, PATCH status, DELETE) plus a frontend page at `/admin/learnings`, both gated to the `architect` Entra App Role via a new `require_architect` dependency in `app/deps.py`.** Engineer-role users get 403; `DEV_AUTH_BYPASS=true` passes through to match the existing pattern in `app/auth/rbac.py`. PATCH cannot set status to `rejected` (Pydantic 422) and cannot change a rejected entry's status at all (409) — reactivating a judge-flagged learning would re-enable the poisoning class of attacks the judge exists to prevent. **Trade-off**: engineer-role users cannot even *view* learnings — they get 403 across the board; accepted because learnings are architectural memory (only architects should promote/archive) and read-only-for-engineer is a follow-up if it turns out to be needed. Alternatives rejected: (1) per-conversation learning ownership with personal vs team scope — learnings are about toolchain behaviour, not user identity; (2) a CLI-only admin path — architects already have a browser session open and a table is lower friction than `sqlite3` queries.

### 2026-05-20 — Engineer skill rejects .drawio writes at tool layer

The Engineer (`chat-with-kb`) skill's prompt ruled out diagram generation in the 2026-05-19 retire decision, but the model kept calling `generate_file` with a `.drawio` filename anyway. We now enforce the rule structurally: a `ContextVar` (`_current_skill_name` in `app/tools/base.py`) carries the active skill slug into the tool layer, and `generate_file` returns an error when `skill_name == "chat-with-kb"` and the extension is `.drawio`. Prompt rewrite alone failed in 2026-05-19 sanity testing; defence-in-depth landed.
**Trade-off**: tools are no longer skill-agnostic — one tool now branches on skill context, a small architectural smell. The mechanism is reusable for future skill-scoped restrictions (same pattern as ARM-token passthrough in §5 2026-05-15).

### 2026-05-20 — Rendered PNG attaches via assistant `attachments_json`

Diagram tools producing a PNG (`generate_drawio_from_python`, `render_drawio`, `generate_file`, `patch_drawio_cell`) now have their output captured in a per-turn dict (`pending_render_attachments`) and drained onto the next assistant message's `attachments_json` when the turn terminates with no further tool calls. The frontend already renders inline images from `Message.attachments_json` — zero new SSE events, zero new endpoints.
**Trade-off**: the PNG appears one assistant-turn AFTER the tool call (next to the agent's description), not in the tool-result card itself. Rejected adding a new SSE event type because §5 2026-05-15 and §5 2026-05-18 both established that new information piggy-backs on existing payloads rather than expanding the event vocabulary.

### 2026-05-20 — kb_index.json is optional metadata, not source of truth

`load_index()` now scans `<KB_REPO_LOCAL_PATH>/kb/**/*.md` and produces an entry per file regardless of whether `kb_index.json` exists; curated `summary` and `tags` from the json are layered onto matching disk-scanned entries when present. Replaces the prior implicit "kb_index.json is the index" assumption that left 10 files invisible to `search_kb` and the system-prompt KB summary on 2026-05-19. The merge runs on startup AND after every periodic `sync_repo()`, so new KB-repo files appear without a backend restart.
**Trade-off**: non-`.md` files (e.g. `.drawio` reference patterns) still require a `kb_index.json` entry to be indexed — the auto-scan is `.md`-only. Curated entries pointing at deleted files are logged as drift warnings and skipped, so omissions on either side are noticed without breaking the live index.

### 2026-05-20 — Orchestrator nudges on narration-instead-of-action

When the model returns no `tool_calls` but the closing of the assistant text matches a "deferred action" pattern (`(I'll|I will|Let me|...)` + action verb, last 400 chars only), the orchestrator appends a synthetic system reminder and re-enters the loop once before yielding `done` — capped at one nudge per turn (`narration_nudges_used`) so a stubborn narrator can't infinite-loop us. Behind feature flag `NARRATION_NUDGE_ENABLED` (default `true`). The architect's "Tool calls are not narration" hard rule didn't hold in practice; this is the structural backstop.
**Trade-off**: false-positive risk on legitimate informational replies — the regex is intentionally narrow (closed verb list, tail-only match) to limit it. Interim until the DSPy refactor ([dspy-coverage-tracker.md](../IdeasTodo/dspy-coverage-tracker.md) row 4) makes the no-tool-call-with-narration state structurally unrepresentable.

### 2026-05-21 — Subprocess hardening: env allowlist + shell=False + block %, &

**Replaces the 2026-05-15 "Shell injection blocks only backtick and NUL" decision.** `_run_az()` now builds an explicit ~14-key env allowlist (PATH, HOME, AZURE_CONFIG_DIR, Windows profile vars, proxy vars, plus the ARM token overlay) instead of inheriting `os.environ`, so a malicious `az` argument expanding `%AZURE_OPENAI_API_KEY%` (or similar) has nothing to leak. `subprocess.run` is now invoked with `shell=False` unconditionally; `shutil.which("az")` resolves to `az.cmd` on Windows so cmd.exe is never in the path. `check_shell_injection` additionally rejects `%` (Windows env expansion) and `&` (command chaining); `|`, `;`, `<`, `>`, `$` are still allowed because KQL queries and JSON bodies legitimately use them.
**Trade-off**: any future tool that depends on an env var outside the allowlist must add it explicitly — accepted because the previous "inherit everything" stance made credential exfiltration a one-arg attack. The block-list narrowing on `%` and `&` is defence-in-depth on top of `shell=False`, not the primary defence.

### 2026-05-21 — ARM token preflight + frontend-driven refresh via new SSE event

Orchestrator now calls `arm_token_status()` (decodes only the JWT `exp` claim, unverified) before every `AzureToolBase` dispatch; `missing` / `expired` short-circuits with a structured error telling the model to wait, `near_expiry` still executes but emits a new `token_refresh_required` SSE event. The frontend handles the event by calling `msalInstance.acquireTokenSilent()` and POSTs the new token to `/api/chat/refresh-token`, which JWT-validates audience/tenant/expiry and stores it via `set_arm_token_override()` for the in-flight turn. This extends the typed-SSE-events approach from §5 2026-05-15 with one new event type — chosen over piggy-backing on `done` (§5 2026-05-18) because the refresh needs to happen *mid-turn*, before `done` ever fires.
**Trade-off**: one new event type and one new endpoint to maintain; vs the alternative of always failing the turn and letting the user retry — rejected because long architect turns can outlive a 1-hour ARM token and silent-renewal preserves the conversation.

### 2026-05-21 — Lease heartbeat on conversations row; recovery is "restart turn" not state replay

Added `conversations.lease_heartbeat_at` + `lease_owner` columns (lightweight migration in `_apply_lightweight_migrations`) plus `GET /api/conversations/{id}/lease` returning `idle | active | stale` and the last user message id. The orchestrator writes a heartbeat at most every 30s (`LEASE_HEARTBEAT_INTERVAL_SECONDS`) and clears it at end-of-turn. The frontend uses this to offer a "Restart turn" affordance when a worker crashes mid-turn — synthetic retry / drawio state is **deliberately not** reconstructed, because reasoning context can't be restored faithfully from DB rows and a partial replay would mislead the agent.
**Trade-off**: schema migration is hard to reverse, and a crashed turn loses in-flight reasoning; accepted because "restart from the last user message" is a clear UX contract and reconstructing partial state has been a source of bugs in agent frameworks we surveyed.

### 2026-05-21 — Dedicated tool ThreadPoolExecutor + per-user asyncio.Semaphore

Tool dispatch now goes through `app/agent/concurrency.py`: a lazy-singleton `ThreadPoolExecutor(max_workers=64, thread_name_prefix="tool")` torn down via FastAPI lifespan, a per-user `asyncio.Semaphore(4)` chokepoint (`_gated_tool_execute`), and a `run_in_tool_executor()` helper that `copy_context().run(...)` propagates `ContextVar`s (ARM token, active skill) into the worker. KB indexing / SQLite / GitPython / MSAL stay on Python's default executor — only orchestrator tool subprocesses move. The full `asyncio.create_subprocess_exec` port of `_run_az` was deliberately deferred: the bounded pool + per-user cap addresses the exhaustion symptom this change was scoped against.
**Trade-off**: two thread pools to reason about instead of one, plus a hidden invariant that `ContextVar`s only survive the hop when callers use `run_in_tool_executor` (not `asyncio.to_thread`); accepted because a single chatty user could previously starve everyone else's tool calls.

### 2026-05-21 — Rephrase learnings before the 3-gate defense, not after

**Refines the 2026-05-20 "Three-gate write defense" decision.** `derive_learning_from_success()` still produces raw `details` + a rule-derived rough `summary`, but a new `rephrase_learning()` call now runs the summary through the chat deployment with a strict "no opinions, no framing" system prompt to produce the canonical sentence stored in `agent_learnings.summary`. The three gates (regex / name guard / LLM judge) then run on the **rephrased** text, so a malicious rephrase can't slip suppression intent past the detectors. On rephrase failure / empty output / 3× length blowup, the rule-derived summary is used unchanged.
**Trade-off**: one extra Azure OpenAI completion per learning write (in addition to the judge call); accepted because rephrasing-then-gating is structurally safer than gating-then-rephrasing, and the write path is already async via `_schedule_learning_write` so end-of-turn latency is unaffected.

### 2026-05-21 — Hybrid retrieval for agent_learnings: FTS5 + vec0 + RRF

Added `agent_learnings_fts` (FTS5 external-content over `agent_learnings`) with INSERT/UPDATE/DELETE triggers and a `rebuild` backfill in `_ensure_agent_learnings_vec()`. `retrieve_relevant_learnings()` now runs BM25 and sqlite-vec in parallel and fuses via Reciprocal Rank Fusion (`_rrf_fuse`); either side may be absent (FTS5 or vec0 module missing) and the other carries the result. Status / tool-name / validation boosts are applied on top of the fused score. Mirrors the architecture already validated for KB hybrid retrieval (§5 2026-05-14 / 2026-05-15) so there are no new dependencies — same `sqlite-vec` extension, same Azure OpenAI embedding deployment.
**Trade-off**: a third virtual table on the same logical row (`agent_learnings`, `agent_learnings_vec`, `agent_learnings_fts`) — more schema surface for a relatively small table; accepted because vector-only retrieval was missing keyword matches on tool names and exact phrases architects search for in the admin UI.

### 2026-05-21 — Azure OpenAI circuit breaker around every completions call

Module-level `app/agent/circuit_breaker.py` with closed / open / half_open states, configured via three `AOAI_CB_*` settings (failure threshold 5, window 60s, open 30s). Every chat-completions call (main orchestrator loop, compaction summarizer, learning judge, learning rephrase) now wraps in `cb_check()` → call → `cb_success()` / `cb_failure()`, and `/healthz` includes `aoai_circuit_breaker: <state>` so deployers can detect a stuck-open breaker without watching logs. When open, calls short-circuit with a clear error to the agent rather than piling up timeouts.
**Trade-off**: chat halts entirely during the open window for the affected deployment; vs the previous behaviour of cascading timeouts that exhausted retry budgets across every concurrent conversation. The breaker is process-local, so multi-replica deployments will need a shared store before scaling out — noted alongside the §6 concurrency assumption.

### 2026-05-21 — LLM summarisation for large tool outputs; head+tail is the fallback

Tool results over 2 KB (configurable threshold) are now routed through `_summarize_tool_result_with_llm()` before being fed back to the agent, instead of the previous head+tail split that could leave the model staring at half a JSON object or duplicate top-level keys. Error envelopes (`status == "error"`) skip the summariser path — the agent gets exact error text so retry strategy decisions stay faithful. On summariser failure / timeout / empty output, the old head+tail truncation is the fallback so a degraded LLM never breaks the chat.
**Trade-off**: one extra Azure OpenAI call per oversized tool result and a small risk of summary loss-of-fidelity on novel formats; accepted because the head+tail split was a documented source of model confusion on JSON / drawio outputs and the new path preserves the *meaning* across both ends.

### 2026-05-22 — Retire `run_shell`; replace with `execute_script` + `read_file` + `az_rest_api.body_file`

**Replaces Phase 5 Track 5B's "sandbox `run_shell` in ACI" plan.** A 14-conversation audit of `run_shell` usage (47 invocations, the only field we have) found: ~25% of calls were users explicitly demanding "run a shell command" (test traffic, not real use), ~25% were the model bypassing dedicated Azure tools (`az` invoked through `run_shell` because the typed tool didn't expose a needed feature), ~25% were Nexus self-startup (already retired in spirit by §5 2026-05-15 "Removed deploy-backend etc. skills"), and the rest were `.ps1` execution from `output/scripts/`. The single most consequential cascade (conv 257, Logic App PATCH) failed seven times because `az_rest_api` had no way to take a request body from a file and there was no `read_file` symmetric with `generate_file`. Closing those two typed-tool gaps removes every legitimate driver of `run_shell`. The remaining script-execution surface is replaced by `execute_script(path)`, scoped to `output/scripts/`, shell inferred from extension (`.ps1` → PowerShell, `.sh` → bash), no inline command parameter, no `args` parameter (deferred — observed legitimate scripts were self-contained; add when ≥3 real conversations demand it).
**Trade-off**: ACI sandbox dropped (saves operational surface — image build, network policy, cold-start latency); the deletion is hard to reverse but the new surfaces are strict subsets of what `run_shell` did, so any genuinely-needed shell use case must surface as a typed-tool gap that gets closed deliberately. The structural narrowing replaces a perimeter (sandbox) with structural impossibility (no command string to pass), which is the OWASP LLM06 "Excessive Agency" mitigation done at the interface rather than the runtime.

### 2026-05-23 — AWS icon support in drawio diagrammer

Adds AWS service mappings (~90 services spanning `compute / network / database / storage / security / integration / analytics / ml / management / devtools / iot / general` mingrammer namespaces — parity with the existing Azure depth) to `_drawio_emitter.py`. The emitter uses the `mxgraph.aws4.resourceIcon` wrapper stencil with a service-specific `resIcon=mxgraph.aws4.<service>` reference and a per-service-group `fillColor` (compute=orange, network=purple, database=red, storage=green, security=red-pink, analytics=purple, ml=teal, integration/management/iot=magenta, devtools=plum), yielding the colored-tile-with-white-icon look from AWS's official architecture-icon set. Picked over the flat `shape=mxgraph.aws4.<service>` style after the smoke test showed the flat variant (a) renders as a colored outline with empty white background — washed-out at small zooms — and (b) requires fillColor anyway because without it the stencil is invisible against white. The validator's existing `[icon-style]` allowance (`shape=mxgraph.aws4.` substring) covers both styles, so no validator change was needed. Architects drew weekly AWS diagrams manually before this because `from diagrams.aws.*` imports fell through to the rectangle style and then blocked on `[icon-style]` validator violations. Same PR adds a `prometheus-client` counter on diagram-tool mingrammer imports labelled with `<cloud>/<subnamespace>` (e.g. `aws/analytics`, `gcp/compute`) so GCP-support priority and AWS-coverage gaps inside curated namespaces are decided from real usage data — `sum by (tool, cloud)` recovers the cloud-level rollup at query time. Tightened badge placement for short labeled edges (perpendicular nudge raised past the validator's 40 px label-render threshold) closes the badge/label collision that surfaced on every numbered-flow diagram.
**Trade-off**: mingrammer's AWS class names drift from AWS's actual icon catalog so a few mappings are approximations (OpenSearch routes to the legacy `elasticsearch_service` stencil since the catalog has no clean `opensearch` root; `IotGreengrass` routes to `greengrass`; `DocumentDB` to `documentdb_with_mongodb_compatibility`). The Architect SKILL.md's "Guaranteed-good imports" section lists the curated set so the LLM picks supported classes. Rejected alternatives: (a) the flat-icon style (rejected — invisible without fillColor, less legible at small zooms); (b) full AWS + GCP parity across ~160 mappings before merging (rejected — would have delayed AWS architects' weekly workflow further; GCP is now gated on telemetry data the new `<cloud>/<subnamespace>` label captures).

### 2026-05-25 — Tool-outcome telemetry counter with coarse success/empty/error taxonomy

Added `nexus_tool_calls_total{tool, outcome}` (incremented once per tool dispatch in `orchestrator.py`) plus `classify_tool_outcome()` in `app/tools/base.py`. Outcome is one of three labels: `success`, `empty` (empty string / short result / empty-data JSON envelope), `error` ("Error:" prefix or `status == "error"` envelope). The taxonomy is deliberately coarse so the immediate question — "is this tool quietly failing?" — is answerable from one PromQL query without a join, replacing one-off DB archaeology of the `messages` table.
**Trade-off**: the coarse labels lose detail (rate-limit vs auth-fail vs server-error all collapse to `error`); accepted because finer taxonomies become hard-to-change public API once dashboards reference them. The classification rules — 30-char short-result threshold and the empty-array regex over `data|results|items|value` keys — are a snapshot of how today's tools shape their results; if a future tool returns large blobs that are still semantically empty, refine `_EMPTY_RESULT_MAX_LEN` and the regex in lockstep.

### 2026-05-25 — LLM-judge reranker for `search_kb_hybrid`

**Replaces the ~~2026-05-15 "Conditional cross-encoder reranking"~~ decision.** That struck-through entry left a conditional trigger ("if retrieval quality proves insufficient at larger corpus scale, a reranker can be added then") which fired once `kb_hybrid_eval_50.py` made mid-rank relevance gaps measurable. `app/kb/reranker.py` calibrates each top-K RRF candidate to a 0.0–1.0 relevance score via one Azure OpenAI chat call, sorts the judged segment, and tags each hit with a `confidence` tier (high/medium/low) from configurable thresholds. Chose LLM-judge over the originally-planned `bge-reranker-base` cross-encoder because it reuses the existing Azure OpenAI deployment (no new 279 MB model file, no ONNX runtime), and the calibrated 0.0–1.0 score transfers across corpora — unlike raw cosine distance, which is corpus-dependent.
**Trade-off**: one additional Azure OpenAI completion call per `search_kb_hybrid` invocation (~200 output tokens) and rerank availability is now gated on Azure OpenAI — accepted because the call participates in the §5 2026-05-21 circuit breaker, falls back to RRF order on any parse/API failure, and can be disabled via `KB_RERANK_ENABLED=false`.

### 2026-05-26 — Multi-wiki ADO ingestion via `INGEST_ADO_WIKI_SOURCES` list

Replaces the four scalar `INGEST_ADO_WIKI_*` env vars (deleted outright, no deprecation, because no other deployment has them set yet) with a JSON list of `{label, org, project, wiki}` records; each source ingests into `kb_data/kb/ado_wiki/<label>/` and tags chunks with a new `source_instance` column on `kb_chunks`. The `label` is a stable user-chosen identifier (regex `^[a-z][a-z0-9-]{1,39}$`, unique across sources), never derived from the ADO project name — because ADO renames would otherwise silently orphan every chunk in the renamed source. A `_source_meta.json` sentinel in each label directory pins the `(org, project, wiki)` triple so accidental label rebinds at deploy time fail loudly instead of silently swapping content. The reindexer's existing `_gc()` orphan-sweep (delete from `kb_chunks_vec` then `kb_chunks` where `kb_path` no longer exists on disk) handles deleted pages going forward; cutover from the single-wiki path is a one-time idempotent legacy DELETE gated on the `source_instance` column-add, dropping flat `kb/ado_wiki/<page>.md` chunks while leaving hand-authored and PDF content untouched.
**Trade-off**: rejected the "aggregate N wikis into one KB git repo via an external job" path (cleaner, zero Nexus code) because the Nexus team would own the aggregator with no operational appetite for a second service; rejected per-source PATs as YAGNI for the one-org-one-team case but left the additive upgrade path documented.

### 2026-05-27 — ARM token mandatory under real auth; az fallback dev-only

**Refines the 2026-05-21 "ARM token preflight" decision and restores the 2026-05-15 graceful-degradation promise for dev.** The B3 pre-flight treated a `missing` ARM token as a hard short-circuit in every environment, which broke local `DEV_AUTH_BYPASS=true` runs — `dev-user` never carries a token, so every Azure tool told the user to "sign in from the frontend" instead of falling through to the developer's local `az login` session. Now `missing` is gated on `DEV_AUTH_BYPASS`: under bypass it falls through to the local/server `az` session; under any real-auth deployment (production included) it stays a hard stop. `expired` / `near_expiry` remain unconditional — they can only occur when a token was actually present. **Trade-off**: production users must consent the ARM `user_impersonation` scope before any Azure tool works (no silent server-identity fallback), accepted because running Azure commands as the server identity rather than the signed-in user is exactly the privilege ambiguity the 2026-05-15 passthrough decision exists to remove.

### 2026-06-01 — ARM gate keys off platform env var, not config flag

**Refines the 2026-05-27 "ARM token mandatory" decision.** The `DEV_AUTH_BYPASS` predicate was unspoofable but still a per-deployment config knob; the hosting platform already tells us we're deployed. The ARM missing-token hard-stop now triggers when `CONTAINER_APP_NAME` is set (Azure Container Apps injects this in every replica) and falls through to the local `az login` session otherwise. URL-based detection (`Host` header, peer IP) was considered and rejected: every signal a request carries is either client-spoofable from the internet or, behind the Container Apps ingress controller, reports localhost from inside the container — inverting the intent. **Trade-off**: the gate is now coupled to one specific hosting platform — `_is_deployed_environment()` is the single edit point if Nexus ever moves off Container Apps. `DEV_AUTH_BYPASS` survives unchanged for its other job (bypassing Entra JWT validation for `dev-user`).

### 2026-06-04 — Advisory risk assessment on approval cards via a separate review LLM

Every approval-gated tool call (`az_cli`, `execute_script`, mutating `az_rest_api` /
`az_devops`) gets an AI risk verdict (✓ safe / ⚠ caution / ⛔ destructive) plus a neutral
"what this command does" description, produced by a **separate** LLM call so the generator
isn't grading its own homework; ⛔ requires a second confirmation click. The verdict is
advisory only — it never approves or denies, the human stays the sole gate — and a
deterministic floor (the existing `_is_blocked` matcher for `az_cli`, plus an equivalent
content scan of the `.ps1`/`.sh` body for `execute_script`) can raise the tier to ⛔ but the
LLM can never lower it, so a false ✓ can't downgrade a destructive command. The card renders
immediately with Allow disabled until the verdict resolves; the review is capped ~3–4s and
**fails closed to ⚠ "assessment unavailable," never ✓.** The reviewer judges the raw command
cold (not the generator's `reason`, which stays only for audit) and reads script *contents*
for `execute_script`; the verdict folds into the existing `approval_required` SSE event (per
the 2026-05-15 typed-events decision), with `risk_level` + `risk_description` added to
`pending_approvals`.
**Trade-off**: one extra LLM call and a round-trip of latency on every approval, a new schema
column, and the risk that a green ✓ trains users to stop reading — accepted because the
deterministic floor + escalate-only + fail-closed design keeps the tick from ever being the
sole guard on a destructive command, where a biased self-review would rationalize what an
independent reviewer catches.

### 2026-06-04 — A user denial is terminal, never a retryable error

When the user denies an approval-gated tool call, the orchestrator now classifies the result
as its own envelope status `"denied"` (via `_tool_control_outcome`) with `is_error=False`, so
it can never enter the multi-strategy retry path. Previously a denial was lumped in as an
error, which fed Strategy 2 — the hint that literally tells the model "use `az_rest_api`
instead" — so a denied `az vm delete` got routed into an `az_rest_api DELETE` (denial-evasion;
OWASP LLM06 Excessive Agency). The denial is fed back with an explicit "this is final, do not
attempt the same outcome by any other tool/command/REST/script" instruction, the
success-after-failure learning path is also skipped, and a structural backstop
(`_MAX_DENIALS_PER_TURN = 1`) auto-refuses any further approval-gated call for the rest of the
turn **without re-prompting the user**, so a refusal can't be turned into approval-spam.
**Trade-off**: a denied call gets no retry even when the refusal was about syntax rather than
intent (the user must re-ask), and the per-turn auto-deny can block a later legitimate approval
in the same turn — both accepted because honouring a refusal unconditionally is the whole point
of the approval gate, and a fresh user message clears the turn-level state.

### 2026-06-04 — Stop button: interrupt cleanup via `cleanup_interrupted_turn`

The chat composer shows a Stop button in place of Send while a turn is streaming; it aborts the
SSE request client-side (`AbortController`), which disconnects the `StreamingResponse` and
closes the orchestrator generator. Because the generator's normal done-path cleanup never runs
on an interrupt, the `/api/chat` stream wraps its loop in a `finally` that calls
`cleanup_interrupted_turn(session, conversation_id)` — clearing the conversation lease and any
ARM-token override so a stopped turn doesn't leave stale state behind. A tool subprocess already
executing when Stop is pressed finishes server-side but its result is discarded; no *new* tool
calls run after the abort, and the frontend re-fetches the conversation so the view reflects
exactly what was persisted. **Trade-off**: an in-flight (already-approved) command is not
force-killed — Stop halts generation and the agent's forward progress, not an OS process that
is already running — accepted because cooperatively cancelling a mid-flight subprocess across
the thread-pool boundary is out of scope and the approval gate already governed that command.

### 2026-06-04 — Kill switch for execute_script via tracked process group

**Refines the 2026-06-04 "Stop button" decision, which left in-flight subprocess kill out of
scope.** `execute_script` now launches its subprocess as a killable group (POSIX
`start_new_session` → `os.killpg`; Windows `taskkill /F /T` over the PID tree) and registers
the handle in a per-conversation registry in `app/tools/base.py`, keyed by a `conversation_id`
ContextVar that propagates into the executor thread (same mechanism as the ARM token / skill
slug). The existing Stop / client-disconnect path kills the whole tree through
`cleanup_interrupted_turn`, so a multi-step script (a loop over resources) can be stopped before
its remaining iterations run. **Rejected alternative**: running scripts via an Azure DevOps
pipeline the user could cancel — rejected because a pipeline executes as its service connection,
not the signed-in user, which breaks the 2026-05-15 user-identity ARM-token passthrough
invariant (a privilege-escalation path: the user acts as the pipeline's SP) and reintroduces the
operational surface the 2026-05-22 run_shell/ACI decision rejected. A kill switch on single-line
`az_cli` / `az_rest_api` was also rejected as false safety — their destructive effect is a
server-side ARM dispatch that killing the local `az` process can't recall. **Trade-off**:
scripts still run on the Nexus host (blast radius unchanged), and the kill stops only *future*
iterations — whatever the current iteration already dispatched to Azure completes server-side;
accepted because preserving "every action runs as the signed-in user" plus a real forward-stop
beats a better abort button that acts as the wrong principal.

### 2026-06-04 — Decouple learning-eligibility from retry-eligibility; broaden capture

**Refines the 2026-05-20 "Orchestrator-owned learning writes" decision.** Learning capture was gated on `_COMMAND_TOOLS` (`az_cli`, `execute_script`, `az_resource_graph`) — the same set used for multi-strategy retry — so `az_rest_api`, `az_devops`, and the diagram-as-code tools could never produce a learning despite emitting real `status:error` failures the agent recovers from within a turn; a new `_LEARNING_ELIGIBLE_TOOLS` superset now gates the success-after-failure write path while `_COMMAND_TOOLS` continues to gate retry alone (read/search/`ask_user` stay excluded — their "failures" don't generalize). This only became worth doing once three latent bugs that made the live path produce *zero* rows were fixed in the same change: `az_cli`/`execute_script` reported non-zero exits as `"Exit code: N"` (so the orchestrator's `is_error` prefix check never fired and the failure was never tracked); `derive_learning_from_success` embedded raw tool args whose subscription GUIDs tripped the `_looks_environment_specific` guard and rejected every Azure learning (now redacted to placeholders at derivation, the guard kept as a safety net); and the fire-and-forget judge task discarded its own reference and could be GC'd mid-flight. The LLM judge additionally now retries transient AOAI errors (`max_retries=2`) rather than failing closed on a momentary hiccup. All existing gates (redaction, override-pattern guard, environment guard, LLM judge, provisional→active lifecycle) still apply, so broader capture does not widen the memory-poisoning surface. **Trade-off**: more background judge/rephrase LLM calls and more provisional rows to validate; accepted because the 2026-06-04 conversation logs showed the highest-value lessons (REST api-versions, diagram-class hallucinations) live exactly in the previously-excluded tools.

### 2026-06-05 — User-correction learning capture, source-gated lifecycle

**Extends the 2026-05-20 "Orchestrator-owned learning writes" decision with a second learning source.** An explicit user teach-intent turn ("add to learnings that…", "remember this…") now triggers a background extractor that records a learning, tagged on a new `agent_learnings.source` column (`failure_success` | `user_correction`); the write stays orchestrator-owned (the model never elects to write — a cheap regex marker pre-gate detects intent, a constrained extractor treats the user message as untrusted *data to analyse, never instructions to follow*, and the result still passes the full rephrase → override-regex → name-guard → suppression-judge stack and lands `provisional`). Because user assertions are grounded in opinion, not reality, `source='user_correction'` rows are excluded from the tool-outcome promote/archive path in `mark_learning_outcome` — a command merely running cannot validate the *correctness* of advice (and would otherwise canonize wrong-but-harmless guidance), so their only removal arbiter is a later *contradicting* user correction, which supersedes the older entry via an embedding-similarity + LLM check. Scope is deliberately explicit-intent-only (empirically ~0.7% of turns vs ~9% for generic corrective markers, at ~80% vs ~10% precision) and there is **no** auto-promotion path yet: these stay provisional, retrievable, and labelled until a future survival signal or an architect promotes them. Gated behind `LEARN_FROM_USER_CORRECTIONS` (kill switch) and the `source` column (audit + future per-source query). **Trade-off**: a new schema column (hard to reverse) plus ~3 background LLM calls on the rare capture turn, and a class of memory that never reaches `active` automatically; accepted because the highest-value team knowledge (architecture/policy lessons like "traffic always routes through F5") arrives as user assertions that no failure→success path can ever capture, and the pollution risk is bounded by contradiction-archive + no-auto-promote rather than by trusting the user blindly.

### 2026-06-05 — LLM-synthesized learning summary replaces mechanical arg-diff

**Replaces the `fail[:80] → success[:80]` arg-diff summary in `derive_learning_from_success` with an LLM synthesizer (`synthesize_learning`) that reads the full redacted args.** The prefix-diff collapsed to a self-contradictory "switch from X to X" whenever the distinguishing change sat past the 80-char window (REST URLs, KQL `project` clauses) and leaked identifiers buried in URL/query positions the placeholder redaction doesn't cover — both confirmed in the live DB, where every rejected `az_rest_api` / `az_resource_graph` learning traced to one of those two derivation defects (the judge was correctly rejecting genuinely-malformed derived content, so the fix belongs upstream of it). The synthesizer states the transferable mechanism (a flag, HTTP method, api-version, query shape) rather than the target, and returns the sentinel `NONE` when the calls differ only in identifiers — so `derive_learning_from_success` now returns `Optional` and the orchestrator skips the write entirely instead of manufacturing junk and spending a judge call to reject it. It runs in the already-backgrounded write path (off the request thread) and fails soft: a transient error falls back to the mechanical summary so a real lesson is never lost to a flaky call. **Trade-off**: one extra background LLM call per failure→success write, and synthesis could in principle hallucinate `NONE` for a real lesson; accepted because the mechanical summary was demonstrably producing judge-rejected garbage for exactly the long-payload tools (`az_rest_api`, `az_resource_graph`) the 2026-06-04 broaden-capture change had just made eligible.

### 2026-06-05 — Decouple core from bundles; defer credential abstraction

**Refines the 2026-05-17 "Move azure bundle to `bundles/azure/`" decision**, which intended `app/` to be "unambiguously core (never touch it)" but left core still importing `bundles.azure` by name and hardcoding Azure tool names in `init_tools`, the orchestrator (`_COMMAND_TOOLS`, `_LEARNING_ELIGIBLE_TOOLS`, retry hints, tool-hierarchy prompt) and `phases.py`. The work is phased: first (shipped) per-tool capability attributes (`retry_eligible`, `learning_eligible`, `result_limit`, `requires_credentials`, `config_flag`) carry the facts the orchestrator and loader used to hardcode; then a directory-scan bundle loader, a bundle-manifest hook for prompt fragments, and the relocation of `AzureToolBase` into the Azure bundle let a new platform bundle drop into `bundles/<name>/` behind one `TOOL_BUNDLE_<NAME>_ENABLED` flag with zero `app/` edits. Each bundle owns its authentication internally (as `AzureToolBase` already does); the ARM-token preflight and `token_refresh_required` SSE event stay in core as accepted, documented Azure-aware residue, as does `phases.py`'s temporary, CI-guarded tool-gate map (deliberately left untouched because it is slated for wholesale removal at full rollout). A universal Credential Provider abstraction (per-user vs shared principal; multi-field blobs for AWS STS, Palo Alto API keys, on-prem service accounts) was **considered and deferred** — with only Azure's auth shape fully known and the AWS Okta→STS flow unpinned, designing it now would freeze an Azure-shaped generic against unscoped future bundles, the same speculative generality the 2026-05-15 plugin-registry rejection and the §7 DSPy deferral avoid. The deferral captures one design rule for that future work: a provider can silently transfer/acquire from the existing session **iff its IdP equals Nexus's login IdP (Entra)** — so Azure/ADO/SharePoint/Teams ride one Entra provider at different scopes while AWS (Okta) runs its own STS flow, and the frontend-passthrough-vs-backend-OBO choice (the latter reverses §5 2026-05-15) is decided then, not now. **Trade-off**: when AWS lands it self-contains its STS plumbing and likely duplicates some credential handling, and the user-identity invariant (§5 2026-05-15 / 2026-05-27 / 2026-06-01) is **not** generalised here — both accepted because extracting a Provider is cheap once two real auth shapes exist whereas generalising now is not.

### 2026-06-06 — Context gauge samples turn-end resting occupancy

**Refines the 2026-05-18 "Token usage piggy-backs on the done SSE event" decision.** The gauge is now computed at turn *end* over the resting context the next turn will load (this turn's saved messages with compaction applied), not the first LLM call's turn-start occupancy — so a heavy tool turn's carried-forward output is reflected, while compacted history correctly drops off (matching the UI's "drops when compacted" promise). Because turn-end has no authoritative API `prompt_tokens`, the tiktoken total is scaled by a calibration ratio captured on the first call; the resting recompute reuses `load_compacted_history` and discards its deferred LLM-summary callables (the turn-start load already schedules them). **Trade-off**: rejected the "sample the last LLM call" option (the pre-compaction peak — it overstates and contradicts the occupancy-not-spend contract) and accepted a calibrated estimate over an exact API number for the resting set.

### 2026-06-07 — Structural-IR diagram engine (third diagram path)

Adds `app/diagram_ir/`: a coordinate-free **Diagram IR** (containers/nodes/edges/adornments + style/layout tokens) → schema + referential-integrity validation → recursive box-layout (per-container row/column/grid hints) → MS-reference-style draw.io emit with Azure2/AWS4 icons → deterministic icon-avoiding, lane-separated connector routing (Hanan-grid A\*). Scoped to **containment-canonical** architecture diagrams; edge-topology graphs (branching flowcharts) stay with the Graphviz `generate_drawio_from_python` path, since this engine places boxes by containment + author hints and never infers position from edges. **Trade-off**: a second layout+routing engine to maintain, and placement-correctness moves to the prompt/style layer that emits the IR; accepted because Graphviz auto-layout can't produce Microsoft's grid-aligned nested layouts and a per-provider template library doesn't scale across Azure/AWS/GCP/generic. Not yet a registered Tool — `emit`/`validate`/`layout`/`route` are library functions; the Tool + authoring skill are a deliberate follow-up (landed 2026-06-08, see below).

**Layout-fidelity refinements (2026-06-08).** Two gaps that made output sub-Microsoft were closed in the layout pass: (1) a container now sizes to fit its *own* header label, not just its children — a subnet whose label (`Public subnet  us-east-1a`) is wider than its single icon child no longer clips; the widened box re-centers its children block. (2) A new author hint `align_to: <box-id>` (on Node/Container) lets a satellite service sit over the element it relates to instead of centering on the canvas. It is a *post-placement* shift on the axis perpendicular to flow (X for LR, Y for TB), clamped to the canvas margin, with a same-parent de-collision sweep so two satellites targeting nearby elements spread by `GAP` rather than stack. `align_to` is an **author hint, not edge inference** — it preserves the load-bearing "positions never come from edges" rule (the id is named explicitly, exactly like `layout`/`grid_cols`). Forcing it where it doesn't fit can regress routing: aligning three side-services onto one shared source stacked their fan-out and reintroduced C (arrow-overlap), so it stays opt-in per satellite.

**Now a registered Tool + two skills (2026-06-08).** `generate_structured_diagram` (`app/tools/generic/`, no approval — pure generation, flag `TOOL_STRUCTURED_DIAGRAM_ENABLED`) wires the library end-to-end: JSON IR → schema-load → hard-gate `validate_ir` (renders nothing on a broken IR) → layout → route → emit → write `.drawio` → render PNG (reuses `render_drawio_to_disk`) → reports the A/C scorecard + advisory warnings + attaches the PNG for vision review. The JSON loader/validator gained `align_to` (parsed; dangling ref is an advisory warning, not a hard error, since it's cosmetic). Two shared skills consume it: **`structured-diagrammer`** (diagram-only specialist) and **`structured-architect`** (the full Architect persona — ADR/WAF/Azure tools — but drawing via the IR engine instead of `generate_drawio_from_python`). Both, plus the tool, are granted to the **architect** RBAC tier (`DEFAULT_ACCESS_MAP`). The Azure icon catalog is deliberately narrow today (~10 Azure / ~18 AWS / ~10 generic shapes); the skills instruct the model to flag a missing icon and fall back to a `shape/*` builtin rather than invent a path the validator would reject.

### 2026-06-10 — Two-tier model routing: agent loop on high deployment

When `AZURE_OPENAI_DEPLOYMENT_HIGH` is configured, the main agent loop (the only place reasoning quality is user-visible) runs on it via a dedicated client (`_get_chat_client()`, own API version), while every auxiliary call — compaction summaries, tool-output compression, learning judge/extractor, risk review, greeting, KB rerank — stays on the base `AZURE_OPENAI_DEPLOYMENT`. The explicit `*_HIGH` context-window config wins over the substring table in `token_usage.py` (deployment names like `gpt-5.4` would match the 128K base-model entry and mis-report a 400K window); resolution lives in `Settings.chat_deployment / chat_api_version / chat_context_window`. **Trade-off**: rejected upgrading the single deployment (aux calls would burn high-tier quota for summarisation no one reads) and per-call dynamic routing (no signal to route on yet); accepted two clients and the risk that aux summaries are written by a weaker model than the one that consumes them.

### 2026-06-10 — Diagram engine models text: B/D scorecard + label placer

The dun_prod_traffic_flow render exposed the engine's blind spot: A=0/C=0 while the picture was unreadable, because every defect was TEXT — draw.io dropped edge labels at path midpoints onto node captions and each other, and routes lawfully crossed captions/container titles (only 56×56 icon boxes were obstacles). Labels are now first-class geometry (`diagram_ir/labels.py`): node-caption boxes and container title-text extents join the routing obstacle set, a deterministic placer walks each routed polyline (longest segment first, both perpendicular sides) and emits explicit draw.io edge-label positions, the scorecard gains **B (line-over-label)** and **D (label-collision)**, and A* gets a congestion penalty so a second edge takes the next open corridor instead of stacking into a channel lane-separation can't split. The tool auto-retries routing once with wider clearance when B/D are non-zero; the two structured skills gain edge-label discipline (≤3 words, no hedging, omit when the edge type says it, never restate containment). **Trade-off**: text metrics are CHAR_W-style estimates (placement is approximate, not font-measured) and detectors/router/straight-pass must share insets to avoid flagging accepted grazes; accepted because keeping text out of text is what readers notice, and `examples/dun_prod.py` pins the real failure (midpoint baseline D=6 → placed 0) as a regression fixture.

### 2026-06-10 — Concise-output contract; diagram ceremony collapsed to one gate

**Partially replaces the 2026-06-08 blueprint-phase decisions** (which mandated separate reflection / ask_user / blueprint turns with hard waits) and the "Thinking before acting" always-narrate policy. The framework prompt now carries an output-style contract (lead with the result, no tool-output restating, no narration of routine calls, lists only for enumerable items), and the structured skills collapse research→questions→blueprint into a single proposal-plus-confirmation message with sensible stated defaults — the multi-gate ceremony was insurance against gpt-5.4-mini silently dropping agreed structure, and on the high-tier loop model its cost (3+ round-trips before any render, essay-length turns) outweighed the risk. Engine mechanics (IR contract, icon catalog, layout doctrine) were deduplicated out of both skills into the tool description (one canonical copy, ~29 KB → 8 KB and ~14 KB → 4 KB), backed by validator close-match suggestions for unknown icons. Also fixed en route: the PNG attach/vision-review trigger was a hardcoded name tuple that silently missed `generate_structured_diagram` — now the `attaches_render` capability attribute, pinned in test_bundle_decoupling. **Trade-off**: less forced deliberation means a weaker model (or a regression in the strong one) could ship an unconfirmed structure again; accepted because "never silently simplify" + the 1:1-blueprint rule survive, and the gates can be re-tightened by editing two SKILL.md files.

### 2026-06-10 — Standalone teach capture; suppression-judge scope fix

**Amends the 2026-06-05 "User-correction learning capture" decision.** Conv #350's "please learn that - Rate limit for az_rest_api…" was dropped by three stacked gates: the teach-intent regex lacked the "(please) learn that/this" phrasing entirely; the orchestrator required a prior agent action in the same conversation (the teach OPENED the conversation, so it was silently skipped — and the agent then falsely claimed "Learned and adopted"); and the suppression judge mis-read "prefer Resource Graph first" as suppressing az_rest_api, though tool-ordering is the bundle's own documented hierarchy. Fixes: regex covers learn-that/this/learn:/save-as-a-learning; a standalone teach passes an explicit "(none — standalone teaching instruction)" prior-action instead of being dropped; the judge prompt now scopes suppression to SAFETY guidance/validators/approvals and explicitly approves operational limits + tool-selection guidance; the framework Learning-policy prompt tells the agent to acknowledge capture without claiming or denying the write. **Trade-off**: standalone teaches lose the contradiction context that anchored v1's precision; accepted because the regex stays explicit-phrasing-only and the extractor + judge + rephrase stack still gates every write.

### 2026-06-10 — Placement advisory: stages ordered by traffic position

The fft_prod review's second finding: APIM (hop 3 of the flow) was authored into the VNet stage parked at the END of the spine, so every hop through it crossed the canvas and back — a placement problem no router can fix, and the "positions never come from edges" rule (correctly) forbids the engine from fixing it by moving boxes. The resolution keeps the rule but closes the feedback loop: `check_flow_placement` ranks nodes by BFS depth from the PRIMARY flow entry (largest-reach root only — side stories like an async Logic Apps loop don't pollute ranks), flags flow/private edges whose endpoints are rank-adjacent yet drawn >600px apart, and suggests the traffic-ordered stage arrangement; the tool prints it as a "Placement advisory (fix via `edits`)" and the doctrine (tool description + both skills) now states: order stages by traffic position, not category — a VNet hosting a mid-flow hop is a MIDDLE stage; split stages whose members span very different flow positions. On the real fft IR: 8 far-hops flagged; re-authored to `edge → apps → vnet → data → services → monitoring` it drops to 1 (the genuinely cross-cutting partner-API callout). **Trade-off**: advisory-only means a model that ignores it ships the round-trips anyway; accepted because edge-driven auto-placement would silently destroy containment-canonical layouts, and the advisory text names the exact fix.

### 2026-06-10 — Route-quality cost model: gutters over foreign interiors

A render can score A=B=C=D=0 and still read badly: the fft_prod review showed six long edges spending 3,494px combined INSIDE containers unrelated to them (a line through a box reads as a relationship with that box), hugging borders (a line on a border reads as belonging to it), and forming a 4-wire parallel loom. The A* router now shapes cost instead of only blocking collisions: foreign-interior transit costs +0.9/px (penalty, not prohibition — a wall-spanning container is still crossable), running parallel within 8px of a container border costs +1.2/px, gutter corridor lines 12px outside every drawn container join the grid (without them "just outside the box" wasn't a choosable channel), straight-first rejects diagonals with >80px foreign transit, and clusters of long parallel runs get double lane spacing. fft fixture: 3,494→1,901px transit (the rest is one rational crossing of a full-height zone), max bends 7→4, two private edges fully gutter-clean. **Trade-off**: routes get somewhat longer and the cost model has tuned constants (rates/gutter width) rather than principles; accepted because the readable channel beats the short diagonal, and `test_route_quality.py` pins avoid/cross/straight-reject behavior plus a transit bound on the dun fixture.

### 2026-06-10 — Diagram iteration via stored-IR edits + structural echo

Conv #352 took 12 renders to converge because the tool was regenerate-from-scratch: every iteration re-emitted the full IR from memory (a fresh LLM sample — nodes drawn in attempt 3 vanished in attempt 4), and the model verified structure by reading the downscaled PNG, hallucinating absences that triggered more re-rolls. `generate_structured_diagram` now persists the accepted IR as `output/<stem>.ir.json` on every successful render and takes an `edits` parameter (upsert/remove node/container/edge, set — parent↔children auto-synced, first error aborts the batch) so iteration 2+ is a small delta against structure that cannot drift; the result also carries a **Structure echo** (all container/node/edge ids) declared authoritative for presence/absence, demoting the image to visual-quality review. Adornment labels also joined the label-geometry model (node badges render their text beside the glyph instead of onto the owner's icon — the "WAF on Front Door" defect — and count as placer/router obstacles + B/D detection). **Trade-off**: the sidecar is server-side state the model must trust (mitigated: it always matches the last-rendered .drawio, and a full `diagram` still works and overwrites it); `children` on upsert_container REPLACES the list, accepted for determinism over merge ambiguity.

### 2026-06-10 — Token-budget compaction + search_conversation recall

**Replaces the chars/count compaction triggers (30 messages / 12K chars) with a token budget**: history may occupy `COMPACT_THRESHOLD_FRACTION` (0.5) of the chat model's context window (tiktoken-estimated) before any compression, with the recent verbatim window at 40 messages and the row cap at 200. The old thresholds (~3K tokens) compacted nearly every tool-using conversation from turn two, so the agent ran on lossy 800-char summaries while >95% of the window sat empty — the dominant cause of "the agent forgot what we did". Compaction is now framed as cache eviction with a recovery path: the new `search_conversation` tool (generic, no approval, conversation-scoped via the request ContextVar) searches the full message rows in SQLite, so compacted/truncated details are retrievable on demand instead of needing lossless summaries. The same budget reasoning raised the tool-result LLM-summarisation threshold (2 KB → 16 KB), the per-tool `result_limit` caps (~3x), and `USER_PASTE_THRESHOLD` (3 KB → 20 KB) — verbatim beats compressed whenever affordable. **Trade-off**: higher per-turn prompt cost and slower cache-prefix turnover, accepted because answer fidelity was the user-visible pain; compaction LLM calls also moved off the event loop (`asyncio.to_thread`) since at the new scale a compaction pass is rarer but heavier.

### 2026-06-10 — `sleep` tool: block a worker thread as backpressure

Added a `sleep` tool (1–120s, no approval) so the model can wait out a rate-limit/throttle window and retry the *same* action instead of abandoning the approach, switching tools, or punting to the user. It runs on the tool executor and blocks with `time.sleep()`, deliberately holding one of the user's 4 concurrency slots for the duration; the per-call cap forces a longer wait to be several visible conversation steps rather than one silent multi-minute park. **Trade-off**: rejected an `asyncio.sleep` that frees the slot (frictionless waiting invites the model to park turns and hides the cost) and a no-op "advisory wait" hint (the model had no actual way to wait); accepted that a sleeping thread consumes real concurrency, because that occupancy *is* the backpressure that keeps waits short and honest.

### 2026-06-11 — Diagram convergence stack: edits ergonomics, review governor, graceful iteration cap

Conv #355 forensics: ~28 `generate_structured_diagram` calls across two turns, both killed by `MAX_TOOL_ITERATIONS` with a dead "Maximum tool call iterations exceeded" error. Three fixes. **(1) Edits ergonomics** — 7 of 28 calls were the same two API-shape errors: `remove_container` now *dissolves* (children re-parent to the grandparent; "must be empty first" always meant a 2-iteration move-then-remove dance for the same intent), and an edit batch is a transaction (a pre-pass creates shells for ids a later op in the same batch defines, so `children` may forward-reference them). **(2) Review convergence governor** — 21 consecutive *successful* renders were each vision-reviewed into "actual problems"; an open-ended "fix what's wrong" review never converges because a vision pass can always find a cosmetic flaw. The orchestrator counts successful renders per filename per turn: from 3 the review message demands semantic-only fixes and ONE global spacing change over per-node nudges; from 5 it instructs presenting the render as-is. **(3) Graceful iteration cap** — hitting the cap now makes one final tools-disabled LLM call producing a persisted checkpoint summary WITH the latest render attached (previously lost: attachments only shipped on a natural final message), then emits `iteration_limit` + `done`; the frontend renders a Continue button that sends a plain "continue" (resumption works — all tool results are persisted mid-loop). **Trade-off**: the governor can stop one render short of perfect; accepted because the user can always ask for the next fix, while an unconverged loop costs the whole turn.

### 2026-06-11 — Human-diagrammer layout heuristics: side-lane advisory, trunk bundling, flow-axis ports

Three heuristics transcribed from how the user hand-draws diagrams. **(1) Side-lane advisory** (`geometry.check_side_lane`): a node with 3+ edges spanning 2+ other stages that is NOT itself a hop on the primary flow (DNS, identity) yet sits buried in a stage that hosts flow hops gets flagged with the structural recipe — invisible `band` beside the spine + `align_to` its busiest counterpart. Advisory-only, same reasoning as the placement advisory: the no-edge-driven-placement rule stands. **(2) Fan trunk bundling** (`routing._bundle_fan_routes`): edges sharing an endpoint, a type, NO label, and the same face pair route as one trunk that splits comb-style just before the branch faces ("-E"), exactly colinear on the shared run — previously the congestion penalty + lane separation deliberately tore fans into parallel looms (right for unrelated edges, wrong for fans). All-straight fans stay straight; combs that can't route obstacle-free fall back to individual routing; bundles are exempt from lane separation and from the C detector when edges share the bundled endpoint. **(3) Flow-axis port bias** (`route_edges`): forward flow/private edges within a 1.6× ambiguity band keep the diagram's reading-axis ports (LR: right→left) instead of flipping to vertical mid-story; backward edges and overlay types keep plain dominant-axis. **Trade-off**: bundling renders N stacked lines as one (a reader can't count fan members on the trunk — that's the point); the bias constant is tuned, not principled; both pinned by `test_route_quality.py`.

### 2026-06-11 — Exact text metrics: baked Arial advance tables replace CHAR_W

**Refines the 2026-06-10 "Diagram engine models text" decision**, which accepted CHAR_W-style estimates as its trade-off. `diagram_ir/textmetrics.py` is now the single text oracle: per-character Arial advance widths (regular + bold) extracted offline by `scripts/gen_font_advances.py` and committed as data, measured at the exact px sizes the emitter writes (node 12, container header 11/12 bold parsed from the catalog style, edge label 10, node adornment 10) — draw.io's Helvetica renders as Arial on Windows/Chromium, so these ARE the painted pixels. The placer, router, layout, and detectors keep sharing one geometry model, just a true one; `dun_prod`'s midpoint-label baseline pin dropped 6→2 because two "collisions" were estimate-only phantoms. **Trade-off**: rejected runtime font measurement (a Pillow/fontTools runtime dependency and a non-deterministic font-resolution path) in favour of a baked table that assumes Arial-metric rendering; a viewer on a Helvetica-real platform sees ~2% narrower text, accepted as inside the layout's padding slack.

### 2026-06-11 — Archetype skeleton library: tested starter IRs in the KB

Most platform diagrams are one of a handful of stories, and the expensive decisions (band structure, spine direction, side-lane placement) repeat — so six detector-clean starter IRs (`n-tier-web-app`, `hub-spoke-network`, `event-driven`, `rag-ai-app`, `cicd-flow`, `landing-zone`) now live in `kb/patterns/diagram-archetypes.md`, and both structured skills instruct: classify the ask, copy the matching skeleton as the blueprint base, rename/replace slot nodes, then add specifics. The skeletons encode forward what the side-lane/placement advisories reverse-engineer one flag at a time. They live in the KB (not engine code) because the agent reads them at runtime with `read_kb_file`; `diagram_ir/archetypes.py` parses the SAME document for `tests/test_archetypes.py`, which gates every skeleton through the full pipeline + all detectors + advisories at zero defects. **Trade-off**: tests now depend on KB content (a KB edit can fail the suite — deliberate: a dirty template poisons every diagram derived from it), and v1 relies on the model copying faithfully rather than an engine-enforced `archetype` parameter; accepted because skill-level shipping is zero engine risk and the engine-side loader is the seam for v2 enforcement.

### 2026-06-11 — Semantic graph/view layer above the IR; ARG import

Adds `app/diagram_model/`: a `SemanticGraph` (resources with provider metadata + typed relations, richer than any one picture) and `View`s (include/exclude/collapse) whose deterministic `project()` emits the IR authoring contract — so an L0 overview and its L1 drill-downs are projections of ONE model and cannot drift, with collapse folding a subtree into a counted node and re-targeting its relations. `azure_import.from_resource_graph()` builds the graph from rows an `az_resource_graph` query already returns (containment, VNet subnets, private-endpoint re-parenting + private-link relations, deduped peerings), with deterministic ids so re-imports keep existing Views valid — shifting the agent from *inventing* topology to *curating* it. The IR stays untouched as the render contract; the layer is purely additive above it. **Trade-off**: rejected enriching the IR itself with levels-of-detail (would entangle the render contract with curation policy and break the stored-IR edits flow); v1 ships as library only — the `import_azure_topology` tool wiring is a deliberate follow-up, and until then the layer has no user-visible entry point.

### 2026-06-12 — Floor remote-exec az commands to ⛔ via a Tool risk-floor hook

`az vm run-command invoke` (and the `aks command invoke` / `container exec` / `webapp ssh` / `ssh vm` / `acr run` family) passes an arbitrary command string to remote compute, re-opening the exact code-execution surface the 2026-05-22 `run_shell` retirement claimed to close by "structural impossibility" — so these now floor to ⛔ destructive in the risk reviewer, matched by contiguous-subcommand sequence in `bundles/azure/az_cli.py` and read by `risk_review.deterministic_floor` through a duck-typed `Tool.risk_floor()` hook resolved via the registry (no core→bundle import). Floored to ⛔ rather than hard-blocked like `role assignment delete` because the command runs on the user's own resource as the user's own ARM token — RCE they already possess — so the proportionate control is forcing a careful read, not denial. **Trade-off**: a floor-list is the leaky blocklist the 2026-05-22 entry rejected, so the enumerated family is only the deterministic floor — the separate review LLM still escalates the unenumerated tail and fails closed to ⚠ — and rejected both a hard block (removes a legitimate capability) and a static `from bundles.azure` import in core (reverses the bundle→core arrow).

### 2026-06-12 — Reviewer reads resolved az_rest bodies; oversized payload → ⛔

The advisory risk reviewer judged `az_rest_api` mutations blind — `render_command` showed only the `body_file` *filename*, so a payload opening public network access or assigning Owner was never seen (the 2026-06-04 reviewer reads script contents but not REST bodies). A duck-typed `Tool.render_for_review(func_args)` hook, resolved via the registry exactly like the 2026-06-12 `risk_floor` hook (no core→bundle import), now lets each tool resolve and inline its own body for the reviewer; `AzRestApiTool` reuses its `_resolve_body_file` guard and shows up to a 16 KB window. A body exceeding the window floors to ⛔ via `risk_floor` (size-checked by stat/len, never re-read) with a visible truncation marker, so content we cannot fully review escalates rather than passing silently at ⚠. **Trade-off**: a genuinely large benign deployment template pushed through `az_rest_api` now forces a ⛔ confirmation — accepted because an unreviewable mutation payload deserves a careful read; rejected window+marker-only (a key buried past the cap would stay ⚠) and core resolving the body itself (reverses the bundle→core arrow). The same window+floor rule is the planned fix for the `execute_script` 4000-char truncation (#14) once it adopts the hook.

### 2026-06-12 — execute_script adopts the review hook; over-window script → ⛔

`render_command` showed the review LLM only `body[:4000]` of a script, so a destructive operation NOT in `_shell_floor`'s substring list and buried past char 4000 blinded the LLM tail-net (the deterministic `_shell_floor` already scans the full body, so it was unaffected). `execute_script` now implements the same `render_for_review` + `risk_floor` hooks as `az_rest_api`: the reviewer sees the body up to a 16 KB window with a truncation marker, and an over-window script floors to ⛔ via `risk_floor` (stat-checked) so an unreviewable-length body escalates instead of passing on a partial view. `_shell_floor` stays in `risk_review` (generic shell-pattern knowledge, and `execute_script` is a generic tool so no bundle→core arrow forces a move); only the trivial over-window rule lives in the tool, keeping the diff to security behaviour rather than a refactor. **Trade-off**: this fixes what the *reviewer* sees, not the *human card* — `formatCommand` still shows only the script path (#18, deliberately deferred), so the human gets an accurate ⛔ + second-confirmation but still cannot read an oversized body on the card until #18 sends a backend-resolved render.

### 2026-06-12 — Approval card shows backend-rendered command; download for >64 KB

The human approval card reconstructed the command from raw `args` via the frontend `formatCommand`, so it showed a *pointer* (`execute_script` path, `az_rest_api` body_file filename) — the human approved content they could not see, even after #12–#14 made the *reviewer* see it. The backend now emits a deterministic `rendered_command` (the same registry-resolved render the reviewer uses, NO LLM, on the pending emit so it shows immediately) on `approval_required`, capped at 64 KB inline with a `command_truncated` flag; the card displays it (falling back to `formatCommand` when absent) and offers a Download button → a new user-scoped `GET /api/approvals/{id}/command` that re-derives the uncapped command from `tool_args_json`. One resolver serves all three consumers via a `max_bytes` parameter — reviewer 16 KB (LLM budget), card 64 KB (display only), download uncapped — so the human's view of what they approve is deterministic and complete, not a pointer. **Trade-off**: adds SSE fields + an API route + frontend rendering (hard-to-reverse surfaces), kept reversible via additive fields with a `formatCommand` fallback and re-deriving from stored args (no DB schema change); rejected extending `serve_output` (can't serve an inline body, which has no file on disk). Surfacing the agent's `reason` on the card was considered and dropped — it contradicts the standing "reason is audit-only; the card shows `risk_description`" rule (models.py `pending_approvals.reason`), which stays in force.

### 2026-06-13 — az_cli converges on shared shell=False + env-allowlist runner

`az_cli` now executes through a shared Popen-based runner (env allowlist lifted out of `_run_az` as `_az_env()` in `_az_base`), removing the last `shell=True`-on-Windows + full-`os.environ` subprocess in the codebase; `AzCliTool` deliberately still does NOT inherit `AzureToolBase`, so it keeps its own `require_az_login` preflight, stays out of the §5 2026-05-21 ARM-token preflight, and `_run_az`'s string contract is untouched. `execute()` is now a drain of `execute_streaming()` — the orchestrator only ever dispatches the streaming path, and the parallel `execute()` copy is where a dead 60s timeout hid while production ran unprotected. **Trade-off**: rejected routing az_cli through `_run_az` itself (it can't stream, inheritance flips credential semantics, and az_cli's `Error:`-prefixed exit-code+stdout contract feeds retry/learning detection); rejected copy-pasting the allowlist (re-forks the 2026-05-21 hardening — the exact drift that created this gap).

### 2026-06-13 — Wall-clock watchdog + kill registration for streaming subprocess tools

Streaming subprocess tools (`az_cli`, `execute_script`) register their `Popen` in the per-conversation kill registry and arm a wall-clock `threading.Timer` (`ProcessWatchdog` + `stream_subprocess` in core `base.py`) that kills the process tree at the deadline — the blocked pipe read hits EOF and the generator unwinds; previously `for line in proc.stdout` blocked forever, `proc.wait(timeout)` never ran, and a hung command permanently pinned a tool-executor thread and a user semaphore slot. A timer-set flag distinguishes timeout-kill (retryable `Error: ... timed out`) from Stop-kill (terminal, per §5 2026-06-04). **Refines the 2026-06-04 kill-switch decision**: registering `az_cli` is kill-as-resource-hygiene; the "killing local az can't recall an ARM dispatch" false-safety caveat remains true and is not the claim here. **Trade-off**: wall-clock deadline, not idle-detection (a reader-thread+queue could detect silence but still needs the tree-kill anyway); timeout stays fixed at 60s — configurability is backlog #11.

### 2026-06-13 — az risk classification fully owned by AzCliTool.risk_floor

The floor's az-specific knowledge (`_AZ_DESTRUCTIVE_TOKENS` / `_AZ_READ_VERBS`, a pre-#12 leftover in core `risk_review`) moves into `AzCliTool.risk_floor`, whose contract widens from `destructive | None` to *any* tier; core's `deterministic_floor` az_cli branch collapses to `_tool_risk_floor(...) or CAUTION`, finishing the bundle→core decoupling #12 began. New rules: privilege-escalation grants (`role assignment|definition create|update`) floor ⛔ — same own-resource-own-token logic as #12's remote-exec, so floored not blocked (the lock-out `_BLOCKED_PREFIXES` list is unchanged); credential-reads (`keyvault secret show`, `storage/cosmosdb/redis` key reads) floor ⚠, overriding the read-verb SAFE shortcut. Power-state (`stop`/`deallocate`) stays ⚠ via the default and security-disabling `update`s are left to the review LLM — neither is floorable by verb token alone. **Trade-off**: a bundle hook can now floor to SAFE (authorize the LLM's verdict), accepted because the bundle is trusted first-party code and core already trusts its DESTRUCTIVE verdict; rejected extending the core token sets (deepens the leak #12 closes and can't express resource-specific nuance like "create is dangerous for role but routine for vm").

### 2026-06-13 — Secrets masked from the judge LLM, card, and persisted history

Credential material reached three sinks unmasked: secret-bearing args (`--value` / `--password` / `--admin-password` / connection strings) flowed verbatim to the review LLM and the approval card via `render_command`, and credential-read *output* (`keyvault secret show`) was persisted to the `messages` table and replayed to later turns. Two duck-typed Tool hooks fix this with no core→bundle import: `AzCliTool.render_for_review` masks sensitive args (display-only — execution reads `func_args` directly), covering judge LLM + card + download + stored `tool_calls` (via `mask_args` at the `tool_calls_json` write); a new `redact_output` hook, triggered by the same `_CREDENTIAL_READ_PREFIXES` set that drives the ⚠ floor, replaces credential-read results with a marker before they are saved or replayed. The live SSE stream and the current turn's in-memory history keep the real value, so the user receives the secret they asked for and the agent can chain within the turn; only the persisted and judge-facing copies are masked, and future turns rebuild masked history from the DB. **Trade-off**: future turns can't replay a previously-fetched secret (the agent re-fetches, re-approving) — accepted because persisting credentials plaintext is the larger risk; rejected field-level JSON masking (format-dependent on `-o json`) in favour of whole-body redaction, since a credential-read's entire output is the secret.

### 2026-06-14 — Per-user weekly spend cap via an append-only usage ledger

**Extends the 2026-05-18 "token usage piggy-backs on the done event, not persisted"
decision** — the `usage` object is now also written per LLM call to a new
`usage_events` ledger (`user_oid, conversation_id, deployment, prompt/cached/completion
tokens, created_at`), making cumulative per-user spend durable. A hard per-user weekly
cap (nullable `users.credit_cap_usd`, NULL → Entra-role default from §5 2026-05-17, set
via an architect-gated `PATCH /api/users/{oid}` mirroring §5 2026-05-20) is enforced
pre-flight and at the top of each agent-loop iteration; crossing it stops the turn via
the §5 2026-06-11 graceful-checkpoint path, bounding overspend to one iteration. Remaining
budget is two windowed SUMs over the ledger — `cap − max(0, last_week_overspend) −
this_week_spend` — so the fixed weekly period needs no reset job and debt carries exactly
one week forward; tokens+deployment are stored (not dollars) so a price or deployment-tier
change doesn't strand history (the §5 2026-05-15 embed_model lesson). A turn-start block
reuses the existing `error` SSE event with a distinct code — no new event type, per §5
2026-05-18 / 2026-05-21. **Trade-off**: rejected a mutable counter column (reset race, no
attribution) and a debit/credit wallet (reintroduces a periodic grant job, banks surplus
unless clamped); accepted that underspend never rolls forward and multi-week compounding
debt is unrepresentable — both immaterial because the graceful stop keeps overspend to one
iteration.

### 2026-06-14 — execute_script is an Azure-credential-free zone

`execute_script` deliberately keeps the user's ARM token out of its subprocess
env (unlike `az_cli`'s `_az_env`) and sets `AZURE_CONFIG_DIR` to a fresh
per-invocation throwaway dir, so a script's `az` finds no cached login and fails
closed identically in dev and prod — Azure work must flow through `az_cli`, the
only path that carries identity behind the blocked-prefix / risk-floor /
credential-masking guards. The rejected alternative, symmetric injection (hand
scripts the user token like `az_cli`), was unacceptable because an approved
script is an opaque blob that could exfiltrate the bearer token to the output
sandbox or run `az role assignment create`, bypassing every `az_cli` guard —
expanding the token's blast radius from one reviewed command to anything an
approved script does. A test guard asserts `AZURE_ACCESS_TOKEN` never enters the
script env so a future "make it symmetric" PR can't silently regress it; and
because `AZURE_CONFIG_DIR` cannot stop `az login --identity` from reaching IMDS,
any server managed identity added later (e.g. for a separate Azure vector DB)
must be data-plane-scoped only, never granted ARM/management rights.
**Trade-off**: a script that genuinely needs multi-step `az` work can't do it
inline — it must decompose into discrete `az_cli` calls — accepted because that
is exactly what restores per-command review, masking, and user attribution.
Closes hardening backlog #19.

### 2026-06-15 — az_cli `@file` args are sandbox-bounded, fingerprinted reads

az's `@file` convention lets any `az_cli` argument load its value from disk at
execution time (`--scripts @output/x.ps1`), so we hard-reject in
`execute_streaming` any `@`-token that resolves outside `output/` (reusing
az_rest's `_resolve_body_file`, lifted to `_az_base`), rewrite the survivor to
its absolute path so az and the reviewer read the same bytes, and carve out
`--query`/`-q` (JMESPath owns `@`). az_cli gains a `review_fingerprint` hook
(sha256 over all resolved `@file` bytes) so the #20 pre-execute re-check aborts
on a swap between approval and run. Resolved content is shown only to the human
approval card; the judge LLM and stored `tool_calls_json` get the
pointer + fingerprint, never the bytes — extending the #16 surface split so
file-borne secrets reach neither the judge nor the DB.
**Trade-off**: the human card and judge now review *different* renders (content
vs. pointer), and a post-approval file swap aborts the turn rather than running
reviewed-but-stale bytes; we rejected inlining the content into the command
(which would eliminate the TOCTOU outright) because it reintroduces the
large-payload escaping corruption and command-line-length limits that
`@file`/`body_file` exist to avoid. Closes hardening backlog "@file indirection".

---

## 6. Operations

### Running locally

```bash
# Backend  (port 8000)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# Frontend (port 5174)
cd frontend
npm install
npm run dev
```

Both `.env` files (`backend/.env`, `frontend/.env`) must exist. `DEV_AUTH_BYPASS=true`
short-circuits Entra auth in dev to a fake `dev-user` identity AND short-circuits
the role-based access filter (see 2026-05-17 §5 entry) so local development
sees every shared skill and every tool regardless of Entra roles.

### Deployment env vars worth knowing about

These two are unset by default and only matter once you're running against real Entra:

| Var | Effect |
|---|---|
| `AZURE_APPCONFIG_ENDPOINT` | App Configuration resource URL (e.g. `https://nexus-config.azconfig.io`). When set, the lifespan handler reads `Nexus:RoleAccessMap` (JSON value) at startup and replaces the in-process `_ACCESS_MAP` in `app/auth/rbac.py`. Unset = hardcoded defaults stand. Requires the Container App's Managed Identity to have the `App Configuration Data Reader` role. |
| `AZURE_APPCONFIG_ROLE_KEY` | Override the App Configuration key name (default `Nexus:RoleAccessMap`). Useful for parallel dev/prod role maps in one App Configuration resource. |

### Tests

```bash
cd backend && python -m pytest tests/ -x -q   # 595 tests as of 2026-05-20
cd frontend && npm test                       # 109 tests
```

### Background tasks (started in [main.py `lifespan`](../backend/app/main.py))

| Task | What | Cadence |
|---|---|---|
| `start_periodic_sync` | Git-pull KB repo, normalize, then trigger reindex | `KB_SYNC_INTERVAL_SECONDS` (default 15 min) |
| `_approval_sweeper` | Expire stale `pending_approvals` + `pending_questions` | Every 60 s |
| `_backup_loop` | Snapshot `app.db` to `app-db-<ts>.db` | `BACKUP_INTERVAL_SECONDS` (default 24 h); off by default |
| KB reindex (Phase 2) | Diff per file by sha256; chunk + embed + upsert changed files | After each `sync_repo()` + on startup (background) |
| Agent-learnings reembed | Embed any `agent_learnings` row with `embed_model IS NULL` (after orchestrator writes; also one batch sweep at startup after the legacy-learn.md migration) | Inline after each write (limit=1); batch of 200 at startup |
| `_usage_ledger_prune` | Delete `usage_events` rows older than the longest reporting window (default 90 days) so the spend ledger doesn't grow unbounded | `USAGE_LEDGER_PRUNE_INTERVAL_SECONDS` (default 24 h) |

### Health endpoints

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness — always returns 200 if the process is up |
| `GET /metrics` | Prometheus scrape — request counts, durations, token usage, tool calls |
| `GET /api/kb/index/status` *(Phase 2)* | Hybrid-retrieval index progress (state, indexed/total files, errors) |
| `POST /api/kb/index/rebuild` *(Phase 2)* | Force a full re-embed (use after model swap or chunker change) |

### KB reindex — when to force a full rebuild

The reindexer skips a file when its content hash (`sha256` of the raw file
bytes) has not changed since the last run. It also detects an embedding-model
swap via the `embed_model` column and forces a full re-embed automatically.

**It does NOT auto-detect chunker config changes.** If you change either of
these settings in `.env`, you must trigger a manual rebuild:

- `KB_CHUNK_MAX_CHARS` — maximum characters per chunk
- `KB_CHUNK_OVERLAP_FRACTION` — how much of the previous chunk to carry forward

Without a rebuild the DB silently retains chunks cut at the old boundaries and
`search_kb_hybrid` keeps returning them. Run:

```bash
curl -X POST http://localhost:8000/api/kb/index/rebuild
```

or call `GET http://localhost:8000/api/kb/index/status` to confirm
`state == "complete"` after the rebuild finishes.

### Chat deployment swap — update the context-window setting

`AZURE_OPENAI_CONTEXT_WINDOW_TOKENS` (default `128000`) is the denominator the
frontend context-usage indicator divides into. It is **not** auto-detected from
the deployment — when you change `AZURE_OPENAI_DEPLOYMENT` in `.env`, update
this setting in the same edit. Wrong value just mis-scales the indicator (the
backend never enforces it as a hard cap), so a deploy that forgets the update
will look like it's using less context than it actually is.

### ADO wiki source labels — convention and immutability

Each entry in `INGEST_ADO_WIKI_SOURCES` carries a stable user-chosen
`label` (regex `^[a-z][a-z0-9-]{1,39}$`, unique across the list). The
label is Nexus's internal identity for that source instance — it names
the on-disk subdirectory `kb_data/kb/ado_wiki/<label>/`, the
`source_instance` column on every chunk, and the `_source_meta.json`
sentinel that pins the `(org, project, wiki)` triple bound to it.

**Convention**: the label is a slug of the ADO project name. If a project
hosts multiple wikis (project wiki + N code wikis), append a stable
disambiguator (e.g. `-docs`, `-code`). The label is your identity choice,
not a derivation — Nexus does not enforce the convention.

**Immutability**: once a deployment is live with `label: "platform"`, you
cannot change it without a manual reindex. The sentinel file detects
accidental label rebinds (`label: "platform"` pointing at a different
`(org, project, wiki)` triple than last sync) and aborts that source's
ingestion with an actionable error rather than silently swapping
content. If a rebind is intentional, delete the corresponding label
directory under `kb_data/kb/ado_wiki/` and let the next sync repopulate.

### Concurrency assumption
Currently single-process (one uvicorn worker). The KB re-indexer uses an
in-process `threading.Lock` to prevent overlapping runs. If we ever scale
to multiple workers, we'll need a DB-level advisory lock or to pin reindex
to worker 0. Logged here so a future change doesn't quietly break
indexing.

The per-user spend cap (§5 2026-06-14) also leans on this assumption: the
pre-flight `SUM` + ledger write is only atomic under one process. Multi-worker
would let two concurrent turns each read "under cap" and both spend past it —
the same process-local-state gap already flagged for the circuit breaker
(§5 2026-05-21). A scale-out needs a shared/transactional read-then-write
(e.g. a SQLite `BEGIN IMMEDIATE` around the gate, or a shared store) or the
budget leaks per concurrent turn.

### Logs to watch
- `Token usage — prompt: N (cached: M, X%), completion: K` — Azure OpenAI cache hit rate per turn
- `Compacted N older msgs ...` — compaction firing
- `Cached text_summary for msg N` / `Cached image_summary for msg N` — message-level compression
- `KB schema DDL skipped: ...` — sqlite-vec extension didn't load (check Python build)
- `sqlite-vec load failed: ...` — `search_kb_hybrid` is disabled this session

---

## 7. Open questions / future work

- **Per-document access control**: currently all KB chunks are globally
  readable. Importing ADO content with restricted permissions will need an
  ACL column on `kb_chunks` + query-time Entra group check.
- **Multi-worker deployment**: see §6 concurrency note.
- **OCR for scanned PDFs**: not currently needed (corpus is born-digital);
  add Tesseract path when first scanned PDF appears.
- **Retire cloud `search_kb_semantic`**: after the local path is validated
  on real ingested content with a golden-set comparison.
- **DSPy refactor** *(evaluated 2026-05-25, deferred)*: walked through five
  candidate use cases (compaction summarizer, query expansion, drawio codegen,
  narration nudge, hybrid LLM-judge rerank) and concluded none currently earn
  the framework cost. Reasoning: no demonstrated user pain on the existing
  LLM-call sites; framework-creep risk parallel to the §5 2026-04-22
  LangChain/LangGraph rejection; compiled-artefact opacity breaks `git diff` /
  `git blame` as review tools; re-compile drift on model/chunker changes has
  no auto-detection. The unique DSPy value (`BootstrapFewShot` auto-mining of
  few-shot examples) is overkill for single-step request-response LLM calls
  where hand-picked few-shots in the prompt capture most of the gain.
  **Re-evaluation trigger:** revisit when Nexus is in production with
  significant scenario coverage AND a specific LLM-call site has produced
  documented user pain that prompt iteration + hand-picked few-shots cannot
  fix, OR a new multi-step LLM pipeline emerges where joint optimisation
  beats per-step tuning. The deferral summary lives at
  [IdeasTodo/dspy-coverage-tracker.md](../IdeasTodo/dspy-coverage-tracker.md).
- **Full corpus ingestion** (1000 wiki + 100 PDF): same code as the pilot,
  just turn on more wiki spaces / longer link lists.
