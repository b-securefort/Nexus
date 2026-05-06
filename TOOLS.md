# Nexus Tool Registry — Raw List

All tools: implemented (✅), planned (📋).  
Roles: `KB` = knowledge base · `AI` = Azure Infra · `AArch` = Azure Architect · `AEng` = Azure Engineer · `SecArch` = Security Architect

---

## Implemented Tools (24)

| Tool | Approval | Role | Description |
|------|----------|------|-------------|
| `read_kb_file` | No | KB | Read a file from the knowledge base by relative path |
| `search_kb` | No | KB | Token-scored keyword search over KB index (title × 3, tags × 2, summary × 1) |
| `search_kb_semantic` | No | KB | LLM-powered search: query expansion + synonym expansion + re-ranking |
| `read_learnings` | No | KB | Read the agent's persistent mistake/fix memory (learn.md) |
| `update_learnings` | No | KB | Append a new learning entry to learn.md |
| `fetch_ms_docs` | No | AI | Search Microsoft Learn documentation — returns top 5 results |
| `web_fetch` | No | AI | Fetch and extract text from any HTTPS URL |
| `run_shell` | **Yes** | AI | Execute a shell / PowerShell command |
| `az_cli` | **Yes** | AI | Execute an Azure CLI command (uses managed identity or SP) |
| `az_resource_graph` | No | AI | Run KQL queries against Azure Resource Graph (read-only) |
| `az_cost_query` | No | AI | Query Azure Cost Management — spend by resource/service/tag |
| `az_monitor_logs` | No | AI | Run KQL against Log Analytics workspaces |
| `az_rest_api` | GET: No / Mutate: **Yes** | AI | Call any Azure REST / ARM endpoint |
| `az_devops` | No / **Yes** for writes | AI | Azure DevOps — Boards, Repos, Pipelines, Wiki |
| `az_policy_check` | No | AI | Policy compliance summaries and non-compliant resource lists |
| `az_advisor` | No | AI | Azure Advisor recommendations (cost, security, reliability, perf, ops) |
| `network_test` | No | AEng | DNS lookup, TCP port check, NSG rule query |
| `diagram_gen` | No | AArch | Generate architecture diagrams as draw.io / mermaid |
| `validate_drawio` | No | AArch | Validate a draw.io XML file for structural correctness |
| `generate_file` | No | AI | Write a generated artefact (IaC, config, markdown) to the output directory |
| `search_stack_overflow` | No | AI | Stack Exchange API — questions with score, accepted-answer flag, and vote count |
| `search_github` | No | AI | GitHub Search API — repositories (stars, topics) or code files |
| `search_azure_updates` | No | AI | Azure Updates RSS feed — GA releases, previews, retirements, filtered by keyword |
| `web_search` | No | AI | DuckDuckGo HTML search — catch-all for Reddit, Tech Community, blogs. Supports `site:` shortcuts |

---

## Planned — Azure Architect

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `az_pricing_lookup` | No | Azure Retail Prices API — SKU + region + meter pricing | `prices.azure.com/api/retail/prices` OData filter |
| `az_service_health` | No | Active incidents, retirements, advisories per subscription/region | `Microsoft.ResourceHealth/events` + `availabilityStatuses` |
| `az_quota_check` | No | vCPU / network / storage quota vs current usage per sub+region | `Microsoft.Capacity/resourceProviders/.../usages` |
| `az_waf_review` | No | Well-Architected Framework 5-pillar checklist for a workload | Prompt-driven; outputs JSON pillar scores + gaps |
| `az_landing_zone_check` | No | CAF/ALZ baseline: mgmt groups, policy, hub-spoke, log sinks, Defender | Composite: Resource Graph + policy + Defender APIs |
| `bicep_what_if` | No | ARM/Bicep deployment what-if — shows blast radius before deploy | Wrap `az deployment <scope> what-if` |
| `bicep_lint` | No | Lint Bicep/ARM/Terraform files before review | `bicep build --stdout` + `tflint` |
| `compare_services` | No | Structured comparison of two Azure services for a given workload | KB-backed + retail pricing |
| `tco_estimate` | No | Aggregate pricing lookups into monthly/annual TCO with assumptions | Composes `az_pricing_lookup` |

---

## Planned — Azure Engineer

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `kubectl` | Read: No / Write: **Yes** | kubectl — read verbs (get/describe/logs/top) skip approval; write verbs require it | Whitelist read verbs |
| `keyvault_peek` | No | Secret/key/cert metadata (names, versions, expiry) — never values | `az keyvault {secret,cert,key} list` + expiry filter |
| `storage_inspect` | No | List containers, blobs, SAS expiry, lifecycle rules, firewall rules | `az storage` + Resource Graph |
| `appservice_logs` | No | Stream App Service / Functions log tail (capped) | `az webapp log tail` with timeout |
| `acr_inspect` | No | List ACR repos/tags, last-pushed, Defender scan results | `az acr repository show-manifests` + Defender assessment |
| `deployment_status` | No | Recent ARM deployments at sub/RG scope with errors expanded | `az deployment group list` + `operation list` |
| `activity_log_query` | No | "Who changed what, when" over last N hours | `az monitor activity-log list` |
| `defender_secure_score` | No | Defender for Cloud secure score + top recommendations | `Microsoft.Security/secureScores` + `assessments` |
| `rbac_who_has_access` | No | Role assignments for a resource including nested group expansion | `az role assignment list` + Graph group expansion |
| `dns_resolve` | No | DNS lookup against a specific resolver or Azure Private DNS zone | `dnspython` |
| `tls_cert_inspect` | No | Certificate chain, expiry, SAN, cipher suite for hostname:port | `ssl.get_server_certificate` + `ssl.wrap_socket` |
| `resource_lock_check` | No | CanNotDelete / ReadOnly locks at resource and parent scopes | `az lock list` |
| `diagnostic_settings_check` | No | Verify a resource sends logs to workspace/storage/event hub | `az monitor diagnostic-settings list` |
| `managed_identity_check` | No | MI role assignments, federated credentials, overprivileged identities | Graph + role assignments |

---

## Planned — Security Architect

### Identity & Access

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `entra_privileged_roles` | No | Who holds Global Admin, Owner, Contributor, User Access Admin across tenant | Graph `roleManagement/directory/roleAssignments` + members |
| `entra_risky_users` | No | Entra ID Protection risky users and sign-in risk events | Graph `identityProtection/riskyUsers` + `riskDetections` |
| `entra_conditional_access_audit` | No | All CA policies — flags gaps (no MFA for admins, no device compliance) | Graph `identity/conditionalAccess/policies` |
| `pim_assignments` | No | PIM-eligible vs permanent role assignments | Graph `privilegedAccess/aadRoles/roleAssignments` |
| `service_principal_audit` | No | SPs with expiring creds, unused SPs (last-sign-in > 90d), SPs with Owner/Contributor | Graph `servicePrincipals` + `signInActivity` |

### Defender for Cloud / CSPM

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `defender_assessments` | No | Security assessments per resource type — the building block of every cloud security review | `Microsoft.Security/assessments` Resource Graph |
| `defender_attack_paths` | No | Chained attack paths from internet to crown-jewel resources | `Microsoft.Security/attackPaths` API |
| `defender_jit_review` | No | JIT VM access policies — which VMs have it, recent requests | `Microsoft.Security/locations/jitNetworkAccessPolicies` |
| `defender_plans_check` | No | Which Defender plans are enabled (CSPM, Servers, Containers, Storage, AppService…) | `Microsoft.Security/pricings` |
| `regulatory_compliance` | No | CIS / NIST SP 800-53 / PCI-DSS / ISO 27001 control pass/fail state | `Microsoft.Security/regulatoryComplianceStandards` |

### Sentinel / SIEM

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `sentinel_incidents` | No | Open incidents by severity/status with affected entities | Sentinel `incidents` API |
| `sentinel_analytics_rules` | No | Enabled/disabled detection rules with MITRE ATT&CK tactic mapping | Sentinel `alertRules` API |
| `sentinel_watchlist_query` | No | Query a named watchlist (VIPs, threat IPs, asset inventory) | Sentinel `watchlists/{name}/watchlistItems` |
| `sentinel_threat_intel` | No | TI indicators (IPs, domains, file hashes) from the Sentinel TI connector | `Microsoft.SecurityInsights/threatIntelligence/indicators` |

### Network Security

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `firewall_policy_audit` | No | Azure Firewall policy rules — flag allow-all or internet-to-internal rules | `az network firewall policy rule-collection-group list` |
| `waf_policy_audit` | No | WAF policy (Front Door / App Gateway) — mode, ruleset version, custom overrides | `az network application-gateway waf-policy list` |
| `nsg_effective_rules` | No | Effective NSG rules for a NIC/subnet (after inheritance) — not raw NSG | `az network nic list-effective-nsg` |
| `private_endpoint_coverage` | No | All PaaS resources lacking a private endpoint (publicly reachable) | Resource Graph: `properties.privateEndpointConnections == null` |
| `ddos_protection_check` | No | DDoS Protection Plan attachment per VNet | Resource Graph on `virtualNetworks` |
| `public_ip_exposure` | No | All public IPs in a subscription — VMs, App GWs, LBs, Bastion | Resource Graph `Microsoft.Network/publicIPAddresses` |

### Secrets & Data

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `keyvault_security_audit` | No | Firewall, soft-delete, purge protection, RBAC model, cert expiry < 30d, key rotation | `az keyvault show` + cert/key metadata |
| `storage_security_audit` | No | Public blob access, SAS without expiry, firewall off, CMK vs PMK, immutability | `az storage account show` + Resource Graph |
| `tls_cert_inspect` | No | Certificate chain, expiry, SAN, cipher suite for hostname:port | `ssl.get_server_certificate` |
| `secret_scan_local` | No | Scan local directory/file for hard-coded Azure secrets (SAS tokens, storage keys, AAD secrets) | Regex patterns for Azure credential formats |

### Vulnerability & Posture

| Tool | Approval | Description | Implementation hint |
|------|----------|-------------|---------------------|
| `vm_vulnerability_assessment` | No | VM CVEs, CVSS scores, patch status from Defender for Servers | `Microsoft.Security/assessments` filtered to VA findings |
| `container_image_scan` | No | Defender for Containers CVE scan results per ACR repo/tag | `Microsoft.Security/assessments` for container registries |

---

## Counts

| Status | Count |
|--------|-------|
| Implemented | 24 |
| Planned — Azure Architect | 9 |
| Planned — Azure Engineer | 14 |
| Planned — Security Architect | 24 |
| **Total** | **67** |
