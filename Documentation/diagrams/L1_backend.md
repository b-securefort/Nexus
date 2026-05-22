# Nexus L1 — Backend Internals

**Diagram**: [`backend/output/nexus-l1-backend.drawio`](../../backend/output/nexus-l1-backend.drawio) · [PNG preview](../../backend/output/nexus-l1-backend.png)

**Audience**: A new engineer onboarding to Nexus, or a stakeholder who needs the "how does this thing actually work end-to-end" picture in one slide.

**Time to present**: ~5 minutes.

---

## TL;DR

A chat request enters from the user's browser, becomes an SSE stream into `api/chat`, and is handled by an **Orchestrator** that runs a per-turn pipeline (**Compaction → Learnings retrieval → Circuit breaker → Azure OpenAI**) and dispatches LLM-requested tool calls (with an **Approval gate / ask_user pause** in front of mutating ones). All state lives in one SQLite file (`app.db`), kept in sync with a Git-backed knowledge base via a 15-minute reindex.

---

## Teleprompter script

> **Open with the frame.**
> "This is the single diagram that explains how Nexus works as a system. Everything else we'll talk about is a zoom-in on one of the boxes here. If you take only one slide home from this talk, take this one."

> **Walk the request path, left side top-to-bottom.**
> "Start at the User in the top-left. A signed-in user opens the React frontend in their browser. They type a message. The frontend posts it as an SSE — Server-Sent Events — stream to the backend, hitting `api/chat`. That's a FastAPI route.
>
> `api/chat` immediately hands off to the **Orchestrator**, which is the heart of Nexus — a single async generator that drives the whole chat turn. Down the left column, you can see what the Orchestrator does *before* calling the LLM.
>
> It first calls **Compaction** — that's our context-window manager. We preserve every user message verbatim, but we compress the assistant + tool scaffolding between them into single-bullet summaries. We do that because long tool-heavy turns push the original ask out of context, and we never want the model to forget what you asked for.
>
> Then it calls **Learnings retrieval**. This is interesting — Nexus has a persistent memory of mistakes it has made before, stored in SQLite. We embed the user's latest message and do hybrid retrieval — BM25 keyword search plus vector similarity, fused with reciprocal rank fusion — to pull only the top 5 most relevant learnings into the system prompt for this turn. So the model sees 'we've tried X before and it failed because Y' without us having to inject the entire history.
>
> The composed prompt goes through a **Circuit breaker** before the LLM call. If Azure OpenAI starts failing repeatedly, the breaker opens and we short-circuit with a clear error instead of cascading timeouts. Then **Azure OpenAI** completes the chat call, streaming tokens back."

> **Pivot to the right side.**
> "If the LLM returns plain text, we stream it back and we're done. But if it returns tool calls — that's the right column.
>
> **Tool dispatch** is where Nexus does something most chat apps don't: it actually *runs commands*. It has a per-user `asyncio.Semaphore` capped at 4 — so one chatty user can't starve everyone else — and it runs tools in a thread pool of 64 workers. From there, the path branches.
>
> Read-only tools — like `search_kb_hybrid`, `read_kb_file`, `az_resource_graph`, `az_cost_query` — execute immediately, no human in the loop.
>
> Mutating tools — `az_cli`, `run_shell`, `az_rest_api` writes — pass through the **Approval gate / ask_user pause**. The orchestrator persists a `pending_approvals` row, emits an `approval_required` SSE event, and physically waits for the user to click approve or deny. Same primitive backs `ask_user` for clarification questions.
>
> When an Azure tool runs, we inject the user's ARM token into the subprocess environment via a ContextVar. Every `az` call therefore runs as the signed-in user, not as the server's managed identity. That's `ARM preflight + token CV` on the egress arrow."

> **Cover storage and external dependencies.**
> "Top-right: `app.db` is our single SQLite file. Everything is in there — messages, conversations, approvals, queues, both KB chunks and agent learnings with their vector indexes. SQLite plus the `sqlite-vec` extension. WAL mode so reads don't block writes.
>
> Above it, the **KB Git repo**. Every 15 minutes we pull, normalize markdown, and reindex into `kb_chunks`. That's the only dashed line in the diagram — persistence, not request flow.
>
> Bottom: Azure OpenAI for chat and embeddings, Azure ARM for the actual cloud operations the tools perform."

> **Close.**
> "Three things to remember about Nexus from this diagram: it preserves the original ask through long iterations; it gates risky actions behind explicit user approval; and it learns from its own mistakes turn after turn. Each of those is the subject of a separate drill-down. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **User** | The signed-in human using Nexus through a browser. | Anchor: the source of every chat request. Without showing the user, the SSE direction is unclear. |
| **Frontend (React + Vite)** | The single-page app at [frontend/src/](../../frontend/src/) — MSAL for sign-in, SSE consumer for chat, approval/question/skill UI. | The other half of the SSE conversation. Drives what goes into `api/chat` and what gets rendered back. |
| **KB Git repo (ADO / GitHub)** | The external Git repo the team's knowledge base lives in. Could be ADO, GitHub, or any Git host. | Shown to make clear that KB content comes from outside; Nexus never queries it directly at runtime — only the periodically-synced local copy. |
| **api/chat (SSE stream)** | [`backend/app/api/chat.py`](../../backend/app/api/chat.py) — the FastAPI route that opens an SSE stream and yields the orchestrator's events. | The entry point. Every chat turn flows through here. |
| **Orchestrator (loop, <=15 iters)** | [`backend/app/agent/orchestrator.py`](../../backend/app/agent/orchestrator.py) — async generator that runs the full turn: compose prompt → LLM → tools → loop, max 15 iterations. | The brain. Everything else in the diagram is something the orchestrator invokes or coordinates. |
| **Compaction (preserve user msgs)** | [`backend/app/agent/compaction.py`](../../backend/app/agent/compaction.py) — asymmetric history compressor: user messages stay verbatim, assistant+tool scaffolding between them collapses into one bullet. | Without this, long iteration counts blow the context window and the model forgets the original ask. |
| **Learnings retrieval (BM25 + vec + RRF)** | `retrieve_relevant_learnings()` in [`backend/app/agent/learnings.py`](../../backend/app/agent/learnings.py) — hybrid search over `agent_learnings` for entries relevant to this user message. | Replaced the old "always inject the whole `learn.md`" path with per-turn relevance. Drill 4 is the deep dive. |
| **Circuit breaker (closed/open/half_open)** | [`backend/app/agent/circuit_breaker.py`](../../backend/app/agent/circuit_breaker.py) — module-level breaker around every Azure OpenAI chat completions call. | When AOAI is sick, fail fast and tell the agent, instead of every conversation cascading timeouts. |
| **Tool dispatch (ThreadPool(64) + Semaphore(4)/user)** | [`backend/app/agent/concurrency.py`](../../backend/app/agent/concurrency.py) — gated tool executor. | A single chatty user used to be able to starve other users' tool calls. Now bounded per user and per process. |
| **Approval gate / ask_user pause** | [`backend/app/agent/approvals.py`](../../backend/app/agent/approvals.py) — orchestrator pause + DB-persisted `pending_approvals` / `pending_questions` rows, resumes on UI response. | The "Nexus runs commands, doesn't just suggest" claim needs human approval. This is the structural gate. |
| **Read-only tools** | KB search, ms_docs, web_fetch, `az_resource_graph`, `az_cost_query`, `az_monitor_logs`, etc. — tools whose `requires_approval = False`. | These run without prompting the user. Approval would be friction without safety upside. |
| **Mutating tools** | `az_cli`, `run_shell`, `az_rest_api` writes — tools whose `requires_approval = True`. | These can change cloud state. Approval is non-negotiable. |
| **app.db (SQLite + sqlite-vec)** | Single SQLite file at [`backend/app.db`](../../backend/app.db). Tables: users, conversations, messages, pending_approvals, pending_questions, kb_chunks*, agent_learnings*. | Single-file persistence; backup is `cp`. The `*` notes that there are three virtual tables each for KB and learnings (canonical + FTS5 + vec0). |
| **Azure OpenAI (chat + embed)** | The chat-completions deployment and the `text-embedding-3-small` deployment. | Both LLM calls and embedding calls go here. Single trusted endpoint for both. |
| **Azure ARM / CLI** | The Azure control plane (`https://management.azure.com/`). | Where mutating Azure tools actually act. Token comes from the user via ARM passthrough. |

---

## Appendix B — Edges (the lines)

| From → To | Label | Meaning |
|---|---|---|
| User → Frontend | (none) | The user clicks/types in the browser. |
| Frontend → api/chat | `SSE` | The frontend opens a Server-Sent Events POST to `/api/chat` with the message. |
| api/chat → Orchestrator | (none) | The route hands the message to the orchestrator and yields the orchestrator's event stream back. |
| Orchestrator → Compaction | `history` | The orchestrator passes the conversation's prior messages in. |
| Compaction → Learnings retrieval | `retrieve` | The compacted history feeds context to the learnings retriever (which uses the latest user message as the query). |
| Learnings retrieval → Circuit breaker | `prompt` | The fully composed system prompt + history goes into the LLM dispatch. |
| Circuit breaker → Azure OpenAI | `chat / embed` | The breaker-wrapped Azure OpenAI call (both chat completions and any inline embedding). |
| Orchestrator → Tool dispatch | `tool_calls` | When the LLM returns tool calls, they go to dispatch. |
| Tool dispatch → Approval gate | `needs approval` | Mutating tools route through the approval gate. |
| Approval gate → Mutating tools | (none) | Once the user approves, the tool executes. |
| Tool dispatch → Read-only tools | `no approval` | Read-only tools execute immediately. |
| Mutating tools → Azure ARM | `ARM preflight + token CV` | The tool subprocess runs with the user's ARM token injected via ContextVar; ARM preflight checks the token's `exp` claim first. |
| KB Git repo → app.db (dashed) | `reindex 15m` | The KB reindexer runs on a 15-minute timer, writing chunked + embedded content into `kb_chunks` and friends. |

---

## Appendix C — Glossary references

For abbreviations on the diagram (SSE, ARM, CV, BM25, RRF, etc.), see **[GLOSSARY.md](GLOSSARY.md)** in this folder. For Nexus terminology (Skill, Tool, Approval, Learning, Compaction), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the design decisions behind each component, see **[DESIGN.md](../DESIGN.md)** § 5 Decision log — every node in this diagram has at least one decision-log entry explaining why it's there.
