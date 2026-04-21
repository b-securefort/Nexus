# Agent Learnings

This file records known issues, mistakes, and solutions discovered during tool execution.
The agent consults this before running commands to avoid repeating errors.

---

## [workaround] Cost Management query works at subscription scope with configuration.costType=AmortizedCost and no aggregation/grouping
- **Date**: 2026-04-21 14:46 UTC
- **Tool**: az_cli
- **Details**: A direct Microsoft.CostManagement query against a subscription scope succeeded when using api-version=2023-11-01 and body {type:'Usage', timeframe:'MonthToDate', dataset:{granularity:'None', configuration:{costType:'AmortizedCost'}}}. The API rejected grouping/aggregation when configuration was present. The response only returned currency in this environment, so further filtering or a different dataset shape may be needed to retrieve numeric totals.

## [gotcha] Cost Management query returned only currency and sometimes 429 rate limits
- **Date**: 2026-04-21 14:48 UTC
- **Tool**: az_cli
- **Details**: A subscription-scoped Microsoft.CostManagement query with body {type:'Usage', timeframe:'MonthToDate', dataset:{granularity:'None', configuration:{costType:'AmortizedCost'}}} succeeded syntactically but returned only a Currency column (USD) instead of numeric totals in this environment. A second parallel subscription query hit HTTP 429 Too Many Requests. When this happens, retry with backoff or use the earlier known REST shape that previously returned numeric totals in another session.

## [syntax-fix] Cost Management actual-cost query requires Daily granularity
- **Date**: 2026-04-21 14:50 UTC
- **Tool**: az_cli
- **Details**: A subscription-scoped Microsoft.CostManagement query for actual cost rejected dataset.granularity=None with the error 'Missing dataset granularity; valid values: Daily'. Retrying with dataset.granularity='Daily' succeeded syntactically, but the environment returned only UsageDate and Currency rows, not numeric amounts. For actual cost queries in this environment, use granularity Daily as the minimum valid setting.

## [workaround] Actual cost query returns numeric totals when using Daily granularity plus PreTaxCost aggregation
- **Date**: 2026-04-21 14:53 UTC
- **Tool**: az_cli
- **Details**: For subscription-scoped Microsoft.CostManagement queries, the working REST shape for actual cost was {type:'Usage', timeframe:'MonthToDate', dataset:{granularity:'Daily', aggregation:{totalCost:{name:'PreTaxCost', function:'Sum'}}}}. Earlier attempts without aggregation returned only UsageDate/Currency rows. This shape returned numeric daily PreTaxCost rows and enabled deriving month-to-date totals by summing the results.

## [syntax-fix] ARG subscription query failed due to projecting a non-existent field
- **Date**: 2026-04-21 14:54 UTC
- **Tool**: az_resource_graph
- **Details**: A Resource Graph query against ResourceContainers failed when projecting `state` from `microsoft.resources/subscriptions`; the entity does not expose that field in this environment. The corrected query was `ResourceContainers | where type == 'microsoft.resources/subscriptions' | project subscriptionId, name | order by name asc | summarize subscriptionCount=count(), subscriptionNames=make_list(name)`. Resource Graph was the fastest/simplest approach for this read-only query compared with Az CLI or REST.

## [workaround] Cost Management actual-cost query returns numeric totals with Daily granularity plus PreTaxCost aggregation
- **Date**: 2026-04-21 14:55 UTC
- **Tool**: az_cli
- **Details**: Subscription-scoped Microsoft.CostManagement queries for actual cost worked with `timeframe=MonthToDate` and body `{"type":"Usage","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"PreTaxCost","function":"Sum"}}}}`. Earlier attempts without aggregation returned only UsageDate/Currency rows. This shape returned numeric daily PreTaxCost rows and allowed deriving month-to-date totals by summing the results.

## [gotcha] Cost Management daily query can return 429 and succeeds on immediate retry
- **Date**: 2026-04-21 14:57 UTC
- **Tool**: az_cli
- **Details**: A subscription-scoped Microsoft.CostManagement query for last-7-day daily actual costs hit HTTP 429 Too Many Requests on the first attempt, but succeeded on an immediate retry with the same request body. When this happens, retry once or with small backoff before changing the query shape.

## [best-practice] Cost Management resource-type breakdown works with grouping on ResourceType
- **Date**: 2026-04-21 16:14 UTC
- **Tool**: az_cli
- **Details**: A subscription-scoped Cost Management query for resource-type-wise actual cost succeeded with `timeframe=MonthToDate`, `dataset.granularity=None`, `aggregation.totalCost=PreTaxCost Sum`, and `grouping:[{"type":"Dimension","name":"ResourceType"}]`. This returned numeric PreTaxCost rows per resource type and is a useful pattern for cost analysis by resource type.

## [gotcha] Key Vault access can be blocked by missing data-plane permissions or network restrictions
- **Date**: 2026-04-21 16:17 UTC
- **Tool**: az_cli
- **Details**: Listing secrets in `keyvaultcsp01` failed with `Forbidden` because the caller lacks secrets list permission. Listing secrets in `kv-zn-beta-eus2-01` failed with `Public network access is disabled and request is not from a trusted service nor via an approved private link`. Key Vault inspection requires both data-plane authorization and network reachability; Resource Graph only confirms vault existence, not contents.

## [gotcha] Cost Management query can return 429 repeatedly on one subscription while another succeeds
- **Date**: 2026-04-21 16:34 UTC
- **Tool**: az_cli
- **Details**: When querying month-to-date PreTaxCost with Microsoft.CostManagement via az rest, one subscription returned numeric daily rows immediately, but the other subscription consistently returned HTTP 429 Too Many Requests on three retries. The working pattern for the successful subscription was body {"type":"Usage","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"PreTaxCost","function":"Sum"}}}}. If a subscription hits repeated 429s, retry later with backoff or query one subscription at a time.

## [best-practice] Back off between subscription Cost Management queries to reduce 429s
- **Date**: 2026-04-21 16:37 UTC
- **Tool**: az_cli
- **Details**: When querying multiple subscriptions for Cost Management data, do not call the second subscription immediately after the first. Insert a short sleep/backoff between requests because the API can rate-limit per subscription or tenant, causing HTTP 429 Too Many Requests. Querying one subscription at a time with a delay is more reliable than parallel or back-to-back requests.

## [best-practice] Act first instead of asking for repeat confirmation when a tool path already implies approval
- **Date**: 2026-04-21 16:38 UTC
- **Tool**: general
- **Details**: When the user asks for an action and the workflow already provides a tool-based acceptance path (for example a yes/no tool call), do not ask the user again for confirmation. Proceed by attempting the tool call directly and use the tool response as the acceptance signal. This reduces back-and-forth and keeps the assistant proactive.

## [known-issue] Cost queries must use real subscription IDs, not placeholders
- **Date**: 2026-04-21 21:03 UTC
- **Tool**: az_cli
- **Details**: A cost query attempt failed because placeholder subscription IDs were used, resulting in 'Subscription not found'. Before calling Microsoft.CostManagement, first retrieve the actual subscription IDs from Resource Graph, then query each subscription scope individually.

## [workaround] Monthly subscription cost can be derived from daily PreTaxCost rows
- **Date**: 2026-04-21 21:03 UTC
- **Tool**: az_cli
- **Details**: Subscription-scoped Microsoft.CostManagement queries returned numeric daily PreTaxCost rows when using body {"type":"Usage","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"PreTaxCost","function":"Sum"}}}}. Summing the returned daily rows produced the month-to-date cost for each subscription.

## [best-practice] Use Resource Graph first to inventory Azure services in the environment
- **Date**: 2026-04-21 21:28 UTC
- **Tool**: az_resource_graph
- **Details**: For read-only environment inventory, Azure Resource Graph quickly returned resource counts by type across subscriptions and was sufficient to summarize deployed services. This was faster and simpler than Azure CLI or REST for a high-level service inventory.

## [syntax-fix] Resource Graph resource-group inventory should query live RGs directly; datatable join syntax failed in this environment
- **Date**: 2026-04-21 22:57 UTC
- **Tool**: az_resource_graph
- **Details**: Attempting to compare documented resource groups to live RGs with a KQL datatable + fullouter join caused ParserFailure in Azure Resource Graph. The working approach was to query live resource groups directly with `Resources | where type =~ 'microsoft.resources/subscriptions/resourcegroups' | project resourceGroup=name, location, subscriptionId | order by resourceGroup asc` and then compare against the documented list separately. Resource Graph was still the fastest/simplest read-only approach in the tool hierarchy (Resource Graph > Az CLI > REST API).

## [syntax-fix] Resource Graph query for storage account properties failed due to invalid KQL syntax; corrected projection worked
- **Date**: 2026-04-21 22:57 UTC
- **Tool**: az_resource_graph
- **Details**: A Resource Graph query failed because it used an invalid expression in the projection (`properties.encryption.services.blob.enabled` combined with a malformed field reference). The parser reported `ParserFailure` at the `=` token. The corrected and successful query was: `Resources | where type =~ 'microsoft.storage/storageaccounts' | project id, name, location, sku=tostring(sku.name), encryption=tostring(properties.encryption.services.blob.enabled), accessTier=tostring(properties.accessTier)`. Resource Graph was the fastest/simplest approach for this read-only inventory query in the tool hierarchy.

## [best-practice] Use Resource Graph for fast cross-subscription RG inventory and richer metadata
- **Date**: 2026-04-21 22:59 UTC
- **Tool**: az_resource_graph
- **Details**: For listing resource groups across accessible subscriptions, Resource Graph query on ResourceContainers with type == 'microsoft.resources/subscriptions/resourcegroups' returned subscriptionId, location, managedBy, tags, and id in one call. This is faster and more detailed than az group list for inventory comparisons, while az group list remains a simple CLI view scoped to the current context/subscription unless otherwise specified.

