# Tester.md — Tool QA Playbook (Nexus)

Adversarial, persona-driven QA of every registered tool (generic + bundles) plus the
orchestrator/frontend integration around them. Complements `Testing.md` (the automated
pytest/vitest suites) — this file is the **manual/exploratory** plan: how to exercise
the tools directly, what cases to run, and what "good" looks like.

Latest run: **2026-06-06** — full results and root-causes in
[NEXUS_TOOL_TEST_REPORT.md](NEXUS_TOOL_TEST_REPORT.md).

---

## 1. Objective & scope

- Exercise all **28 tools** in `TOOL_REGISTRY` (11 generic + KB/diagram + 9 Azure-bundle).
- Grade each **PASS / SO-SO / FAIL** on correctness, output quality (is it useful to the LLM?), and guardrails.
- Reproduce/root-cause the reported issues in `Bugs.md` at the layer they actually live in.
- For each tool, adopt the **persona** that would call it (FinOps engineer for cost, SRE for monitor/network, architect for diagrams, etc.).

## 2. Preconditions / environment

| Need | Why | Check |
|---|---|---|
| `az` logged in | live Azure-bundle tests | `az account show` |
| graphviz `dot` on PATH (or `C:\Program Files\Graphviz\bin`) | `generate_python_diagram`, `generate_drawio_from_python` | `dot -V` |
| draw.io desktop **or** `DRAWIO_EXPORT_URL` sidecar | `render_drawio`, auto-render | `C:\Program Files\draw.io\draw.io.exe` |
| Azure OpenAI keys in `backend/.env` | KB hybrid/semantic, learnings, E2E | `/healthz` → `aoai_circuit_breaker.state=closed` |
| `DEV_AUTH_BYPASS=true` | drive `/api/chat` without MSAL | both `.env` files |

> Azure-bundle tests are **read-only**. Never run mutating `az_cli` / `az_rest_api` (PUT/POST/PATCH/DELETE) / `execute_script` against a real tenant during QA.

## 3. Test strategy — three layers

Bugs live at different layers, so testing is layered:

1. **Tool harness (unit-ish).** Import `TOOL_REGISTRY`, call `tool.execute(args, user)` directly with a fake `User`. Deterministic, fast, isolates *tool quality* from *model quality*. Used for all generic + diagram + Azure-bundle tools.
2. **Azure bundle live, read-only.** Same harness, real subscription, read-only KQL/REST/cost/advisor/policy/devops/monitor/network calls.
3. **Full E2E.** Start the backend, drive real chat turns through the orchestrator + LLM over the SSE `/api/chat` endpoint; inspect tool calls, token-usage payloads, persisted attachments. Plus DB inspection (`agent_learnings`, `messages.attachments_json`) and static frontend root-cause for UI-only bugs.

### 3a. Reproducing the tool harness

The harness is a throwaway script run from `backend/`. Pattern:

```python
# backend/_tooltest.py  (delete after use)
from app.tools.base import init_tools, TOOL_REGISTRY, classify_tool_outcome
from app.auth.models import User
init_tools()
USER = User(oid="dev", email="balaji@futurefortifiedtech.com", display_name="Balaji")

def run(name, label, args):
    tool = TOOL_REGISTRY[name]
    out = tool.execute(args, USER)          # direct call — approval is orchestrator-level, bypassed here
    print(name, label, classify_tool_outcome(out), len(out))
    print(out[:1400])

run("web_search", "happy", {"query": "azure front door vs application gateway", "site": "reddit", "limit": 3})
# ... etc per the case tables below
```

Notes:
- Approval (`requires_approval`, `_needs_approval`) is enforced by the **orchestrator**, not `execute()` — a direct call bypasses it, which is fine for read-only QA.
- Azure tools authenticate via cached `az` login (the env allowlist forwards `USERPROFILE`/`HOME`); no ARM token needs to be injected for harness runs.
- Introspect any schema with `TOOL_REGISTRY[name].parameters_schema`.

### 3b. Reproducing the E2E driver

```python
# backend/_e2e.py  (delete after use) — stream SSE from /api/chat
import httpx, json
payload = {"message": "...", "skill_id": "shared:chat-with-kb"}   # skill ids are namespaced
with httpx.stream("POST", "http://localhost:8000/api/chat", json=payload, timeout=300) as r:
    for line in r.iter_lines():
        if line.startswith("event:") or line.startswith("data:"):
            print(line)
```
SSE shape: `event: <type>` + `data: <json>`. Token events use `{"text": "..."}`. The `done`
event carries `usage` (occupancy + segment breakdown). Attachments are **not** streamed —
they're persisted to `messages.attachments_json` and seen on conversation reload.

Run backend: `python -m uvicorn app.main:app --port 8000`. Skill ids:
`shared:architect`, `shared:chat-with-kb`, `shared:drawio-diagrammer`, `shared:kb-searcher`.

### 3c. Grading rubric

- **PASS** — correct result, well-shaped/useful output, all guardrails hold.
- **SO-SO** — works but degrades quietly, dumps unusable bulk, or misses obvious queries.
- **FAIL** — errors on a valid input, returns wrong/empty when data exists, or a guardrail is missing.

---

## 4. Test cases

Each case is `tool — input — expected`. ✅ = passed in the 2026-06-06 run; ⚠️/❌ link to a bug id in the report.

### 4.1 Generic — web & docs search

| Tool | Input | Expected |
|---|---|---|
| web_search | `{query:"azure front door vs application gateway", site:"reddit", limit:3}` | ✅ 3 reddit results, `site:reddit.com` appended |
| web_search | `{query:"aks autoscaling site:reddit.com", site:"reddit"}` | ✅ no double `site:` (de-dupe) |
| web_search | `{query:""}` | ✅ `Error: query is required` |
| web_fetch | `{url:"https://learn.microsoft.com/.../container-apps/overview"}` | ✅ refuses with "JS-rendered → use fetch_ms_docs" (**B11**: same msg for 404s) |
| web_fetch | `{url:"file:///etc/passwd"}` | ✅ scheme blocked |
| fetch_ms_docs | `{query:"azure key vault rbac vs access policy"}` | ✅ relevant Learn results |
| fetch_ms_docs | `{query:""}` | ✅ `Error: query is required` |
| search_github | `{query:"azure bicep aks module"}` | ⚠️ **B3** returns `[]` (verbose query ANDs to 0); `"azure bicep aks"` → results |
| search_stack_overflow | `{query:"az cli login device code timeout"}` | ⚠️ **B3** returns `[]`; `"az login timeout"` → results |
| search_azure_updates | `{query:"container apps"}` | ✅ structured updates feed |

### 4.2 Generic — knowledge base

| Tool | Input | Expected |
|---|---|---|
| search_kb | `{query:"network security group"}` | ✅ `[]` is *correct* — KB has only 8 docs, none on NSG |
| search_kb | `{query:""}` | ✅ `[]` (empty tokens) |
| search_kb_hybrid | `{query:"how do we handle private endpoints"}` | ✅ ranked chunks + confidence banding |
| search_kb_hybrid | `{query:""}` | ✅ `Error: query is required` |
| search_kb_semantic | `{query:"AKS rbac"}` | ❌ **B1** — `max_tokens` 400 → degrades to empty |
| read_kb_file | `{}` / `{path:"../../../etc/passwd"}` / `{path:"kb/missing.md"}` | ✅ required / Invalid path / File not found |
| read_file | `{path:"../config.py"}` / `{path:"nope.txt"}` | ✅ traversal blocked / not found |

### 4.3 Generic — diagrams

| Tool | Input | Expected |
|---|---|---|
| generate_file | good `.drawio` XML, `overwrite:true` | ✅ saved + auto-validate PASS + auto-render PNG |
| generate_file | `.drawio` with overlap + generic-icon + literal `\n` | ✅ auto-validate **FAILS** with 3 precise violations |
| generate_file | `{filename:"evil.exe"}` / `{filename:"../escape.txt"}` / existing no-overwrite | ✅ ext blocked / traversal blocked / exists error |
| validate_drawio | good file | ✅ `Validation PASSED` |
| validate_drawio | bad file | ✅ `Validation FAILED` + suggested target coords |
| validate_drawio | malformed XML / missing file / non-`.drawio` | ✅ parse error / not found / ext error |
| patch_drawio_cell | `{cell_id:"vm2", x:420}` | ✅ patched + re-validate + re-render |
| render_drawio | good file, `format:"png"` | ✅ local CLI render (drawio desktop) |
| render_drawio | `format:"gif"` / missing file | ✅ unsupported format / not found |
| generate_python_diagram | valid `diagrams` code | ✅ renders PNG — ⚠️ **B2** output never inlined in chat |
| generate_python_diagram | `import os` / no `with Diagram()` / syntax error | ✅ AST rejects / requires block / syntax error |
| generate_drawio_from_python | valid `diagrams` code | ✅ DOT→drawio→validate PASS→render |
| generate_drawio_from_python | `import socket` | ✅ AST rejects forbidden import |

### 4.4 Generic — execution & interaction

| Tool | Input | Expected |
|---|---|---|
| execute_script | write `scripts/hello.ps1` then run | ✅ exit 0 + stdout |
| execute_script | `{}` / `{path:"../../config.py"}` / `{path:"nope.ps1"}` / `hello.txt` | ✅ required / traversal / not found / bad ext |
| ask_user | valid `questions` array | ✅ validates, returns orchestrator-only structured error |
| ask_user | 1 option / `questions:"nope"` | ✅ "2-4 options" / "must be a list" |

### 4.5 Azure bundle — live, read-only

| Tool | Input | Expected |
|---|---|---|
| az_resource_graph | `Resources \| summarize count() by type \| order by count_ desc \| take 10` | ✅ live inventory |
| az_resource_graph | `{query:""}` / bad KQL / `... & whoami` | ✅ required / parser error (**B10** verbose) / `&` injection blocked |
| az_cli | `{args:["account","show","-o","json"]}` | ✅ live account |
| az_cli | args containing `` ` `` or `&` | ✅ injection blocked per-arg |
| az_rest_api | `GET .../resourcegroups?api-version=2021-04-01` | ✅ live RG list |
| az_rest_api | non-Azure URL / empty URL | ✅ URL allowlist / required |
| az_cost_query | `{query_type:"usage", time_period:"this_month", group_by:"ResourceGroup"}` | ✅ formatted cost-by-RG (**#6**: default-sub only) |
| az_cost_query | `{query_type:"budget_status"}` | ✅ graceful "No budgets found" |
| az_advisor | `{category:"Cost"}` | ⚠️ **B4** raw ~12KB JSON |
| az_policy_check | `{action:"compliance_summary"}` | ⚠️ **B4** raw ~12KB JSON |
| az_monitor_logs | `{query:"AzureActivity \| take 5"}` | ✅ graceful 0-results (slow ~8.7s) |
| az_devops | `{action:"list_projects"}` | ✅ live projects; mutations gated by `_SAFE_ACTIONS` |
| network_test | `{action:"dns_lookup", hostname:"learn.microsoft.com"}` | ✅ resolves |
| network_test | `{action:"port_check", hostname:"learn.microsoft.com", port:443}` | ✅ SUCCESS (open) |

### 4.6 Integration / E2E (orchestrator + frontend)

| Area | How tested | Expected / Finding |
|---|---|---|
| Approval gating | static: orchestrator `_tool_needs_approval` duck-types `_needs_approval(method/action)` | ✅ `az_rest_api` DELETE & `az_devops` create_pr/trigger_build require approval |
| Azure tool turn | E2E `shared:chat-with-kb` "how many resources by type?" | ✅ model calls `az_resource_graph`, streams, `done` carries usage |
| Diagram turn | E2E `shared:architect` "Front Door → App Service → SQL" | ✅ `generate_drawio_from_python`; attachment `/api/output/<name>.png?v=<mtime>` persisted |
| Diagram stale (#1) | DB `messages.attachments_json` + `serve_output` headers | ✅ content-derived filename + `?v=mtime` + `Cache-Control: no-store`; residual = **B2** / browser |
| Learnings (#2) | DB query `agent_learnings` | ✅ 39 rows (23 active/14 prov/2 arch), 0 empty — capture works (success-after-failure only) |
| Token gauge (#3) | E2E `done.usage.segments` across iterations | ⚠️ **B5** sampled at first call; Tools = 46–59% of prompt; under-reports intra-turn growth |
| Download (#5) | grep frontend for `<a download>` | ⚠️ **B6** none; `serve_output` supports `.drawio` download but UI never links it |
| Image auth (prod) | `serve_output` `Depends(current_user)` | ⚠️ **B7** `<img>` can't send bearer → 401 in MSAL mode |

---

## 5. Cleanup

The harness/E2E scripts (`backend/_tooltest.py`, `backend/_e2e.py`) and any `output/tt_*`
artifacts are throwaway — delete after a run so they don't pollute the sandbox or git.
