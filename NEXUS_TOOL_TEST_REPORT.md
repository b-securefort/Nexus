# Nexus Tool Test Report

**Date:** 2026-06-06 · **Tester:** Claude (adversarial tool QA) · **Scope:** all 28 registered tools (generic + Azure bundle) + orchestrator/frontend integration
**Environment:** live `az` login as `balaji@futurefortifiedtech.com` → sub `FFT-Dunamis Aviation - IaaS`; graphviz `dot` + draw.io desktop installed; Azure OpenAI `gpt-5.4-mini`; backend Phase 3.

## Method
Three layers:
1. **Tool harness** — imported `TOOL_REGISTRY` and called each `tool.execute(args, user)` directly with persona-driven happy-path + abuse inputs (empty/missing args, shell-injection strings, path traversal, oversized output, malformed payloads).
2. **Azure bundle live, read-only** — same harness against the real subscription. No mutating commands were run against the tenant.
3. **Full E2E** — started the backend and drove real chat turns through the orchestrator + LLM via the SSE `/api/chat` endpoint (Azure resource-graph turn + architect diagram turns), inspecting tool calls, token-usage payloads, and persisted attachments. Plus DB inspection of `agent_learnings` and static root-cause of the frontend.

---

## Per-tool grades

| Tool | Grade | Notes |
|---|---|---|
| web_search (DDG) | ✅ PASS | site: shortcuts, double-`site:` de-dupe, empty-query guard all correct |
| web_fetch | ✅ PASS | blocks `file://`; **B11**: all `learn.microsoft.com` URLs (incl. 404s) collapse to one "JS-rendered" message |
| fetch_ms_docs | ✅ PASS | clean, relevant results |
| search_azure_updates | ✅ PASS | rich structured output |
| search_kb | ✅ PASS | token scoring sound; empty results are correct (KB has only 8 docs) |
| search_kb_hybrid | ✅ PASS | RRF + rerank + confidence banding; **B8** noisy SQL logging |
| **search_kb_semantic** | ❌ **FAIL** | **B1** — `max_tokens` rejected by model → query expansion + rerank both 400 → silent degrade to empty |
| read_kb_file | ✅ PASS | traversal + not-found guarded |
| read_file | ✅ PASS | sandbox + traversal guarded |
| **search_github** | ⚠️ SO-SO | **B3** — verbose NL queries return `[]` (API ANDs all terms; no relaxation) |
| **search_stack_overflow** | ⚠️ SO-SO | **B3** — same; default `tagged=azure` narrows further |
| generate_file | ✅ PASS | ext allowlist, traversal, overwrite, auto-validate+auto-render on `.drawio` |
| validate_drawio | ✅ PASS | **excellent** — precise, actionable violations with target coords; malformed XML handled |
| render_drawio | ✅ PASS | local draw.io CLI render works; format/missing-file guarded |
| patch_drawio_cell | ✅ PASS | surgical geometry patch + re-validate/re-render |
| **generate_python_diagram** | ⚠️ SO-SO | tool renders fine, but **B2** — output never shown to user *or* model |
| generate_drawio_from_python | ✅ PASS | `dot -Tjson` → drawio XML → validate/render; AST guard blocks bad imports |
| execute_script | ✅ PASS | runs `.ps1`; traversal/not-found/bad-ext/missing-arg guarded |
| ask_user | ✅ PASS | schema validation correct; orchestrator-only execution enforced |
| az_resource_graph | ✅ PASS | live KQL inventory; `&` injection blocked; **B10** verbose error boilerplate |
| az_cli | ✅ PASS | live reads; backtick/`&` injection blocked; env allowlist |
| az_rest_api | ✅ PASS | GET works; non-Azure URL blocked; mutations gated by orchestrator; **B9** misleading comment |
| az_cost_query | ✅ PASS | live cost-by-RG ($173.21); **#6** can't target a non-default subscription |
| **az_advisor** | ⚠️ SO-SO | **B4** — dumps raw ~12 KB ARM JSON into the prompt |
| **az_policy_check** | ⚠️ SO-SO | **B4** — same raw-JSON dump |
| az_monitor_logs | ✅ PASS | graceful 0-results; slow (~8.7 s) |
| az_devops | ✅ PASS | live `list_projects`; mutations gated by `_SAFE_ACTIONS` |
| network_test | ✅ PASS | dns_lookup + port_check fast and correct |

**Security posture: strong.** Shell-metachar injection (`` ` ``, `&`, `%`, NUL) blocked on every command path; path traversal blocked on every file tool; `diagrams` code runs through an AST allowlist sandbox; az subprocess uses an env allowlist (no secret leakage); mutating `az_rest_api`/`az_devops` actions are approval-gated via orchestrator duck-typing.

---

## Bugs (severity-ranked, with repro + root cause)

### HIGH

**B1 — `search_kb_semantic` is broken (model rejects `max_tokens`).**
Repro: `search_kb_semantic({"query":"AKS rbac"})` → `Query expansion failed (400 … 'max_tokens' is not supported with this model. Use 'max_completion_tokens')` → falls back to keyword search → `{"results": []}`.
Root cause: [backend/app/tools/generic/kb_tools.py](backend/app/tools/generic/kb_tools.py#L188) line 188 and line 224 still pass `max_tokens`. The team already migrated `kb/reranker.py` and added a regression test (`test_uses_max_completion_tokens_not_max_tokens`) — `kb_tools.py` is the straggler. Both LLM calls in the tool 400; the tool silently returns empty.
Fix: rename both `max_tokens=` → `max_completion_tokens=`.

**B2 — `generate_python_diagram` output is never displayed (to user or model).**
Repro: an architect/diagram turn that calls `generate_python_diagram` renders `<stem>.png` on disk, but no image appears in the chat bubble and the model gets no vision-review.
Root cause: [backend/app/agent/orchestrator.py](backend/app/agent/orchestrator.py#L2058) line 2058 lists `render_drawio, generate_file, patch_drawio_cell, generate_drawio_from_python` for the vision-review + `_attachment_for_rendered_png` capture — **`generate_python_diagram` is omitted.** The frontend `ToolCallCard` preview also excludes it (and requires a `.drawio` filename it never has). Net: the PNG is orphaned, yet the tool's own success text promises "the rendered image is being attached to your next turn." This is the cleanest concrete piece of Bugs.md #1.
Fix: add `generate_python_diagram` to the line-2058 tuple and resolve its `<stem>.png` directly.

### MEDIUM

**B3 — `search_github` / `search_stack_overflow` silently return `[]` for verbose queries.**
Repro (live): GitHub `"azure bicep aks module"` → `total_count 0`, but `"azure bicep aks"` → 83. SO `"az cli login device code timeout"` → 0, but `"az login timeout"` → 2. Both APIs AND every term; the tools forward the full natural-language query with no relaxation. This is the real substance of Bugs.md #4 (web_search/DDG itself works fine).
Fix: on zero results, retry with progressively fewer terms (drop trailing words) and/or instruct the model to use ≤3 keywords; for SO, don't force `tagged=azure` when the query isn't Azure-specific.

**B4 — `az_advisor` / `az_policy_check` dump raw ~12 KB ARM JSON into the prompt.**
Repro: `az_advisor({"category":"Cost"})` → 12,304-char raw JSON (truncated at the 12,288 `max_output_size`). `az_policy_check({"action":"compliance_summary"})` → same.
Root cause: unlike `az_cost_query` (which formats a tidy summary), these return the REST payload verbatim, with no `result_limit` cap. Token-wasteful and hard for the model to use.
Fix: summarize (top-N recommendations / per-policy compliant-vs-non-compliant counts) like `az_cost_query` does.

**B5 — Context-usage gauge under-reports and "resets" (Bugs.md #3).**
Repro (E2E): an architect turn sampled `prompt_tokens=12645` then read 2 KB files in later iterations; the reported occupancy stayed 12645 — the intra-turn growth is invisible. Each turn replaces the value with that turn's *start* occupancy.
Root cause: [backend/app/agent/orchestrator.py](backend/app/agent/orchestrator.py#L1670) captures `resting_usage` only on the **first** LLM call of the turn (`if resting_usage is None`), and the frontend updates `contextUsage` only on `done`. So the gauge shows turn-*start* occupancy, ignores tool outputs / vision images / the model's own tool-call messages added during the turn, and visibly jumps between turns.
Fix: sample on the **last** LLM call of the turn (or track the per-turn peak) so the number reflects the actual current fill.

**B6 — No download affordance in the UI (Bugs.md #5).**
Repro: no `<a download>` anywhere in `frontend/src/components`. The `serve_output` route *does* serve `.drawio` (and PNG) for download — the frontend simply never links it.
Fix: add download buttons for the `.drawio` source and the rendered PNG next to each diagram (`ToolCallCard` / `MessageBubble`).

### LOW / robustness

- **B7** — `serve_output` requires auth (`Depends(current_user)`). An `<img src>` tag can't send a bearer header, so in MSAL (non-dev) mode diagram images **and** downloads would 401. Works in dev-bypass; verify the prod auth is cookie-based or add a signed-URL/same-origin path. [backend/app/api/chat.py](backend/app/api/chat.py#L645)
- **B8** — SQLAlchemy `echo=True`: every query is logged (observed flooding the backend log). Prod noise + perf. Disable echo outside debug.
- **B9** — `az_rest_api` in-file comment says "actual check happens in execute()" — it doesn't; approval is enforced by the orchestrator duck-typing `_needs_approval`. Functionally correct today but fragile. [backend/bundles/azure/az_rest.py](backend/bundles/azure/az_rest.py#L84)
- **B10** — `az_resource_graph` bad-query errors include useless "Please provide below info when asking for support: timestamp/correlationId" boilerplate — noise to the model. Trim to the `InvalidQuery`/`ParserFailure` detail.
- **B11** — `web_fetch` short-circuits *all* `learn.microsoft.com` URLs (including genuine 404s) to the same "JS-rendered, use fetch_ms_docs" message; a broken link is indistinguishable from a valid one.
- **Doc** — [CLAUDE.md](CLAUDE.md) still documents `read_learnings`/`update_learnings` as live tools and says "8 tools registered"; they were removed (now orchestrator-owned) and 28 tools register. Update the tool table.

---

## Reported issues that did NOT reproduce / clarifications

- **Bugs.md #2 (learnings not captured / empty table).** *Not reproduced.* `agent_learnings` holds **39 rows** (23 active, 14 provisional, 2 archived) with **0 empty** summary/details. Capture works — but it only fires on a **success-after-failure** transition for learning-eligible tools (or explicit user corrections); an ordinary successful conversation records nothing by design. The "empty values" are most likely the nullable lifecycle columns (`judge_verdict_json`, `embed_model`, `last_validated_at`) on `provisional` rows. Note: on any LLM-judge exception the learning is silently rejected (`approve=False`) — a possible quiet-loss path worth a metric.
- **Bugs.md #1 (stale diagram).** Backend is robust: content-derived filenames + `?v=<mtime_ns>` cache-bust + `Cache-Control: no-store`. Verified the persisted attachment URL: `/api/output/front-door-app-service-sql.png?v=1780760485108491400`. Residual risk is **B2** (python_diagram) and the browser/`ToolCallCard` path — recommend a manual UI confirmation.
- **Bugs.md #6 (cost scope).** Confirmed limitation, not a bug: `az_cost_query` always scopes to the default subscription from `az account show`; there is no subscription/management-group/billing-scope parameter.
- **Bugs.md #4 (web search).** `web_search` (DDG) works well in every test; the empty-results pain is specifically **B3** (GitHub/StackOverflow).

---

## Improvements for quality (prioritized)

1. **Fix B1 and B2** — both are one-liners with outsized impact (a dead search tool; invisible diagrams).
2. **Trim tool-schema token weight.** The `Tools` segment measured **4,456–5,819 tokens (46–59% of the whole prompt)** in live turns. Shorten tool descriptions and/or load only the active skill's tools. This is the single biggest context-efficiency lever.
3. **Summarize `az_advisor`/`az_policy_check`** (B4) and add a `result_limit`.
4. **Query relaxation** for GitHub/SO search (B3).
5. **Add a subscription/scope parameter** to `az_cost_query` (#6).
6. **Add diagram download buttons** (B6) and verify image auth in prod (B7).
7. **Make the context gauge sample at turn-end / peak** (B5); turn off SQL echo (B8).
8. **Refresh CLAUDE.md** tool list (28 tools; no read/update_learnings).

## What's genuinely strong
- The **drawio toolchain** (generate → validate → render → patch) is excellent: the validator's coordinate-level, actionable feedback is a standout.
- **Azure bundle** works end-to-end against real Azure across resource-graph, cli, rest, cost, advisor, policy, devops, monitor, network — with consistent injection/auth guarding.
- **Security guardrails** (injection, traversal, AST sandbox, env allowlist, approval duck-typing) are thorough and consistent.
- **Learnings subsystem** is sophisticated and actually populated (39 rows) with a real defense stack against memory-poisoning.
