# Drill 2 — Tool execution & approval pipeline

**Diagram**: [`backend/output/nexus-drill2-tool-approval.drawio`](../../backend/output/nexus-drill2-tool-approval.drawio) · [PNG preview](../../backend/output/nexus-drill2-tool-approval.png)

**Audience**: Engineers working on tool implementations, security reviewers, anyone evaluating Nexus's "runs commands as the user" claim.

**Time to present**: ~7 minutes.

---

## TL;DR

When the LLM emits a tool call, it goes through a 7-stage pipeline before any command actually runs: **skill allowlist check → per-user concurrency semaphore + thread pool → ARM token preflight → blocked-prefix check → approval gate → subprocess (env-allowlisted, shell=False, ARM-tokened) → result handling**. The result-handling cluster then summarises long outputs via LLM and feeds success-after-failure events into the learning-write pipeline.

---

## Teleprompter script

> **Set up the frame.**
> "This is the path from 'LLM says: please run this' to actually running it. Seven stages. Each stage is a check that can fail closed. Read left to right."

> **Step 1 — Origin.**
> "The Orchestrator on the far left has received a tool call from the LLM. That tool call is a JSON object: `{name: 'az_cli', arguments: {...}}`. We're about to put it through gauntlet."

> **Step 2 — Skill allowlist + ContextVar.**
> "First gate: is this tool even on the current skill's allowlist? Every skill — Architect, Engineer, Default — has a frozen `tools: []` list in its `SKILL.md`. If the LLM hallucinates a tool that isn't allowed, we reject here before anything else.
>
> Also at this stage we set the **skill name ContextVar**. Why? Some tools — like `generate_file` — refuse certain operations based on which skill is calling them. For example, Engineer can't write `.drawio` files via `generate_file`; that's reserved for Architect. We pass the skill identity into the tool layer via ContextVar so the tool can branch on it."

> **Step 3 — Concurrency gate.**
> "Per-user `asyncio.Semaphore(4)` plus a global `ThreadPoolExecutor(64)`. Translation: any single user can have at most 4 tool calls in flight; the whole process pool has 64 worker threads.
>
> The semaphore exists because we hit a real production issue — one user running a 15-iteration Azure-tour conversation was starving everyone else's tool calls. The cap is per-user, not global, so even with the semaphore, *other* users still get their fair share."

> **Step 4 — ARM token preflight.**
> "If the tool is an Azure tool — anything inheriting `AzureToolBase` — we check the user's ARM token *before* spending an Azure call. We decode only the `exp` claim — unverified, just to read the expiry. Three outcomes: missing, expired, or `near_expiry`.
>
> Missing or expired: short-circuit with a structured error telling the model to wait while the frontend refreshes. Near-expiry: still execute, but emit a `token_refresh_required` SSE event that the frontend acts on mid-turn — it calls `acquireTokenSilent` and POSTs the new token back to a `/api/chat/refresh-token` endpoint while this turn is still running. We added this because architect turns sometimes outlive a 1-hour ARM token; silent renewal preserves the conversation."

> **Step 5 — Blocked-prefix check.**
> "For `az_cli` specifically: there are six command sequences we *always* reject, even after the user has granted Approval — `az account clear`, `ad app create`, `ad app delete`, `ad sp create`, `ad sp delete`, `role assignment delete`, `role definition delete`. These are credential-wipe or access-removal operations that would lock the team out of Azure. The logic is: 'we don't trust a user to deny under pressure,' so we put the hard line in code.
>
> If the command starts with any of those prefixes, we return an error from `_is_blocked()` and the request never reaches the approval gate."

> **Step 6 — Approval gate / ask_user pause.**
> "For tools where `requires_approval = True` — `az_cli`, `run_shell`, `az_rest_api` writes — we write a row to `pending_approvals` with the tool name, arguments, and reason. We emit an `approval_required` SSE event. The orchestrator literally awaits the user's response. The same primitive backs `ask_user` — when the agent itself needs to ask the user a clarifying question, it writes to `pending_questions`.
>
> Both queues have a 10-minute expiry sweep — if the user walks away, we don't pile up stale approvals forever."

> **Step 7 — The actual subprocess.**
> "Finally — and only after every gate above has passed — we invoke `subprocess.run`. Look at the box: `env allowlist ~14 keys`, `shell=False`, `AZURE_ACCESS_TOKEN injected`.
>
> `shell=False` is non-negotiable. We don't pass the command through `cmd.exe` — we pass it as a list directly to `CreateProcess`. So there's no shell interpolation of `%VARS%` or `&&` chaining.
>
> Env allowlist: we don't inherit `os.environ`. We build a fresh env dict with about 14 keys — `PATH`, `HOME`, `AZURE_CONFIG_DIR`, Windows profile vars, proxy vars, and we layer the ARM token on top. So a malicious `az` argument trying to read `%AZURE_OPENAI_API_KEY%` finds nothing to read.
>
> The Azure tools then talk to Azure ARM. From ARM's perspective, the call is signed by the *user's* token — `oid=<user>`, `roles=<user's roles>`. Not the server's managed identity. So the audit trail in Azure shows who did what."

> **Step 8 onward — Result handling.**
> "On the right we have the result-handling cluster. Three things happen.
>
> The tool returns a result envelope — status, output. If the output is over 2 KB and isn't an error envelope, we route it through the **LLM summariser** — a separate Azure OpenAI call with a strict 'preserve the meaning, both ends' prompt. The old behaviour was to head+tail split, which would leave the model staring at half a JSON object. The summariser fixes that. Error envelopes skip the summariser — the agent gets the exact error text so retry decisions stay faithful.
>
> The result then goes back to the Orchestrator — dashed line on the diagram — and the orchestrator either feeds it into the next LLM iteration or, if this was the last tool call, emits the terminal `done` event.
>
> If the tool just succeeded *after* prior failures in this turn, the **success-after-failure detector** fires. It triggers a learning write — Drill 4 is that subgraph."

> **Close.**
> "Three things to remember. First: every gate fails closed. If any one of them errors, the tool doesn't run. Second: the user's ARM token is what powers the Azure calls — Nexus has no Azure permissions of its own beyond reading the App Configuration role map. Third: blocked prefixes bypass the approval gate by design — there are six operations we don't even let the user approve. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **Orchestrator (LLM returned tool_call)** | Anchor — the source of every tool call. | The pipeline starts here when the LLM emits a `tool_calls` array. |
| **Skill allowlist + skill_name CV** | Allowlist check using `Conversation.skill_snapshot_json.tools` + a `ContextVar` set with the active skill slug. | Two purposes: reject hallucinated tool calls early; let tools branch on which skill is calling them (used for `generate_file` `.drawio` rejection in Engineer skill). |
| **Per-user Semaphore(4) + ThreadPool(64)** | The `_gated_tool_execute` choke-point + lazy-singleton `ThreadPoolExecutor`. | One user can't starve others. Lazy singleton means no startup cost; lifespan teardown. |
| **ARM token preflight (JWT exp claim)** | `arm_token_status()` — decodes only `exp`, unverified, to bucket as `missing / expired / near_expiry / ok`. | Don't burn an Azure call when the token is already dead. `near_expiry` is what triggers the silent-refresh SSE event mid-turn. |
| **Blocked-prefix check** | `_is_blocked()` in [`az_cli.py`](../../backend/app/tools/azure/az_cli.py) — hardcoded list of six prefixes. | Permanent reject for credential-wipe / access-removal ops. Bypasses the approval gate by design. |
| **Approval gate / ask_user pause** | Persisted pause via `pending_approvals` / `pending_questions` + SSE event + 10-min sweeper. | The user-in-the-loop gate that makes Nexus safe to run mutating tools. |
| **subprocess.run** | The actual external command invocation: `env` allowlisted ~14 keys, `shell=False`, ARM token injected as `AZURE_ACCESS_TOKEN`. | Hardened subprocess invocation. Each detail in the label is load-bearing security work. |
| **Tool result (status/output)** | The Result envelope: `{status: 'ok'|'error', output: ...}`. | Distinguishes successful results from errors so the summariser knows to skip errors. |
| **LLM summariser (if >2 KB, non-error)** | Separate Azure OpenAI completions call with a "preserve meaning, head + tail" prompt. | Fixes the old head+tail truncation problem where the model would see half a JSON object. Skipped for errors so retry strategy stays faithful. |
| **Success-after-failure detector (triggers learning write)** | State tracked on the orchestrator: failed tool calls in this turn + a subsequent success → trigger `record_validated_learning`. | The signal source for the learning-write pipeline. Drill 4 is the deep dive on what happens after this triggers. |
| **Azure ARM / CLI** | The Azure control plane, called with the user's ARM token in the subprocess env. | Where the actual cloud operation happens — visible from this side as the egress target. |
| **Azure OpenAI (summariser deployment)** | The chat-completion deployment used by the summariser. | Same model family as the main chat path; same credentials. |

---

## Appendix B — Edges (the lines)

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 1 | Orchestrator → Skill allowlist | `1 tool_call` | LLM-emitted tool call enters the gauntlet. |
| 2 | Skill allowlist → Semaphore + ThreadPool | `2 allowed` | Tool is on the skill's allowlist. |
| 3 | Semaphore → ARM preflight | `3` | Thread acquired; per-user cap respected. |
| 4 | ARM preflight → Blocked-prefix check | `4 ok / refresh` | Token is fresh, or marked `near_expiry` (triggers refresh SSE event but proceeds). |
| 5 | Blocked-prefix check → Approval gate | `5 not blocked` | Command isn't on the permanent reject list. |
| 6 | Approval gate → subprocess.run | `6 approved` | User clicked approve in the UI. |
| 7 | subprocess.run → Azure ARM | `7 az + token CV` | The actual command executes against ARM as the user. |
| 8 | subprocess.run → Tool result | `8 stdout/stderr` | The subprocess returns; output is captured. |
| 9 | Tool result → LLM summariser | `9 if >2 KB` | Long outputs route through the summariser. |
| 10 | LLM summariser → Azure OpenAI | `10` | The summariser call itself is an AOAI completion. |
| 11 | Tool result → Orchestrator (**dashed**) | `11 back to orchestrator` | Result feeds back into the LLM loop. |
| 12 | Tool result → Success-after-failure detector (**dashed**) | `12 after retry success` | If this tool call succeeded after prior failures, trigger the learning write (Drill 4). |

---

## Appendix C — Glossary references

For abbreviations (ARM, CV, JWT, SSE, AOAI), see **[GLOSSARY.md](GLOSSARY.md)** in this folder.

For Nexus-specific terms (Tool, Approval, Question, Blocked prefix, Skill), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the underlying design decisions:
- Blocked-prefix bypasses approval → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- Subprocess hardening (env allowlist, shell=False) → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- ARM token passthrough via header → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- ARM preflight + frontend-driven refresh → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- Per-user semaphore + thread pool → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- LLM summarisation for large tool outputs → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- Engineer skill rejects .drawio at tool layer → [DESIGN.md §5 2026-05-20](../DESIGN.md)
