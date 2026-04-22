# Agent Learnings

This file records known issues, mistakes, and solutions discovered during tool execution.
The agent consults this before running commands to avoid repeating errors.

---

## [gotcha] Cost Management REST API can return HTTP 429 on back-to-back queries
- **Date**: 2026-04-21 16:37 UTC
- **Tool**: az_cost_query
- **Details**: When querying multiple subscriptions for Cost Management data, do not call the second subscription immediately after the first. The API can rate-limit per subscription or tenant. The az_cost_query tool retries once on 429, but if it persists, wait a few minutes before retrying. Querying one subscription at a time with a delay is more reliable.

## [gotcha] Key Vault access can be blocked by missing data-plane permissions or network restrictions
- **Date**: 2026-04-21 16:17 UTC
- **Tool**: az_cli
- **Details**: Listing secrets in Key Vaults can fail with `Forbidden` (missing secrets list permission) or `Public network access is disabled` (needs private endpoint). Resource Graph confirms vault existence but not data-plane access. Always check both RBAC and network config before assuming Key Vault is inaccessible.

## [best-practice] Act first instead of asking for repeat confirmation when a tool path already implies approval
- **Date**: 2026-04-21 16:38 UTC
- **Tool**: general
- **Details**: When the user asks for an action and the workflow already provides a tool-based acceptance path (for example a yes/no tool call), do not ask the user again for confirmation. Proceed by attempting the tool call directly and use the tool response as the acceptance signal.

## [known-issue] Azure DevOps CLI requires proper authorization — TF400813
- **Date**: 2026-04-22 00:34 UTC
- **Tool**: az_devops
- **Details**: az_devops operations can fail with TF400813 if the current Azure identity lacks project access. Verify the Azure DevOps org/project and permissions before retrying. The tool requires the azure-devops CLI extension and a properly authorized identity or PAT.

## [best-practice] Use ResourceContainers (not Resources) for subscriptions and resource groups
- **Date**: 2026-04-21 22:59 UTC
- **Tool**: az_resource_graph
- **Details**: For listing subscriptions use `ResourceContainers | where type == 'microsoft.resources/subscriptions'`. For listing resource groups use `ResourceContainers | where type == 'microsoft.resources/subscriptions/resourcegroups'`. Querying `Resources` for these returns zero records.

## [syntax-fix] PowerShell script calling az must avoid pipeline execution of the az executable
- **Date**: 2026-04-22 02:00 UTC
- **Tool**: run_shell
- **Details**: A PowerShell script that used `az graph query -q $query -o json | ConvertFrom-Json` failed with `Cannot run a document in the middle of a pipeline: ...\az`. The fix is to assign the az output to a variable first (for example `$json = az graph query ... -o json`) and then pipe `$json | ConvertFrom-Json`, instead of piping the az executable directly.

## [syntax-fix] PowerShell cannot pipe az directly; capture output first
- **Date**: 2026-04-22 02:13 UTC
- **Tool**: run_shell
- **Details**: A PowerShell command that piped `az graph query ... | Out-String` failed with `Cannot run a document in the middle of a pipeline`. The correct approach is to assign the az output to a variable first (for example `$json = az graph query ... -o json`) and then process `$json`, rather than piping the az executable directly.

## [syntax-fix] Resource Graph subscription query should not project nonexistent state field
- **Date**: 2026-04-22 20:54 UTC
- **Tool**: az_resource_graph
- **Details**: A query against ResourceContainers for microsoft.resources/subscriptions failed when projecting `state` because that field is not present for subscription records in this environment. The corrected query was `ResourceContainers | where type == 'microsoft.resources/subscriptions' | project subscriptionId, name, tenantId | order by name asc`. Resource Graph was the fastest/simplest approach and should be preferred over Az CLI for listing subscriptions.

## [best-practice] For AI model counts, query deployment child resources first
- **Date**: 2026-04-22 21:40 UTC
- **Tool**: az_rest_api
- **Details**: When asked how many AI models are deployed across subscriptions, do not stop at listing AI hosting resources (Cognitive Services accounts or ML workspaces). The correct first pass is to enumerate Microsoft.CognitiveServices/accounts/{account}/deployments for each OpenAI/AIServices account and count the deployment child resources, separating Succeeded/Enabled from Disabled. Resource Graph is useful for finding the parent accounts, but it does not reliably expose the deployment layer.

