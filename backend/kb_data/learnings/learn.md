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

## [best-practice] Return the actual AI deployment inventory directly when asked for deployed models
- **Date**: 2026-04-24 01:02 UTC
- **Tool**: az_rest_api
- **Details**: When users ask how many AI models are deployed, do not stop at counting parent Azure AI / Cognitive Services accounts or ML workspaces. Query the deployment child resources for each account first, then present a deployment-level table including account, model name, region, and provisioning status. Explicitly separate active/succeeded deployments from disabled ones so the answer matches the user's intent on the first pass.

## [workaround] Use parent AI account properties when ARG does not surface child deployment resources
- **Date**: 2026-04-24 01:29 UTC
- **Tool**: az_resource_graph
- **Details**: Resource Graph only returned parent Microsoft.CognitiveServices/accounts and Microsoft.MachineLearningServices/workspaces records even though deployed AI models are likely present as child resources or service-side objects. When ARG does not enumerate child deployments, inspect the parent resource properties for endpoints, account kind, and related workspace metadata, then use service-specific APIs/CLI only if a true deployment count is needed.

## [workaround] Add a 5-second delay between back-to-back cost API calls to avoid 429 rate limits
- **Date**: 2026-04-24 19:21 UTC
- **Tool**: az_cost_query
- **Details**: When using az_cost_query for multiple related cost queries, immediate consecutive calls can trigger HTTP 429 Too Many Requests. The reliable workaround is to wait about 5 seconds before issuing the next query, especially when querying adjacent time windows or multiple group-bys for the same subscription. This is preferable to retrying instantly.

## [known-issue] Azure DevOps project and work item operations can fail due to Conditional Access blocking token issuance
- **Date**: 2026-04-26 01:07 UTC
- **Tool**: az_devops
- **Details**: Attempting to list Azure DevOps projects failed first because the Azure DevOps CLI extension/auth was not initialized, and a REST fallback then failed with AADSTS53003 Conditional Access blocking token issuance. The correct next step is to authenticate the environment with `az login --scope https://management.core.windows.net//.default` (or equivalent approved auth path) before retrying az devops or REST calls.

## [gotcha] draw.io validator can misclassify resource-sized vertices unless Azure2 image styles are explicit and spaced apart
- **Date**: 2026-05-06 02:15 UTC
- **Tool**: generate_file
- **Details**: When generating Azure draw.io diagrams, generic-looking image vertices can still be flagged as generic styles by the validator. The safest approach is to use explicit Azure2 image paths for every resource icon, keep observability services fully outside VNets/VPNs, and give large spacing between resources and monitoring nodes to avoid overlap heuristics.

