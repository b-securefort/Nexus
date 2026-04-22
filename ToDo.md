# Nexus v2 — New Tools Development TODO

## Priority 0: Azure Login Pre-Check (Foundation)

### `az_login_check` — Auto-detect Azure CLI auth state
- **Approval:** No
- **What:** On first tool call per session that needs Azure CLI, run `az account show` to check login state. If not logged in, return instructions for `az login --use-device-code`.
- **Implementation:**
  - Add `check_az_login()` helper in `app/tools/az_cli.py` — caches result for 5 min
  - Called automatically by `az_cli`, `az_resource_graph`, and `az_cost_query` before execution
  - If not logged in: return friendly error with device code login instructions
  - If logged in: cache the subscription/tenant info and inject into system prompt
  - Store login state in module-level variable with TTL
- **System prompt change:** Add Azure context section showing current subscription, tenant, and logged-in user
- **Config:** None new (uses existing `TOOL_AZ_CLI_ENABLED`)
- **Tests:** Mock `subprocess.run` for logged-in / not-logged-in / expired token scenarios
- **Effort:** ~1 hour

---

## Priority 1: `az_cost_query` — Read-Only Cost Management

### What it does
Query Azure Cost Management API for cost data without approval.

### Parameters
```json
{
  "query_type": "enum: usage | forecast | budget_status",
  "time_period": "enum: last_7_days | last_30_days | last_month | this_month | last_3_months",
  "group_by": "optional string: ResourceGroup | ResourceType | ServiceName | Location",
  "filter_resource_group": "optional string: filter to specific RG"
}
```

### Implementation
- Wraps `az cost management query` (read-only, no approval needed)
- Pre-built KQL-like query templates for common patterns
- Returns formatted table with totals
- Falls back to `az consumption usage list` if cost management API is unavailable
- **Config:** `TOOL_AZ_COST_ENABLED=true`
- **Effort:** ~1 hour

---

## Priority 2: `az_monitor_logs` — Log Analytics KQL Queries

### What it does
Run KQL queries against Azure Monitor Log Analytics workspaces.

### Parameters
```json
{
  "query": "string: KQL query",
  "workspace_name": "optional string: workspace name (auto-discovers if omitted)",
  "timespan": "optional string: ISO 8601 duration, default PT1H"
}
```

### Implementation
- Wraps `az monitor log-analytics query --workspace <id> --analytics-query <kql>`
- Auto-discovers workspace via Resource Graph if `workspace_name` omitted
- Caches workspace ID after first lookup
- Returns tabular results (JSON → formatted table)
- **Approval:** No (read-only queries)
- **Config:** `TOOL_AZ_MONITOR_ENABLED=true`
- **Effort:** ~2 hours

---

## Priority 3: `az_rest_api` — Generic ARM REST Calls

### What it does
Direct Azure Resource Manager REST API calls for anything the CLI doesn't cover.

### Parameters
```json
{
  "method": "enum: GET | POST | PUT | PATCH | DELETE",
  "uri": "string: ARM resource URI (e.g., /subscriptions/{sub}/providers/...)",
  "api_version": "string: API version",
  "body": "optional object: JSON body for POST/PUT/PATCH",
  "reason": "string: why this call is needed"
}
```

### Implementation
- Wraps `az rest --method <m> --uri <uri> --body <json>`
- **Approval:** Yes for POST/PUT/PATCH/DELETE; No for GET
- Auto-injects current subscription if URI uses `{subscription}` placeholder
- **Config:** `TOOL_AZ_REST_ENABLED=true`
- **Effort:** ~2 hours

---

## Priority 4: `generate_file` — Write Output Artifacts

### What it does
Write files to a sandboxed output directory — Bicep templates, scripts, reports, CSV exports.

### Parameters
```json
{
  "filename": "string: filename with extension (e.g., main.bicep, report.csv)",
  "content": "string: file content",
  "description": "string: what this file is for"
}
```

### Implementation
- Writes to `output/` directory under KB path
- Path traversal guard (no `..`, must stay in output dir)
- Returns confirmation with file path and size
- Supports: `.bicep`, `.json`, `.ps1`, `.sh`, `.csv`, `.md`, `.tf`, `.yaml`, `.yml`
- **Approval:** No (writes to sandboxed dir only)
- **Config:** `TOOL_FILE_GENERATION_ENABLED=true`, `FILE_OUTPUT_DIR=./output`
- **Effort:** ~45 min

---

## Priority 5: `az_devops` — Azure DevOps Queries

### What it does
Query Azure DevOps pipelines, builds, PRs, and work items.

### Parameters
```json
{
  "action": "enum: list_pipelines | list_builds | list_prs | get_work_item | list_repos",
  "project": "optional string: ADO project name (uses default if omitted)",
  "pipeline_id": "optional int: for filtering builds",
  "work_item_id": "optional int: for get_work_item",
  "state": "optional string: filter PRs by state (active | completed | abandoned)",
  "top": "optional int: max results, default 10"
}
```

### Implementation
- Wraps `az devops` CLI commands
- Auto-configures org/project from `az devops configure --list`
- Read-only actions only
- **Approval:** No
- **Config:** `TOOL_AZ_DEVOPS_ENABLED=true`, `AZ_DEVOPS_ORG=`, `AZ_DEVOPS_PROJECT=`
- **Effort:** ~2 hours

---

## Priority 6: `az_policy_check` — Policy Compliance

### What it does
Check Azure Policy compliance status, list assignments, evaluate resources.

### Parameters
```json
{
  "action": "enum: compliance_summary | list_assignments | non_compliant_resources",
  "scope": "optional string: subscription or resource group scope",
  "policy_name": "optional string: filter by specific policy"
}
```

### Implementation
- Wraps `az policy state list`, `az policy assignment list`
- Summarizes compliance percentage and top violations
- **Approval:** No (read-only)
- **Config:** `TOOL_AZ_POLICY_ENABLED=true`
- **Effort:** ~1 hour

---

## Priority 7: `diagram_gen` — Architecture Diagrams

### What it does
Generate Mermaid diagrams from resource data or description.

### Parameters
```json
{
  "diagram_type": "enum: resource_topology | network_flow | sequence | custom",
  "title": "string: diagram title",
  "mermaid_code": "string: Mermaid diagram code",
  "auto_discover": "optional bool: if true, query Resource Graph and generate topology automatically"
}
```

### Implementation
- LLM generates Mermaid syntax, this tool validates and saves it
- If `auto_discover=true`, runs Resource Graph query for resources → builds topology
- Saves as `.mmd` file via `generate_file`
- Returns the Mermaid source for rendering
- **Approval:** No
- **Config:** `TOOL_DIAGRAM_ENABLED=true`
- **Effort:** ~1.5 hours

---

## Priority 8: `network_test` — Connectivity Testing

### What it does
DNS lookup, TCP connectivity test, basic network diagnostics.

### Parameters
```json
{
  "action": "enum: dns_lookup | tcp_test | traceroute | ping",
  "target": "string: hostname, IP, or URL",
  "port": "optional int: for tcp_test, default 443",
  "timeout_seconds": "optional int: default 10"
}
```

### Implementation
- DNS: `nslookup` / `Resolve-DnsName`
- TCP: Python `socket.create_connection()` (no subprocess needed)
- Ping: `Test-Connection` on Windows
- **Approval:** No (read-only diagnostics)
- **Config:** `TOOL_NETWORK_TEST_ENABLED=true`
- **Effort:** ~1 hour

---

## Priority 9: `az_advisor` — Azure Advisor Recommendations

### What it does
Pull Azure Advisor recommendations for cost, security, reliability, and performance.

### Parameters
```json
{
  "category": "optional enum: cost | security | reliability | performance | operational_excellence | all",
  "resource_group": "optional string: scope to RG",
  "impact": "optional enum: high | medium | low"
}
```

### Implementation
- Wraps `az advisor recommendation list`
- Groups by category, shows impact and description
- **Approval:** No (read-only)
- **Config:** `TOOL_AZ_ADVISOR_ENABLED=true`
- **Effort:** ~45 min

---

## Priority 10: `web_fetch` — General Web Content Fetch

### What it does
Fetch content from any URL — GitHub issues, Stack Overflow, vendor docs, status pages.

### Parameters
```json
{
  "url": "string: URL to fetch",
  "extract_text": "optional bool: strip HTML tags, default true",
  "max_length": "optional int: truncate response, default 4000 chars"
}
```

### Implementation
- Uses `httpx` to GET the URL
- HTML → text via `html2text` or basic tag stripping
- URL allowlist validation (no internal IPs, no file:// URIs)
- **Approval:** No
- **Config:** `TOOL_WEB_FETCH_ENABLED=true`
- **Effort:** ~1 hour

---

## Skill Updates Required

After implementing new tools, update these skill definitions:

| Skill | Add Tools |
|-------|-----------|
| `chat-with-kb` | `az_cost_query`, `az_monitor_logs`, `az_rest_api`, `generate_file`, `network_test`, `web_fetch` |
| `architect` | `az_cost_query`, `az_monitor_logs`, `az_rest_api`, `generate_file`, `az_policy_check`, `diagram_gen`, `az_advisor`, `network_test` |
| `kb-searcher` | No changes (KB-only skill) |
| `deploy-backend` | `generate_file`, `az_devops` |
| `deploy-frontend` | `generate_file`, `az_devops` |
| `local-runner` | `network_test` |

## Implementation Order

1. `az_login_check` (foundation — all Azure tools depend on this)
2. `az_cost_query` (quick win, high daily use)
3. `az_monitor_logs` (high impact for debugging)
4. `az_rest_api` (covers gaps in CLI)
5. `generate_file` (enables deliverables)
6. `az_devops` (team workflow integration)
7. `az_policy_check` (governance)
8. `diagram_gen` (architecture reviews)
9. `network_test` (connectivity debugging)
10. `az_advisor` (free optimization insights)
11. `web_fetch` (general research)
