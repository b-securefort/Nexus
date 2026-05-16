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
   │ + pdf │       (planned        │   │  ms_docs, learnings    ││
   └───────┘        ingestion)     │   └────────────────────────┘│
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
record.

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

### Skills system
**Files**: [backend/app/skills/](../backend/app/skills/), `kb_data/skills/shared/<skill>/SKILL.md`

A skill is a YAML-frontmatter markdown file specifying a `display_name`,
`description`, `system_prompt`, and a `tools:` allowlist. Switching skills
swaps the agent's persona and scoped toolset. Personal skills live in the
`personal_skills` table; shared skills live in the synced KB repo.

### Tools

| Tool | Approval | Purpose |
|---|---|---|
| `read_kb_file` | No | Read a KB file by relative path |
| `search_kb` | No | Token-scored search over titles/summaries/tags |
| `search_kb_semantic` | No | **Cloud** path: Azure-OpenAI query expansion + rerank over file-level index. Kept side-by-side with `search_kb_hybrid`. |
| `search_kb_hybrid` *(Phase 2)* | No | **Local** path: chunked hybrid retrieval, no cloud calls |
| `fetch_ms_docs` | No | Microsoft Learn doc search |
| `read_learnings` | No | Read the agent's persistent `learn.md` |
| `update_learnings` | No | Append a categorized learning entry |
| `az_resource_graph` | No | KQL queries against Azure Resource Graph |
| `az_cost_query` | No | Cost Management API queries |
| `az_monitor_logs` | No | Log Analytics KQL queries |
| `az_advisor` / `az_policy_check` | No | Advisor recs and policy compliance |
| `az_cli` | **Yes** | General Azure CLI commands |
| `az_rest_api` | GET=No / mutations=Yes | Direct ARM REST calls |
| `az_devops` | Read=No / mutations=Yes | ADO pipelines/PRs/builds |
| `run_shell` | **Yes** | PowerShell / shell commands |
| `network_test` | No | DNS / TCP / ping diagnostics |
| `generate_file` | No | Write artifacts (bicep, csv, etc.) to `output/` sandbox |
| `validate_drawio` / `render_drawio` / `patch_drawio_cell` | No | Diagram authoring + validation |
| `python_diagram` / `drawio_from_python` | No | Diagram-as-code → drawio |
| `web_fetch` | No | HTTP GET for documentation URLs |
| `ask_user` | No (pauses for UI) | Surface options to the user via the UI; resumes on answer |

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
If the token is absent (user hasn't consented the ARM scope yet, or
`DEV_AUTH_BYPASS=true`), tools fall back to whatever credentials are in the
server's `az` CLI session — no error, just no user identity.

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
| `users` | oid, email, display_name, last_seen_at | auth middleware | everywhere | forever |
| `conversations` | user_oid, title, skill_id, skill_snapshot_json, summary_text, summary_through_message_id | api/conversations, compaction | orchestrator | until user deletes |
| `messages` | conversation_id, role, content, tool_calls_json, tool_call_id, attachments_json, text_summary, image_summary | orchestrator, compaction | orchestrator (history + compaction) | until conversation deleted |
| `pending_approvals` | tool_name, tool_args_json, reason, status | orchestrator | api/chat | expire via sweeper after 10 min |
| `pending_questions` | conversation_id, questions_json, status, answers_json | orchestrator (ask_user) | api/chat | expire via sweeper |
| `personal_skills` | user_oid, name, system_prompt, tools_json | api/skills | skills loader | until user deletes |
| **`kb_chunks`** *(Phase 2)* | kb_path, chunk_idx, heading, text, content_hash, file_mtime, source_url, embed_model | KB reindexer | search_kb_hybrid | until source file removed/changed |
| **`kb_chunks_fts`** *(virtual)* | FTS5 over `kb_chunks.text + heading`, `tokenize=unicode61` | triggers on `kb_chunks` | search_kb_hybrid (BM25 stage) | n/a |
| **`kb_chunks_vec`** *(virtual)* | vec0(float[1536]), joined by rowid==kb_chunks.id — 1536 dims matches Azure OpenAI `text-embedding-3-small` | reindexer (explicit) | search_kb_hybrid (vector stage) | n/a |

WAL mode is enabled on every new SQLite connection by
[sqlite_vec_loader.py](../backend/app/db/sqlite_vec_loader.py) so periodic
KB re-indexing doesn't block in-flight chat reads.

---

## 5. Decision log

A chronological record. Newest decisions at the **bottom**. Each entry: date,
decision, why, trade-offs accepted.

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

### 2026-05-15 — SSE event protocol: nine typed events over a single stream
The `POST /api/chat` endpoint emits nine distinct event types rather than a
single `data` stream, so the frontend can render approval gates, question
cards, tool status, and streaming text from one connection without polling.
Each event carries a `type` discriminator and a `data` payload; the frontend
switches on `type` to decide whether to append a token, show an approval
card, or mark a tool as running.
**Trade-off**: nine event types are more surface area than a simple
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
short-circuits Entra auth in dev to a fake `dev-user` identity.

### Tests

```bash
cd backend && python -m pytest tests/ -x -q   # 570 tests as of 2026-05-16
cd frontend && npm test                       # 109 tests
```

### Background tasks (started in [main.py `lifespan`](../backend/app/main.py))

| Task | What | Cadence |
|---|---|---|
| `start_periodic_sync` | Git-pull KB repo, normalize, then trigger reindex | `KB_SYNC_INTERVAL_SECONDS` (default 15 min) |
| `_approval_sweeper` | Expire stale `pending_approvals` + `pending_questions` | Every 60 s |
| `_backup_loop` | Snapshot `app.db` to `app-db-<ts>.db` | `BACKUP_INTERVAL_SECONDS` (default 24 h); off by default |
| KB reindex (Phase 2) | Diff per file by sha256; chunk + embed + upsert changed files | After each `sync_repo()` + on startup (background) |

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

### Concurrency assumption
Currently single-process (one uvicorn worker). The KB re-indexer uses an
in-process `threading.Lock` to prevent overlapping runs. If we ever scale
to multiple workers, we'll need a DB-level advisory lock or to pin reindex
to worker 0. Logged here so a future change doesn't quietly break
indexing.

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
- **DSPy refactor** (Phase 3): clean up ad-hoc `chat.completions.create`
  calls in the compaction summarizer and the eventual query-expansion path
  into typed signatures. Code-quality, not a new capability.
- **Full corpus ingestion** (1000 wiki + 100 PDF): same code as the pilot,
  just turn on more wiki spaces / longer link lists.
