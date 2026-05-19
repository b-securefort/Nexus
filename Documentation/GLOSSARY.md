# Nexus — Glossary

> This is a glossary, not a spec. It defines what terms mean when used in
> code, PRs, and conversations about Nexus. General programming concepts
> (async, REST, middleware) are excluded — only Nexus-specific or
> overloaded terms belong here.

---

## Language

| Term | Definition | Aliases to avoid |
|---|---|---|
| **Skill** | A YAML-frontmatter markdown file that defines an AI persona: a `display_name`, `description`, scoped `tools` allowlist, and a `system_prompt`. Selecting a skill swaps the agent's behaviour and which tools it can call. | "mode", "persona", "prompt template" |
| **Shared skill** | A skill whose `SKILL.md` file lives in the Git-synced KB repo (`kb_data/skills/shared/<name>/`). Available to all users. | "global skill", "system skill" |
| **Personal skill** | A skill created by a specific user, stored in the `personal_skills` DB table. Visible only to that user. | "custom skill", "user skill" |
| **Skill snapshot** | A JSON copy of a skill's full config (id, name, tools, system_prompt) stored on the `Conversation` row at conversation-creation time. Ensures an existing conversation's behaviour is frozen even if the shared skill is later edited. | "skill config", "skill state" |
| **Conversation** | A named chat session between one user and the agent. Has one active skill snapshot. Persists across browser sessions. | "chat", "thread", "session" |
| **Message** | A single turn within a conversation. Role is `user`, `assistant`, or `tool`. Tool messages carry `tool_call_id` linking them to the assistant call that triggered them. | "turn", "reply", "chat message" |
| **Tool** | A Python class registered in `TOOL_REGISTRY` that exposes a typed JSON schema and an `execute()` method. The LLM calls tools by emitting tool-call JSON; the orchestrator executes them and feeds results back. | "function", "action", "plugin" |
| **Bundle** | A directory under `bundles/<teamname>/` containing Tool subclasses for a specific team or domain, loaded at startup only when `TOOL_BUNDLE_<NAME>_ENABLED=true` — lives outside `app/` so adopting teams can ignore bundles that don't apply to them. | "plugin", "extension", "module" |
| **Approval** | A gate on a `requires_approval=True` tool. When triggered, the orchestrator creates a `pending_approvals` row, emits an `approval_required` SSE event, and blocks until the user approves or denies. | "confirmation", "permission gate" |
| **Question** (`ask_user`) | A structured multi-choice prompt the agent emits when it needs clarification before acting. The orchestrator creates a `pending_questions` row, emits `question_required` SSE, and resumes with the user's answers. Distinct from Approval — it's about gathering intent, not gating a destructive action. | "clarification", "prompt" |
| **KB (Knowledge Base)** | The corpus of markdown files synced from Git (`kb_data/kb/`). Searched via keyword index or hybrid retrieval. Injected as an index summary into every system prompt. | "docs", "wiki", "knowledge repo" |
| **KB source** | An external system (ADO wiki, Git repo) whose content is pulled and normalised into the KB by the sync process. The KB source is never queried directly at runtime — only the synced local copy is. | "external docs", "ADO documentation", "source docs" |
| **Learning** | A categorized entry (`known-issue`, `syntax-fix`, `workaround`, `best-practice`, `gotcha`) in `kb_data/learnings/learn.md`. Written by the agent after a failed retry; read back into every system prompt so future turns avoid the same mistake. | "memory", "note", "log entry" |
| **Orchestrator** | The async generator in `orchestrator.py` that runs the full agent loop: compose system prompt → call LLM streaming → handle tool calls → retry on failure → yield SSE events. | "agent loop", "agent runner" |
| **Compaction** | The process of summarizing older messages to keep the LLM context window manageable. Preserves every user message verbatim; collapses assistant+tool scaffolding between consecutive user messages into `[Outcomes from intermediate tool work]` bullets. | "summarization", "history trimming", "context compression" |
| **ARM token** | An Azure Resource Manager bearer token (`aud=https://management.azure.com/`) acquired by the frontend via MSAL for `user_impersonation`. Passed as `X-ARM-Token` and injected as `AZURE_ACCESS_TOKEN` in every Azure tool subprocess so tools run as the user's own identity. | "Azure token", "management token" |
| **Skill prompt** | The body of a `SKILL.md` file (below the YAML frontmatter). Injected verbatim as the first system-prompt segment every turn. | "system prompt" (too generic — every turn has a composed system prompt; "skill prompt" refers specifically to the skill-file body) |
| **Output sandbox** | The `backend/output/` directory. The only location tools with `generate_file` / `render_drawio` / `generate_python_diagram` are permitted to write. Path-traversal is blocked at tool level. | "output directory", "artifact dir" |
| **SSE event** | A typed JSON line streamed from `POST /api/chat` during a chat turn. Defined in `app/agent/streaming.py` — current types: `token`, `tool_call_start`, `tool_executing`, `tool_output_chunk`, `tool_result`, `approval_required`, `question_required`, `question_answered`, `message_saved`, `done`, `error`. | "event", "stream event", "server event" |
| **Retry strategy** | One of three escalating recovery attempts the orchestrator makes when a command tool fails. Strategy 1: look up MS docs, fix syntax. Strategy 2: try a completely different command/approach. Strategy 3: try a different tool entirely or record a learning and give up. Only applies to tools in `_COMMAND_TOOLS` (`az_cli`, `run_shell`, `az_resource_graph`). | "retry", "auto-retry", "fallback" |
| **Circuit breaker** | A single-trip flag (`_az_circuit_breaker_tripped`) in `app/tools/base.py`. When `az` is not found on startup, the breaker trips once and all subsequent `_find_az()` calls return `None` immediately without re-running `shutil.which()`. Resets only on process restart. | "az check", "CLI detection" |
| **Blocked prefix** | A hardcoded list of `az` subcommand sequences in `app/tools/az_cli.py` that are rejected at execute time regardless of whether the user has granted Approval. Covers operations that wipe credentials or remove access (`account clear`, `ad app/sp create/delete`, `role assignment/definition delete`). | "blocked command", "command blocklist" |
| **Learning guard** | A regex filter (`_OVERRIDE_PATTERNS`) applied to both writes and reads of `learn.md`. Rejects entries that instruct future runs to ignore, suppress, or discredit tool guidance (e.g. "ignore the validator", "recommendations are too noisy"). Prevents the agent self-poisoning its own memory to bypass safety signals. | "override guard", "learning filter" |
| **KB chunk** | A bounded fragment of a KB source file produced by `chunker.py`. Each chunk has a `heading` breadcrumb (e.g. `"Guide > Installation > Windows"`), a `text` body, and an optional `source_url` from the file's front-matter. Chunks are the unit stored in `kb_chunks` and retrieved by `search_kb_hybrid` — unlike `search_kb_semantic` which returns whole files. | "document chunk", "text segment", "fragment" |
| **Embedding** | A list of 1536 numbers (a vector) produced by Azure OpenAI `text-embedding-3-small` that encodes the *meaning* of a piece of text so that texts with similar meanings produce similar vectors. Stored in `kb_chunks_vec`. Used to find semantically similar chunks even when they share no keywords with the query. | "vector", "dense representation", "encoding" |
| **ONNX (Open Neural Network Exchange)** | An open file format for storing a trained machine-learning model so it can run on different runtimes without needing the original training framework (e.g. PyTorch). Considered for Nexus local embeddings (`bge-small-en-v1.5`) but not adopted — Azure OpenAI `text-embedding-3-small` was chosen instead (higher quality, no model download). | "model format", "serialized model" |
| **BAAI (Beijing Academy of Artificial Intelligence)** | The Chinese AI research lab that created and published the `bge` family of embedding and reranker models. Considered for Nexus local hybrid retrieval but not adopted — see the 2026-05-15 "Azure OpenAI text-embedding-3-small" §5 decision entry. | "bge authors", "model provider" |
| **Front-matter** | A YAML block delimited by `---` at the top of every ingested markdown document. Written by the ingestion normalizer; fields include `source`, `source_url`, `original_path`, `last_synced`, `title`. Stripped by the chunker before text is indexed so metadata does not leak into chunk content or embedding vectors. | "YAML header", "document metadata" |
| **Ingestion source type** | The category label assigned to a document during ingestion, stored in the `source` front-matter field and used as the subdirectory name under `kb_data/kb/<source>/`. Current values: `ado_wiki` (pages pulled from an Azure DevOps wiki) and `pdf_web` (PDFs downloaded from a URL link list). Distinct from **KB source** (the external system) — this is the normalised category on each document. | "source label", "doc type" |
| **Embedding model** | The Azure OpenAI `text-embedding-3-small` model called by the reindexer to convert KB chunk text into a 1536-number vector, and called at query time to embed the search query. Uses the same `AZURE_OPENAI_*` credentials as the chat path — no separate model file or download. The model name + dimensions are recorded in `kb_chunks.embed_model` so a model swap triggers automatic re-embedding of all chunks. | "encoder", "sentence transformer", "local embedding model" |
| **RRF (Reciprocal Rank Fusion)** | An algorithm that merges two ranked lists — BM25 keyword results and embedding vector results — into one combined ranking without needing scores on the same scale. Each result gets a score of `1 / (rank + K)` (K=60) from each list; scores are summed. Chunks that rank highly in *both* lists rise to the top. | "rank fusion", "hybrid fusion", "score combination" |
| **Reindexer** | The background process in `reindex.py` that reads every `*.md` file under `kb_data/kb/`, chunks it, calls Azure OpenAI to embed each chunk (1536 dims), and writes the results to `kb_chunks` + `kb_chunks_vec`. Skips files whose `content_hash` hasn't changed since last run. Triggered at startup and after each KB sync. | "indexer", "KB indexer", "background indexer" |
| **DOT capture pipeline** | The mechanism behind `generate_drawio_from_python`. The user's Python (mingrammer `diagrams` DSL) is run with a capture header injected; the Graphviz DOT output is intercepted mid-flight, `dot -Tjson` extracts node coordinates, then each node is mapped to its Azure2 SVG icon before emitting `.drawio` XML. Distinct from `generate_python_diagram` which just renders a PNG via Graphviz directly. | "drawio pipeline", "diagram pipeline" |
| **Text summary** | A cached LLM-generated condensation of a long user message (> 3 KB), stored in `messages.text_summary`. Computed once by compaction; used in place of the full content in all subsequent prompt builds to save tokens. The original content is preserved in the DB. | "message summary", "paste summary" |
| **Image summary** | A cached vision-LLM description of an image attachment on a non-recent user message, stored in `messages.image_summary`. The most recent image-bearing message always keeps its actual bytes; older ones are replaced with this description in the composed system prompt. | "attachment summary", "vision description" |

---

## Relationships

- One **User** → many **Conversations**
- One **Conversation** → one **Skill snapshot** (frozen at creation), many **Messages**
- One **Message** → zero or one set of **Tool calls** (on `assistant` messages), zero or one **Approval**, zero or one **Question**
- One **Bundle** → many **Tools** (every Tool subclass defined in files under `bundles/<teamname>/`)
- One **Skill** → one or more **Tools** (listed in `tools:` frontmatter)
- One **KB** → many **KB files** → many **KB chunks**
- One **KB chunk** → one **Embedding** (stored in `kb_chunks_vec` by the **Reindexer**)
- One **Ingestion source type** → many **KB files** (all files under `kb_data/kb/<source>/` share the same type label in their **Front-matter**)
- One **Embedding model** → all **Embeddings** in `kb_chunks_vec` (model name recorded per-row; swap triggers full re-embed)
- **RRF** fuses one BM25 ranked list + one **Embedding** ranked list → final ranked results returned by `search_kb_hybrid`
- One **Learning** → one **Tool** (the tool it relates to, or `general`)
- One **ARM token** → one **User** per request (attached to `User.arm_token`; never stored in DB)

---

## Example dialogue

> *"The agent asked the user a question about which subnet topology to use."*
> ✓ Correct: the agent called `ask_user`, which emitted a **Question** event.

> *"The agent requested approval before running az vm start."*
> ✓ Correct: `az_cli` has `requires_approval=True`, so an **Approval** gate fired.

> *"I want to change the agent's persona for this chat."*
> → Means: select a different **Skill**. The conversation will use the new skill's snapshot from that point (actually, skill snapshot is fixed at conversation creation — switching means starting a new conversation).

---

## Flagged ambiguities

| Ambiguous term | Resolution |
|---|---|
| "system prompt" | Overloaded. Use **skill prompt** when referring to the SKILL.md body. Use "composed system prompt" when referring to the full prompt built by the orchestrator each turn (skill prompt + KB index + learnings + retry policy + Azure context). |
| "skill" vs "command" | A **skill** is a Nexus agent persona (lives in KB, shown in UI). A **command** (`.claude/commands/*.md`) is a Claude Code IDE slash command. These are different systems serving different users — agent users vs developers. |
| "tool" vs "tool call" | A **tool** is the registered Python class. A **tool call** is a specific invocation of it by the LLM, represented as JSON in `messages.tool_calls_json`. |
| "approval" vs "question" | Both pause execution and wait for the user. **Approval** is binary (allow/deny a specific command). **Question** is multi-choice (gather intent before starting work). |
