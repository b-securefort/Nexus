# Nexus PRD Extension — v2 Features

> Features not covered in the original `Nexus_PRD.md` (v1 scope).
> These extend the tool system, agent behavior, and operational capabilities.

---

## EXT-1: Azure CLI Auth Pre-Check

### Problem
All Azure CLI tools (`az_cli`, `az_resource_graph`) assume the host machine is already logged in via `az login`. If not logged in, tools fail at runtime and the agent wastes 3 retry cycles on an unrecoverable error before giving up.

### Solution
Add an automatic Azure login state check before any Azure CLI tool execution.

**Behavior:**
1. On first Azure tool call per session, run `az account show --output json`
2. If logged in → cache subscription/tenant/user info, inject into system prompt as "Azure Context"
3. If not logged in → return a clear message with `az login --use-device-code` instructions
4. Cache login state for 5 minutes (TTL) to avoid repeated checks
5. On auth error during tool execution, clear cache and re-check

**System prompt addition:**
```
## Azure Context
- Logged in: Yes
- Account: user@domain.com
- Subscription: My-Sub (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
- Tenant: My-Tenant (yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy)
```

If not logged in:
```
## Azure Context
- Logged in: No
- NOTE: Azure CLI is not authenticated. Before running any Azure commands,
  the user must run: az login --use-device-code
```

---

## EXT-2: Read-Only Cost Management Tool (`az_cost_query`)

### Problem
Checking Azure costs currently requires `az_cli` which needs user approval. Cost queries are read-only and should be frictionless.

### Solution
Dedicated `az_cost_query` tool with no approval requirement.

**Capabilities:**
- Usage costs by time period (last 7/30 days, last/this month, last 3 months)
- Cost breakdown by resource group, resource type, service, or location
- Budget status check
- Cost forecast

**Implementation:** Wraps `az cost management query` CLI commands with pre-built query templates.

---

## EXT-3: Log Analytics Query Tool (`az_monitor_logs`)

### Problem
Engineers spend 30%+ of debugging time in Log Analytics. Currently there's no way to query logs without using `az_cli` with approval.

### Solution
Dedicated `az_monitor_logs` tool for read-only KQL queries against Log Analytics workspaces.

**Capabilities:**
- Run KQL queries against any Log Analytics workspace
- Auto-discover workspaces via Resource Graph if not specified
- Cache workspace IDs after first lookup
- Return tabular results formatted for readability

**Implementation:** Wraps `az monitor log-analytics query` CLI command.

---

## EXT-4: Generic ARM REST API Tool (`az_rest_api`)

### Problem
The system prompt mentions REST API as the third tier in the tool hierarchy, but no REST API tool exists. Some Azure operations can only be done via REST (policy evaluation, diagnostic settings, RBAC checks).

### Solution
Generic `az_rest_api` tool for direct ARM REST calls.

**Capabilities:**
- GET, POST, PUT, PATCH, DELETE methods
- Auto-inject subscription ID in URI templates
- Separate approval policy: GET = no approval, mutating methods = approval required

**Implementation:** Wraps `az rest` CLI command.

---

## EXT-5: File Generation Tool (`generate_file`)

### Problem
The agent can analyze and advise but cannot produce deliverables. Engineers frequently need Bicep templates, scripts, reports, and CSV exports.

### Solution
`generate_file` tool that writes files to a sandboxed output directory.

**Capabilities:**
- Write Bicep, JSON, PowerShell, Bash, CSV, Markdown, Terraform, YAML files
- Path traversal guard (sandboxed to `output/` directory)
- Returns confirmation with path and size

**Security:**
- No approval needed (sandboxed write-only directory)
- Cannot overwrite existing files (append timestamp if conflict)
- Cannot write outside the output directory
- Blocked extensions: `.exe`, `.dll`, `.bat`, `.cmd`, `.com`, `.msi`

---

## EXT-6: Azure DevOps Integration (`az_devops`)

### Problem
Most Azure teams use Azure DevOps for CI/CD. The agent can't query pipeline status, recent builds, PRs, or work items.

### Solution
Read-only `az_devops` tool for Azure DevOps queries.

**Capabilities:**
- List pipelines and recent builds
- List and filter pull requests
- Get work item details
- List repositories

**Configuration:**
- `AZ_DEVOPS_ORG` — Azure DevOps organization URL
- `AZ_DEVOPS_PROJECT` — Default project name

---

## EXT-7: Policy Compliance Tool (`az_policy_check`)

### Problem
Policy compliance is critical for governance but requires manual Azure Portal checks or complex CLI commands.

### Solution
Dedicated `az_policy_check` tool for compliance status.

**Capabilities:**
- Compliance summary (% compliant, top violations)
- List policy assignments
- List non-compliant resources with details
- Filter by scope, policy name, or impact

---

## EXT-8: Architecture Diagram Generator (`diagram_gen`)

### Problem
Engineers constantly need architecture diagrams for reviews, documentation, and incident analysis. Currently done manually.

### Solution
`diagram_gen` tool that creates Mermaid diagrams.

**Capabilities:**
- Generate resource topology diagrams from live Azure data
- Create network flow diagrams
- Custom Mermaid diagram generation
- Auto-discover mode: query Resource Graph → build topology automatically
- Save as `.mmd` files via `generate_file`

---

## EXT-9: Network Connectivity Test (`network_test`)

### Problem
"Can service A reach service B?" is a daily question. Currently requires shell commands.

### Solution
`network_test` tool for basic network diagnostics.

**Capabilities:**
- DNS lookup (resolve hostname)
- TCP connectivity test (host:port reachable?)
- Ping test
- Traceroute (when needed)

**Implementation:** Uses Python `socket` module for TCP tests (no subprocess), platform-specific commands for ping/traceroute.

---

## EXT-10: Azure Advisor Integration (`az_advisor`)

### Problem
Azure Advisor provides free optimization recommendations but requires Portal access.

### Solution
`az_advisor` tool for pulling recommendations.

**Capabilities:**
- Filter by category (cost, security, reliability, performance)
- Filter by impact (high, medium, low)
- Scope to resource group
- Summary view with actionable items

---

## EXT-11: General Web Fetch (`web_fetch`)

### Problem
`fetch_ms_docs` only searches Microsoft Learn. Engineers need to reference GitHub issues, Stack Overflow, vendor documentation, and Azure status pages.

### Solution
`web_fetch` tool for general URL content retrieval.

**Capabilities:**
- Fetch any URL and extract text content
- HTML-to-text conversion
- Configurable max response length
- URL security validation (no internal IPs, no `file://` URIs, no SSRF)

**Security:**
- URL allowlist/blocklist validation
- No access to internal network (RFC 1918 addresses blocked)
- Response truncation to prevent context overflow
- Rate limiting per domain

---

## EXT-12: Non-Blocking Event Loop (Infrastructure)

### Problem (Discovered during E2E testing)
The OpenAI SDK and tool execution use synchronous calls that block the asyncio event loop. This prevents concurrent request handling — health checks, approval POSTs, and other chat requests all stall while one tool is executing.

### Solution (Already implemented)
- OpenAI streaming wrapped in `asyncio.Queue` + `run_in_executor` thread
- Tool execution wrapped in `asyncio.to_thread()`
- Approval sweeper datetime fix (naive/aware mismatch)

---

## EXT-13: SSE Parser Spec Compliance (Infrastructure)

### Problem (Discovered during audit)
The React frontend's SSE parser didn't reset `eventType` on blank lines per the SSE specification. The `eventType` variable was re-declared inside each buffer chunk, losing state across chunks.

### Solution (Already implemented)
- Moved `eventType` to outer scope in both `sendChatMessage` and `resumeChat`
- Added blank-line reset (`eventType = ""`)
- Terminal client parser was already correct

---

## EXT-14: Terminal Client Inline Approval (Infrastructure)

### Problem (Discovered during audit)
Terminal client's synchronous SSE reading blocked the main thread, making it impossible to handle approvals inline.

### Solution (Already implemented)
- SSE reading runs in a background `threading.Thread`
- Main thread polls for approval events and prompts user
- Approval resolved via REST POST while stream stays open
- `on_approval_needed` callback pattern in API layer

---

## Cross-Cutting Concerns

### Shell Tool Environment Fix
The `run_shell` tool strips Azure environment variables (`AZURE_CONFIG_DIR`, etc.) from the subprocess environment. This means `az` commands run via `run_shell` may fail or use a different auth context than `az_cli`.

**Fix:** Forward Azure-related env vars in the shell tool's environment dict.

### System Prompt Updates
New tools require updates to the system prompt:
- Azure Context section (login state, subscription, tenant)
- Tool hierarchy update: `az_resource_graph` → `az_cost_query` → `az_monitor_logs` → `az_cli` → `az_rest_api`
- Retry strategy updates for new tool alternatives

### Skill Definition Updates
Each shared skill's `SKILL.md` needs updated `tools:` list — see ToDo.md for mapping.
