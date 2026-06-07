# Nexus Tool Test Report

**Date:** 2026-06-06 (re-run after bug-fix pass) · **Tester:** Claude (adversarial tool QA) · **Scope:** all 28 registered tools (generic + Azure bundle) + orchestrator/frontend integration
**Environment:** live `az` login as `balaji@futurefortifiedtech.com` → sub `FFT-Dunamis Aviation - IaaS` (`3e40a1d8-…`); graphviz `dot` 14.1.5 (`C:\Program Files\Graphviz\bin`) + draw.io desktop installed; Azure OpenAI `gpt-5.4-mini` (circuit breaker closed); backend Phase 3; `DEV_AUTH_BYPASS=true`.

> This is the **second** run of the [Tester.md](Tester.md) playbook. The first run (archived below as "Prior run") found bugs B1–B11 + issue #6. This re-run **verifies the fixes** at every layer (source → harness → live → E2E) and reports one new finding (**N1**) that was found and fixed during the run.

## Method
Three layers, same as the prior run:
1. **Tool harness** — imported `TOOL_REGISTRY`, called `tool.execute(args, user)` directly with a fake `User` (persona happy-path + abuse inputs).
2. **Azure bundle live, read-only** — same harness against the real subscription (no mutations).
3. **Full E2E** — backend up, real chat turns through the orchestrator + LLM over SSE `/api/chat`; inspected tool calls, `done.usage`, persisted `messages.attachments_json`, `serve_output` headers, and the `agent_learnings` table.

---

## Verification of prior bugs (all fixed ✅)

| ID | Prior status | Re-run result | How verified |
|---|---|---|---|
| **B1** `search_kb_semantic` `max_tokens` 400 | ❌ FAIL | ✅ **FIXED** | `kb_tools.py` lines 188/224 now `max_completion_tokens`. Harness: `{"query":"AKS rbac"}` returns `expanded_terms` (expansion succeeded, no 400) + clean `"No matching KB documents found."` (empty is correct — KB has no AKS doc). |
| **B2** `generate_python_diagram` output never displayed | ❌/HIGH | ✅ **FIXED** | `orchestrator.py:2141` adds `generate_python_diagram` to the vision-review tuple **and** `_attachment_for_rendered_png` (via `_resolve_rendered_png`, which resolves its bare-stem `<stem>.png`). E2E architect turn persisted attachment `…?v=<mtime>` (see B-#1 below). |
| **B3** GitHub/StackOverflow `[]` on verbose queries | ⚠️ SO-SO | ✅ **FIXED** | New `search_relax.py` `relaxed_queries()` used by both tools; SO no longer forces `tagged=azure`. Harness: `"azure bicep aks module"` → `Azure/AKS-Construction` (+more); `"az cli login device code timeout"` → live SO results. |
| **B4** `az_advisor`/`az_policy_check` raw ~12 KB JSON | ⚠️ SO-SO | ✅ **FIXED** | Both now have `_summarize()` + `result_limit`. Harness: advisor → `"17 recommendation(s) · by category/impact · Top 17 …"` (2.5 KB); policy → 190-char `"Non-compliant resources: 75 · policies: 1 · per-assignment …"`. |
| **B5** context gauge under-reports / resets | ⚠️ MEDIUM | ✅ **FIXED** | `orchestrator.py:1786` recomputes `resting_usage` at **turn end** over the post-turn context with a tiktoken→API calibration ratio. E2E: across two turns of one conversation prompt_tokens grew 12 996 → 14 073 and the `Messages` segment grew 254 → 1 270 (reflects fill, no reset). |
| **B6** no download affordance in UI | ⚠️ MEDIUM | ✅ **FIXED** | `ToolCallCard.tsx` + `MessageBubble.tsx` add `<a download>` for the rendered PNG and the editable `.drawio` source. Server confirms: `GET /api/output/<stem>.drawio` → 200. |
| **B7** `<img>` 401 in MSAL mode | ⚠️ LOW | ✅ **FIXED** | Frontend now loads images via `useAuthedBlobUrl` (authenticated blob fetch with bearer) instead of a bare `<img src>`; disallowed origins render as inert text. |
| **B8** SQLAlchemy `echo=True` log flood | ⚠️ LOW | ✅ **FIXED** | `engine.py` now `echo=settings.DB_ECHO` (defaults off). Server log over the whole E2E run: **0** SQL/echo lines. |
| **B9** misleading `az_rest_api` comment | ⚠️ LOW | ✅ **FIXED** | Comment now correctly states approval is enforced by the orchestrator duck-typing `_needs_approval(method)`. |
| **B10** `az_resource_graph` error boilerplate | ⚠️ LOW | ✅ **FIXED** | `_trim_arg_error()` strips the `timestamp/correlationId/"Please provide below info"` block. Harness bad-KQL → trimmed `BadRequest` detail only. |
| **B11** `web_fetch` collapses all `learn.microsoft.com` to one message | ⚠️ LOW | ✅ **FIXED** | Real 404 → `Error: HTTP 404 — Not Found`; valid SPA → distinct content-wall message. A dead link is now distinguishable. |
| **#6** `az_cost_query` can't target a subscription | limitation | ✅ **ADDRESSED** | New optional `subscription` GUID param (regex-validated): valid GUID scopes the query; `"not-a-guid"` → clear validation error; omitted → default sub (live `$179.96` by RG). |
| **Doc** CLAUDE.md stale tool list | doc | ✅ **FIXED** | CLAUDE.md now documents 28 tools and notes learnings are orchestrator-owned (no `read/update_learnings`). |

### Prior "did-not-reproduce" items — re-confirmed healthy
- **Bugs #1 (stale diagram).** E2E attachment URL `/api/output/front-door-app-service-sql-db.png?v=1780784052453095300`; `serve_output` returns `Cache-Control: no-store, max-age=0` + content-derived filename + `?v=<mtime_ns>`. Robust. The prior residual (B2) is now closed too.
- **Bugs #2 (learnings not captured / empty).** `agent_learnings` now holds **40 rows** (27 active / 11 provisional / 2 archived), **0 empty** summary/details. A new learning (id 64, `workaround`) was **captured live during this run** from the diagram turn — capture works.
- **Bugs #3 (token reset).** Closed by B5 fix above.

---

## New finding (this run)

**N1 — `generate_drawio_from_python` crashes on non-ASCII labels (Windows).** *Severity: MEDIUM (recovered via retry).* **— FIXED in this run.**
Repro (E2E architect turn): the model emitted an edge label containing `→`; the pipeline raised
`UnicodeEncodeError: 'charmap' codec can't encode character '→'` at `_drawio_emitter.py:run_dot_layout`.
Root cause: `subprocess.run([dot, "-Tjson"], input=…, text=True, …)` with no `encoding=` uses the Windows locale default (cp1252) to encode stdin, so any non-ASCII char the model puts in a label — arrows, em-dashes, accented resource names — crashes the `dot` pipe.
Impact: the orchestrator recovered (model retried without the arrow and recorded learning #64 about non-ASCII), so it's not user-fatal, but it silently burns a tool iteration and would surprise anyone using arrows/accented names.
Fix applied: pass `encoding="utf-8"` to the `subprocess.run` call (fixes both stdin encode and stdout decode). Verified: a diagram with an `Edge(label="ingress → app")` now renders + validates + auto-renders cleanly.
File: [backend/app/tools/generic/_drawio_emitter.py](backend/app/tools/generic/_drawio_emitter.py#L745)

**N2 — the architect diagram loop does not converge for a complex real topology.** *Severity: MEDIUM. — FIXED (Part 1 + 2), verified.*
Repro (E2E, `shared:architect`, "audit network topology + draw it" against the live sub): the model
inventoried 3 VNets / 25 resources / 16 containers correctly, but all 3 `generate_drawio_from_python`
attempts ended `Validation FAILED: 8 violation(s)` (recurring `[resource-parent]` — non-network nodes
like Managed Identity / Entra ID / Key Vault dragged visually inside VNet clusters). It exhausted the
iteration budget and shipped an unusable PNG (tall/narrow, stacked NSGs, overlapping labels, floating
nodes). The simple "Front Door → App Service → SQL" diagram converged fine, so this is specifically a
*large-graph* failure. The model was honest about the shortfall.
Root cause (two modes): (A) a validator self-contradiction — `_check_resources_parented_to_subnets`
flagged top-level (`parent="1"`) identity/DNS/PaaS nodes whose Graphviz coords overlapped a VNet bbox as
a BLOCKING `[resource-parent]` violation, directly contradicting the non-blocking hint (and the skill
rules) that those planes belong OUTSIDE the VNet — unwinnable since the model can't hand-edit coords;
(B) dense-graph routing the model can't fix from Python at whole-subscription scale.
Fix applied:
- **Part 1 (validator):** exclude identity/DNS/PaaS/observability planes from the blocking check
  (reuse `_IDENTITY/_DNS_ZONE/_PAAS_KEYWORDS`); the non-blocking hint still covers the wrongly-nested
  direction. + 2 regression tests. [validate_drawio.py](backend/app/tools/generic/validate_drawio.py#L279)
- **Part 2 (architect skill):** new "Large topologies — decompose" rule: when a topology spans >1 VNet
  or >~12 nodes, produce an overview diagram + one per VNet instead of a mega-diagram.
Verification (re-ran the network audit, conv 331): **`[resource-parent]` count = 0 across all 7 renders**
(was ≥1 and unwinnable every time); the model explicitly cited the decomposition rule, produced a clean,
readable VNet-level overview, converged, and stopped — instead of shipping a failed mega-diagram.
Residual (out of scope for N2): the model still occasionally writes the forbidden `from diagrams import
AzureGeneric` (the AST guard rejects it and it recovers) — a recurring instruction-adherence trip worth a
separate hardening (e.g. have the emitter silently strip that import, since AzureGeneric is injected anyway);
and the final diagram converges to "acceptable" rather than a clean `Validation PASSED` (2 residual
edge/label hints the model judged intentional).

---

## Per-tool grades (re-run)

| Tool | Grade | Note |
|---|---|---|
| web_search (DDG) | ✅ PASS | unchanged |
| web_fetch | ✅ PASS | **B11 fixed** — 404 vs JS-wall now distinct; `file://` blocked |
| fetch_ms_docs | ✅ PASS | |
| search_azure_updates | ✅ PASS | |
| search_kb | ✅ PASS | |
| search_kb_hybrid | ✅ PASS | |
| search_kb_semantic | ✅ **PASS** | **B1 fixed** — expansion + rerank no longer 400 |
| read_kb_file / read_file | ✅ PASS | traversal + not-found guarded |
| search_github | ✅ **PASS** | **B3 fixed** — query relaxation |
| search_stack_overflow | ✅ **PASS** | **B3 fixed** — relaxation; no forced `tagged=azure` |
| generate_file | ✅ PASS | ext/traversal/overwrite + auto-validate/render |
| validate_drawio | ✅ PASS | coordinate-level violations (standout) |
| render_drawio | ✅ PASS | local CLI render |
| patch_drawio_cell | ✅ PASS | surgical geometry patch + re-validate |
| generate_python_diagram | ✅ **PASS** | **B2 fixed** — now inlined/attached; AST guard blocks `import os` |
| generate_drawio_from_python | ✅ PASS | DOT→drawio→validate→render; AST guard; **N1 fixed** (UTF-8) |
| execute_script | ✅ PASS | ps1 run; traversal/ext/not-found guarded |
| ask_user | ✅ PASS | schema validation |
| az_resource_graph | ✅ PASS | live KQL; `&` injection blocked; **B10 fixed** error trim |
| az_cli | ✅ PASS | live reads; backtick/`&` injection blocked per-arg |
| az_rest_api | ✅ PASS | GET works; mutations orchestrator-gated; **B9** comment fixed |
| az_cost_query | ✅ PASS | live cost-by-RG; **#6** subscription param added + validated |
| az_advisor | ✅ **PASS** | **B4 fixed** — summarized |
| az_policy_check | ✅ **PASS** | **B4 fixed** — summarized |
| az_monitor_logs | ✅ PASS | graceful 0-results |
| az_devops | ✅ PASS | live list_projects; mutations gated |
| network_test | ✅ PASS | dns_lookup + port_check |

**Security posture: still strong.** Per-arg shell-metachar injection (`` ` ``, `&`) blocked on `az_cli`/`az_resource_graph`; path traversal blocked; `diagrams` AST allowlist blocks `import os`/`socket`; cost subscription arg is GUID-regex-validated.

---

## Remaining recommendations (non-blocking)
1. **Segment `percent` is `None`** in the `done.usage.segments` payload (tokens are correct). Cosmetic — populate `percent` so the gauge tooltip can show per-segment share without client-side math.
2. **Tool-schema token weight** still ~4.6–6.0 K tokens (≈ 50–58 % of a live prompt). Biggest remaining context lever — shorten descriptions / load only the active skill's tools.
3. **LLM-judge exception path** for learnings still silently rejects (`approve=False`) — worth a metric to catch quiet losses.

## What's genuinely strong
- The **drawio toolchain** (generate → validate → render → patch) remains excellent; the validator's coordinate-level feedback is a standout.
- **Azure bundle** works end-to-end against real Azure across resource-graph/cli/rest/cost/advisor/policy/devops/monitor/network with consistent injection/auth guarding.
- **Learnings subsystem** is populated (40 rows), captures live, and has a real anti-poisoning defense stack.

---

## Cleanup
Throwaway harness/E2E scripts (`backend/_tooltest*.py`, `backend/_e2e.py`, `backend/_server.log`) and `output/tt_*` artifacts were deleted after the run. The only source change left in the tree is the **N1** UTF-8 fix in `_drawio_emitter.py`.
