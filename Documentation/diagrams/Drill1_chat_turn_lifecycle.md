# Drill 1 — Chat turn lifecycle

**Diagram**: [`backend/output/nexus-drill1-chat-turn-lifecycle.drawio`](../../backend/output/nexus-drill1-chat-turn-lifecycle.drawio) · [PNG preview](../../backend/output/nexus-drill1-chat-turn-lifecycle.png)

**Audience**: Engineers who need to understand the timeline of a single chat turn — what happens in what order from the moment a user hits send until the `done` SSE event fires.

**Time to present**: ~6 minutes.

---

## TL;DR

A chat turn is a 13-step sequence: save the user message, build per-turn context (compaction + learnings + system prompt), call the LLM via the circuit breaker, dispatch any returned tool calls (with approval if mutating), feed results back, and loop up to 15 times. The terminal path is the `done` SSE event with usage metadata; the iteration path is the dashed loop back into system-prompt rebuild.

---

## Teleprompter script

> **Set up the frame.**
> "L1 showed the components. Drill 1 shows the *timeline*. Same boxes, ordered by when they fire. Numbered edges 1 through 13. If you trace the numbers, you're following exactly what happens when a single chat message is processed."

> **Phase 1 — Ingestion (steps 1–3).**
> "Step 1: the user types and submits. Step 2: the frontend opens an SSE POST to `/api/chat` with the message body and any attachments. Step 3: the backend immediately persists the user message to the `messages` table — including any image attachments. We save *before* we do anything else so the message survives a crash. If the orchestrator dies mid-turn, the user message is already on disk."

> **Phase 2 — Per-turn context build (steps 4–6).**
> "Step 4: Compaction. We load the conversation's history and apply asymmetric compression — every user message stays verbatim, but assistant-and-tool scaffolding between them collapses into single-bullet summaries. Long pastes over 3 KB get a cached text summary on the row itself.
>
> Step 5: Learnings retrieval. We take the user's latest message, embed it, and run hybrid retrieval — BM25 plus vector similarity, fused via RRF — over the `agent_learnings` table. We pull the top 5 most relevant entries. They come back with `[CANONICAL]` or `[PROVISIONAL]` markers so the model knows which are validated.
>
> Step 6: System prompt build. We compose: the skill's prompt + the KB index summary + the retrieved learnings + the Azure context (subscription, tenant) + a pinned copy of the original first user message — that pinning is the 'never forget what the user asked' safeguard. Cap at 2000 chars for the pinned block."

> **Phase 3 — LLM call (steps 7–8).**
> "Step 7: We send the composed prompt and the full message history to Azure OpenAI for chat completion. This call is wrapped in the Circuit breaker — if AOAI has been failing, the breaker is open and we short-circuit here with a clear error. Step 8: tokens stream back, and we collect any `tool_calls` the LLM emits along the way. We stream the tokens to the frontend as we receive them — the user sees the assistant typing in real time."

> **Phase 4 — Tool execution (steps 9–11).**
> "Step 9: Azure OpenAI returns a response. If it contains tool calls, we route them to Tool dispatch. Dispatch is a gate plus a thread pool — `Tool dispatch + approval/ask_user` on the diagram. Mutating tools pause for user approval; read-only tools fire immediately.
>
> Step 10: The tool actually executes. For Azure tools, that's `subprocess.run` with `shell=False`, a 14-key env allowlist, and the user's ARM token injected. Drill 2 unpacks that subprocess in detail.
>
> Step 11: The tool result comes back. The orchestrator appends it as a `tool` role message and — here's the loop — feeds the entire context back into step 6, rebuilding the system prompt with the new tool result included. That's the dashed arrow on the right going up. We loop up to 15 iterations. The cap is in `MAX_LLM_ITERATIONS`; if you hit it, the orchestrator yields a terminal message saying it gave up."

> **Phase 5 — Termination (steps 12–13).**
> "Step 12: When the LLM finally returns a response with *no* tool calls — that's the dashed terminal arrow on the diagram — we know the turn is done. Step 13: We emit the `done` SSE event with a usage payload (prompt tokens, completion tokens, cached tokens, model name) and close the stream. The frontend uses the usage to update the context-window indicator."

> **Close — non-obvious things to flag.**
> "Three things to call out. First: the user message is saved before anything else fires — that's our 'survive a crash' guarantee. Second: compaction and learnings retrieval are drawn as sequential, but they're independent operations; the sequential drawing is just for layout. Third: the loop edge is dashed on purpose — it's the structural difference between a chat app and an agent. Without that loop, this is just a wrapper around an LLM. *With* it, plus the approval gate, that's what makes Nexus an agent that runs commands. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **User** | The signed-in human. | Anchor for the timeline — step 1 originates here. |
| **Frontend (MSAL + SSE consumer)** | The React app. Handles MSAL token acquisition AND SSE stream consumption — both are explicit because both are non-trivial. | Step 2 is on this box. The dual capability matters: MSAL gets the token, SSE consumes the response. |
| **api/chat (SSE)** | FastAPI route at [backend/app/api/chat.py](../../backend/app/api/chat.py). | Entry point for the turn. Where the SSE stream is opened. |
| **Save user msg + attachments** | [SQLModel write to `messages` table](../../backend/app/db/models.py). Persists `role='user'`, content, plus any image attachments to `attachments_json`. | Step 3. Always persist before compute — if the worker crashes mid-turn, the user message survives. |
| **Compaction (history → bullets)** | [`backend/app/agent/compaction.py`](../../backend/app/agent/compaction.py). | Step 4. Without this, the context window blows up on long tool-heavy turns. |
| **Learnings retrieval (BM25 + vec + RRF)** | [`backend/app/agent/learnings.py::retrieve_relevant_learnings`](../../backend/app/agent/learnings.py). | Step 5. Drill 4 is the full deep dive. |
| **System prompt build (KB summary + learnings + ARM ctx + pinned task)** | `_compose_system_prompt` in [orchestrator.py](../../backend/app/agent/orchestrator.py). | Step 6. Where the model "knows" who the user is, what skills it has, what it has learned, and the original ask. |
| **Circuit breaker** | [`backend/app/agent/circuit_breaker.py`](../../backend/app/agent/circuit_breaker.py). | Wraps step 7. Fails fast when AOAI is sick. |
| **Azure OpenAI (chat completions)** | The chat deployment. | Step 8. Where the actual reasoning happens. |
| **Tool dispatch + approval/ask_user** | Combined node — concurrency gate plus the approval pause logic. | Step 9. Where LLM tool calls become real-world side effects (or wait for user OK). |
| **Tool execute (subprocess for az tools)** | The actual subprocess invocation for command-style tools; direct Python call for query tools. | Step 10. The "Nexus runs commands" claim materializes here. |
| **SSE done event + usage payload** | The terminal event emitted when the LLM returns no more tool calls. Includes prompt/completion/cached token counts and model name. | Step 13. Signals "turn over" and gives the frontend context-window data. |

---

## Appendix B — Edges (the lines)

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 1 | User → Frontend | `1 user msg` | The user submits a message in the browser. |
| 2 | Frontend → api/chat | `2 SSE POST` | The frontend opens a POST to `/api/chat` and starts consuming the SSE stream. |
| 3 | api/chat → Save user msg | `3` | The route persists the message before doing anything else. |
| 4 | Save → Compaction | `4` | Compaction reads the persisted message + history. |
| 5 | Compaction → Learnings retrieval | `5` | Compacted history feeds the retrieval query (latest user message). |
| 6 | Learnings retrieval → System prompt build | `6` | Retrieved learnings get baked into the composed prompt. |
| 7 | System prompt build → Circuit breaker | `7 chat` | The composed prompt enters the LLM dispatch. |
| 8 | Circuit breaker → Azure OpenAI | `8` | Breaker-wrapped chat completions call. |
| 9 | Azure OpenAI → Tool dispatch | `9 tokens + tool_calls` | The model's streamed response, including any tool calls. |
| 10 | Tool dispatch → Tool execute | `10 execute` | Approved tool calls (or read-only ones) execute. |
| 11 | Tool execute → System prompt build (**dashed**) | `11 tool result (loop <=15)` | Loop edge: the result feeds back into the next iteration's prompt. Cap is 15. |
| 12 | Azure OpenAI → SSE done event (**dashed**) | `12 final assistant msg` | When the LLM returns no `tool_calls`, the turn is terminal. |
| 13 | SSE done event → Frontend | `13 SSE done` | Final SSE event with usage payload; closes the stream. |

---

## Appendix C — Glossary references

For abbreviations (SSE, ARM, BM25, RRF, MSAL), see **[GLOSSARY.md](GLOSSARY.md)** in this folder.

For Nexus-specific terms (Orchestrator, Compaction, Learning, Skill, Approval, Question), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the underlying design decisions:
- Asymmetric compaction → [DESIGN.md §5 2026-05-14](../DESIGN.md)
- Pinned original task → [DESIGN.md §5 2026-05-13](../DESIGN.md)
- Retrieval-on-context for learnings → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Circuit breaker → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- Token usage on `done` SSE event → [DESIGN.md §5 2026-05-18](../DESIGN.md)
