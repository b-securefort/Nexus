# Azure Resource Graph — Recipes & Known Quirks

Use `az_resource_graph` for all read-only Azure queries. It is fast, requires no approval, and covers most listing/counting scenarios. This file documents the non-obvious patterns that the agent has repeatedly had to learn the hard way.

---

## 1. Subscriptions and Resource Groups live in `ResourceContainers`, not `Resources`

| What you want | Correct table | Example KQL |
|---|---|---|
| List all subscriptions | `ResourceContainers` | `ResourceContainers \| where type == 'microsoft.resources/subscriptions' \| project subscriptionId, name, tenantId \| order by name asc` |
| List all resource groups | `ResourceContainers` | `ResourceContainers \| where type == 'microsoft.resources/subscriptions/resourcegroups' \| project name, resourceGroup, subscriptionId, location` |
| Count resource groups per subscription | `ResourceContainers` | `ResourceContainers \| where type == 'microsoft.resources/subscriptions/resourcegroups' \| summarize count() by subscriptionId` |

Querying `Resources` for subscriptions or resource groups returns **zero records**. Always use `ResourceContainers` for these.

---

## 2. The `state` field does not exist on subscription records

A query like:

```kql
ResourceContainers
| where type == 'microsoft.resources/subscriptions'
| project subscriptionId, name, state
```

…will fail or return nulls because `state` is not a field on subscription records in Resource Graph. Use `subscriptionId`, `name`, and `tenantId` instead.

---

## 3. Resource Graph does NOT surface child-level deployment resources

Resource Graph indexes **ARM top-level resources** only. The following are **not** returned by ARG even though they exist in ARM:

| Child resource type | Parent | How to query instead |
|---|---|---|
| `Microsoft.CognitiveServices/accounts/deployments` | AI/OpenAI account | `az_rest_api` GET on `/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{name}/deployments?api-version=2023-05-01` |
| `Microsoft.MachineLearningServices/workspaces/models` | ML workspace | `az_rest_api` or `az_cli` |
| VNet subnets | VNet | Available as `properties.subnets` on the parent VNet resource in ARG |

**Pattern for AI deployment counts:**
1. Use ARG to find all `Microsoft.CognitiveServices/accounts` across subscriptions.
2. For each account, call `az_rest_api` to list `/deployments` child resources.
3. Aggregate by model name and provisioning state (`Succeeded` = active).

Do **not** stop at counting parent accounts — that only tells you how many AI hubs exist, not how many models are deployed.

---

## 4. Useful ARG patterns

### All VMs across subscriptions
```kql
Resources
| where type == 'microsoft.compute/virtualmachines'
| project name, resourceGroup, location, subscriptionId, properties.hardwareProfile.vmSize
| order by subscriptionId, name asc
```

### Resources by tag
```kql
Resources
| where tags['environment'] == 'prod'
| project name, type, resourceGroup, subscriptionId
```

### RBAC assignments on a resource group
```kql
AuthorizationResources
| where type == 'microsoft.authorization/roleassignments'
| where resourceGroup == 'my-rg'
| project principalId, roleDefinitionId, scope
```

### All Private Endpoints
```kql
Resources
| where type == 'microsoft.network/privateendpoints'
| project name, resourceGroup, subscriptionId, properties.privateLinkServiceConnections
```

---

## 5. `let` bindings are NOT supported in Resource Graph KQL

Resource Graph implements a subset of KQL. `let` statements are **not supported**. Inline all values directly in the query:

```kql
// Wrong — will fail
let env = 'prod';
Resources | where tags['environment'] == env

// Correct
Resources | where tags['environment'] == 'prod'
```

---

## 6. ARG result limits

- Default result limit: **1,000 rows** per query.
- Use `| top N` or `| limit N` to control result size.
- For full result sets, use `$skipToken` pagination (available via `az_rest_api` against the ARG REST endpoint directly, not via the CLI).
