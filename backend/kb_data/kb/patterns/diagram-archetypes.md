# Diagram archetypes — starter skeletons for the structural IR engine

A good architecture diagram is a story with a spine, and most platform
diagrams tell one of a handful of stories. Each section below is one
**archetype**: when to use it, what its slots mean, and a complete,
detector-clean Diagram IR skeleton (the JSON authoring contract of
`generate_structured_diagram`). Start from the matching skeleton and edit —
rename slot nodes to the real workload, delete what doesn't apply, add
workload-specific pieces — instead of designing band structure from scratch.
The band structure, spine direction, and side-lane decisions are the expensive
part; they are pre-made here.

Every skeleton in this file is regression-tested (`tests/test_archetypes.py`):
it must lay out, route, and pass every geometry detector with zero defects.
If you edit this file, keep the `## <slug> — <title>` heading format and one
` ```json ` block per section.

## n-tier-web-app — request/response web workload

**Use when:** a user-facing app with a web tier, an API tier, and private data
stores; the classic PaaS workload. **Slots:** `webapp`/`apiapp` are the compute
slots (swap App Service for Container Apps / AKS as needed); `sql`/`kv` are the
data slots — keep each store paired with its private endpoint in the `pes`/
`stores` twin columns so PE → target always reads left→right. Monitoring stays
the rightmost tail.

```json
{
  "title": "N-tier web application",
  "direction": "LR",
  "containers": [
    {"id": "spine", "style": "band", "layout": "row", "children": ["edge", "webtier", "apitier", "datatier", "obs"]},
    {"id": "edge", "label": "Edge / Identity", "style": "group", "layout": "column", "parent": "spine", "children": ["afd", "entra"]},
    {"id": "webtier", "label": "Web tier", "style": "group", "layout": "column", "parent": "spine", "children": ["webapp"]},
    {"id": "apitier", "label": "API tier", "style": "group", "layout": "column", "parent": "spine", "children": ["apim", "apiapp"]},
    {"id": "datatier", "label": "Private data", "style": "group", "layout": "row", "parent": "spine", "children": ["pes", "stores"]},
    {"id": "pes", "style": "band", "layout": "column", "parent": "datatier", "children": ["pe_sql", "pe_kv"]},
    {"id": "stores", "style": "band", "layout": "column", "parent": "datatier", "children": ["sql", "kv"]},
    {"id": "obs", "label": "Monitoring", "style": "monitoring", "layout": "column", "parent": "spine", "children": ["appi", "law"]}
  ],
  "nodes": [
    {"id": "afd", "label": "Front Door", "icon": "azure/front_doors", "parent": "edge",
     "adornments": [{"icon": "azure/web_application_firewall", "corner": "top-right", "label": "WAF"}]},
    {"id": "entra", "label": "Entra ID", "icon": "azure/entra_id", "parent": "edge"},
    {"id": "webapp", "label": "Web app", "icon": "azure/app_services", "parent": "webtier"},
    {"id": "apim", "label": "API Management", "icon": "azure/api_management", "parent": "apitier"},
    {"id": "apiapp", "label": "API app", "icon": "azure/app_services", "parent": "apitier"},
    {"id": "pe_sql", "label": "pe-sql", "icon": "azure/private_endpoint", "parent": "pes"},
    {"id": "pe_kv", "label": "pe-kv", "icon": "azure/private_endpoint", "parent": "pes"},
    {"id": "sql", "label": "SQL Database", "icon": "azure/sql_database", "parent": "stores"},
    {"id": "kv", "label": "Key Vault", "icon": "azure/key_vaults", "parent": "stores"},
    {"id": "appi", "label": "App Insights", "icon": "azure/application_insights", "parent": "obs"},
    {"id": "law", "label": "Log Analytics", "icon": "azure/log_analytics", "parent": "obs"}
  ],
  "edges": [
    {"source": "afd", "target": "webapp", "type": "flow", "label": "HTTPS"},
    {"source": "webapp", "target": "apim", "type": "flow"},
    {"source": "apim", "target": "apiapp", "type": "flow"},
    {"source": "webapp", "target": "entra", "type": "flow", "label": "auth"},
    {"source": "apiapp", "target": "pe_sql", "type": "private"},
    {"source": "pe_sql", "target": "sql", "type": "private"},
    {"source": "apiapp", "target": "pe_kv", "type": "private"},
    {"source": "pe_kv", "target": "kv", "type": "private"},
    {"source": "apim", "target": "appi", "type": "telemetry"},
    {"source": "appi", "target": "law", "type": "telemetry"}
  ]
}
```

## hub-spoke-network — hybrid connectivity topology

**Use when:** the subject is the network itself — on-prem connectivity, a hub
VNet with gateway/firewall/shared services, and workload spokes. **Slots:**
`vm1`/`aks` stand for whatever each spoke hosts; add spokes by cloning a
`spokeN` container into the `spokes` band. Private DNS lives in the hub's
shared subnet with one `dns` edge per spoke — never a duplicated zone per
spoke. All north-south traffic transits the firewall.

```json
{
  "title": "Hub-spoke network",
  "direction": "LR",
  "containers": [
    {"id": "spine", "style": "band", "layout": "row", "children": ["onprem", "hub", "spokes"]},
    {"id": "onprem", "label": "On-premises", "style": "zone", "layout": "column", "parent": "spine", "children": ["users", "localgw"]},
    {"id": "hub", "label": "Hub VNet", "style": "vnet", "layout": "row", "parent": "spine", "children": ["gwsub", "fwsub", "shared"]},
    {"id": "gwsub", "label": "GatewaySubnet", "style": "subnet", "layout": "column", "parent": "hub", "children": ["vpngw"]},
    {"id": "fwsub", "label": "AzureFirewallSubnet", "style": "subnet", "layout": "column", "parent": "hub", "children": ["fw"]},
    {"id": "shared", "label": "Shared services", "style": "subnet", "layout": "column", "parent": "hub", "children": ["bastion", "dns"]},
    {"id": "spokes", "style": "band", "layout": "column", "parent": "spine", "children": ["spoke1", "spoke2"]},
    {"id": "spoke1", "label": "Spoke VNet — workload A", "style": "vnet", "layout": "row", "parent": "spokes", "children": ["s1sub"]},
    {"id": "s1sub", "label": "snet-workload", "style": "subnet", "layout": "row", "parent": "spoke1", "children": ["vm1"]},
    {"id": "spoke2", "label": "Spoke VNet — workload B", "style": "vnet", "layout": "row", "parent": "spokes", "children": ["s2sub"]},
    {"id": "s2sub", "label": "snet-aks", "style": "subnet", "layout": "row", "parent": "spoke2", "children": ["aks"]}
  ],
  "nodes": [
    {"id": "users", "label": "Corp users", "icon": "shape/actor", "parent": "onprem"},
    {"id": "localgw", "label": "Local gateway", "icon": "azure/virtual_network_gateways", "parent": "onprem"},
    {"id": "vpngw", "label": "VPN Gateway", "icon": "azure/vpn_gateway", "parent": "gwsub"},
    {"id": "fw", "label": "Azure Firewall", "icon": "azure/firewalls", "parent": "fwsub"},
    {"id": "bastion", "label": "Bastion", "icon": "azure/bastions", "parent": "shared"},
    {"id": "dns", "label": "Private DNS", "icon": "azure/dns_private_zones", "parent": "shared"},
    {"id": "vm1", "label": "VM workload", "icon": "azure/vm", "parent": "s1sub"},
    {"id": "aks", "label": "AKS", "icon": "azure/aks", "parent": "s2sub"}
  ],
  "edges": [
    {"source": "users", "target": "localgw", "type": "flow"},
    {"source": "localgw", "target": "vpngw", "type": "flow", "label": "IPsec"},
    {"source": "vpngw", "target": "fw", "type": "flow"},
    {"source": "fw", "target": "vm1", "type": "flow"},
    {"source": "fw", "target": "aks", "type": "flow"},
    {"source": "dns", "target": "spoke1", "type": "dns"},
    {"source": "dns", "target": "spoke2", "type": "dns"}
  ]
}
```

## event-driven — async producers and consumers

**Use when:** producers emit events into a broker and independent consumers
process them; throughput and decoupling are the story, not request/response.
**Slots:** `producer_app`/`producer_ext` are event sources; `eh` is the broker
slot (swap Event Hubs for Event Grid or Service Bus by changing the icon);
`func_a`/`func_b` are consumer slots fanning out to their own sinks.

```json
{
  "title": "Event-driven processing",
  "direction": "LR",
  "containers": [
    {"id": "spine", "style": "band", "layout": "row", "children": ["producers", "broker", "consumers", "sinks"]},
    {"id": "producers", "label": "Producers", "style": "group", "layout": "column", "parent": "spine", "children": ["producer_app", "producer_ext"]},
    {"id": "broker", "label": "Ingestion", "style": "group", "layout": "column", "parent": "spine", "children": ["eh"]},
    {"id": "consumers", "label": "Processing", "style": "group", "layout": "column", "parent": "spine", "children": ["func_a", "func_b"]},
    {"id": "sinks", "label": "Stores", "style": "group", "layout": "column", "parent": "spine", "children": ["cosmos", "blob"]}
  ],
  "nodes": [
    {"id": "producer_app", "label": "Order service", "icon": "azure/app_services", "parent": "producers"},
    {"id": "producer_ext", "label": "External feed", "icon": "shape/cloud", "parent": "producers"},
    {"id": "eh", "label": "Event Hubs", "icon": "azure/event_hubs", "parent": "broker"},
    {"id": "func_a", "label": "Enrich fn", "icon": "azure/function_apps", "parent": "consumers"},
    {"id": "func_b", "label": "Archive fn", "icon": "azure/function_apps", "parent": "consumers"},
    {"id": "cosmos", "label": "Cosmos DB", "icon": "azure/cosmos_db", "parent": "sinks"},
    {"id": "blob", "label": "Blob archive", "icon": "azure/storage_accounts", "parent": "sinks"}
  ],
  "edges": [
    {"source": "producer_app", "target": "eh", "type": "flow", "label": "events"},
    {"source": "producer_ext", "target": "eh", "type": "flow"},
    {"source": "eh", "target": "func_a", "type": "flow"},
    {"source": "eh", "target": "func_b", "type": "flow"},
    {"source": "func_a", "target": "cosmos", "type": "private"},
    {"source": "func_b", "target": "blob", "type": "private"}
  ]
}
```

## rag-ai-app — retrieval-augmented AI application

**Use when:** an app answers with an LLM grounded in indexed content. Two
stories share the canvas: the query path (the spine) and the ingestion
pipeline (a band drawn UNDER the spine, feeding the index from below) — keep
them separate lanes; merging them into one row is the classic mistake.
**Slots:** `chatapp` is the orchestrator slot; `aoai`/`search` the AI plane;
`docs`/`ingest` the content pipeline.

```json
{
  "title": "RAG AI application",
  "direction": "LR",
  "containers": [
    {"id": "root", "style": "band", "layout": "column", "children": ["spine", "pipeline"]},
    {"id": "spine", "style": "band", "layout": "row", "parent": "root", "children": ["clients", "app", "ai"]},
    {"id": "clients", "label": "Clients", "style": "group", "layout": "column", "parent": "spine", "children": ["user"]},
    {"id": "app", "label": "Orchestration", "style": "group", "layout": "column", "parent": "spine", "children": ["chatapp"]},
    {"id": "ai", "label": "AI services", "style": "group", "layout": "column", "parent": "spine", "children": ["aoai", "search"]},
    {"id": "pipeline", "label": "Content ingestion", "style": "group", "layout": "row", "parent": "root", "children": ["docs", "ingest"]}
  ],
  "nodes": [
    {"id": "user", "label": "User", "icon": "shape/actor", "parent": "clients"},
    {"id": "chatapp", "label": "Chat app", "icon": "azure/app_services", "parent": "app"},
    {"id": "aoai", "label": "Azure OpenAI", "icon": "azure/openai", "parent": "ai"},
    {"id": "search", "label": "AI Search", "icon": "azure/ai_search", "parent": "ai"},
    {"id": "docs", "label": "Documents", "icon": "azure/storage_accounts", "parent": "pipeline"},
    {"id": "ingest", "label": "Indexer fn", "icon": "azure/function_apps", "parent": "pipeline", "align_to": "search"}
  ],
  "edges": [
    {"source": "user", "target": "chatapp", "type": "flow", "label": "ask"},
    {"source": "chatapp", "target": "aoai", "type": "flow", "label": "prompt"},
    {"source": "chatapp", "target": "search", "type": "flow", "label": "retrieve"},
    {"source": "docs", "target": "ingest", "type": "flow"},
    {"source": "ingest", "target": "search", "type": "flow", "label": "index"}
  ]
}
```

## cicd-flow — build and release pipeline

**Use when:** the story is how a change reaches production, not the runtime
topology. Generic flowchart shapes carry the pipeline; cloud icons appear only
where a real service does (registry, environments). **Slots:** `build`/
`release` are pipeline stages; `staging`/`prod` the environment slots; the
`gate` decision is the approval boundary.

```json
{
  "title": "CI/CD pipeline",
  "direction": "LR",
  "containers": [
    {"id": "spine", "style": "band", "layout": "row", "children": ["devs", "ci", "registry", "cd", "envs"]},
    {"id": "devs", "label": "Source", "style": "group", "layout": "column", "parent": "spine", "children": ["dev", "repo"]},
    {"id": "ci", "label": "CI", "style": "group", "layout": "column", "parent": "spine", "children": ["build"]},
    {"id": "registry", "label": "Artifacts", "style": "group", "layout": "column", "parent": "spine", "children": ["acr"]},
    {"id": "cd", "label": "CD", "style": "group", "layout": "column", "parent": "spine", "children": ["release"]},
    {"id": "envs", "label": "Environments", "style": "group", "layout": "column", "parent": "spine", "children": ["staging", "gate", "prod"]}
  ],
  "nodes": [
    {"id": "dev", "label": "Developer", "icon": "shape/actor", "parent": "devs"},
    {"id": "repo", "label": "Git repo", "icon": "shape/document", "parent": "devs"},
    {"id": "build", "label": "Build & test", "icon": "shape/process", "parent": "ci"},
    {"id": "acr", "label": "Container Registry", "icon": "azure/container_registries", "parent": "registry"},
    {"id": "release", "label": "Release pipeline", "icon": "shape/process", "parent": "cd"},
    {"id": "staging", "label": "Staging", "icon": "azure/app_services", "parent": "envs"},
    {"id": "gate", "label": "Approved?", "icon": "shape/decision", "parent": "envs"},
    {"id": "prod", "label": "Production", "icon": "azure/app_services", "parent": "envs"}
  ],
  "edges": [
    {"source": "dev", "target": "repo", "type": "flow", "label": "push"},
    {"source": "repo", "target": "build", "type": "flow"},
    {"source": "build", "target": "acr", "type": "flow", "label": "image"},
    {"source": "acr", "target": "release", "type": "flow"},
    {"source": "release", "target": "staging", "type": "flow", "label": "deploy"},
    {"source": "staging", "target": "gate", "type": "flow"},
    {"source": "gate", "target": "prod", "type": "flow", "label": "promote"}
  ]
}
```

## landing-zone — platform foundations and workload subscriptions

**Use when:** the subject is the enterprise-scale structure itself: platform
services (connectivity, identity, management) on top, workload landing zones
below. TB direction — governance reads downward. **Slots:** clone an `lzN`
zone per landing zone; `vm`/`webapp` stand for each zone's workload plane.

```json
{
  "title": "Landing zone overview",
  "direction": "TB",
  "containers": [
    {"id": "root", "style": "band", "layout": "column", "children": ["platform", "workloads"]},
    {"id": "platform", "label": "Platform", "style": "zone", "layout": "row", "parent": "root", "children": ["conn", "ident", "mgmt"]},
    {"id": "conn", "label": "Connectivity", "style": "group", "layout": "column", "parent": "platform", "children": ["hubvnet", "vpngw"]},
    {"id": "ident", "label": "Identity", "style": "group", "layout": "column", "parent": "platform", "children": ["entra", "kv"]},
    {"id": "mgmt", "label": "Management", "style": "group", "layout": "column", "parent": "platform", "children": ["law", "policy"]},
    {"id": "workloads", "style": "band", "layout": "row", "parent": "root", "children": ["lz1", "lz2"]},
    {"id": "lz1", "label": "Landing zone — corp", "style": "zone", "layout": "column", "parent": "workloads", "children": ["vnet1", "vm"]},
    {"id": "lz2", "label": "Landing zone — online", "style": "zone", "layout": "column", "parent": "workloads", "children": ["vnet2", "webapp"]}
  ],
  "nodes": [
    {"id": "hubvnet", "label": "Hub VNet", "icon": "azure/virtual_networks", "parent": "conn"},
    {"id": "vpngw", "label": "VPN Gateway", "icon": "azure/vpn_gateway", "parent": "conn"},
    {"id": "entra", "label": "Entra ID", "icon": "azure/entra_id", "parent": "ident"},
    {"id": "kv", "label": "Key Vault", "icon": "azure/key_vaults", "parent": "ident"},
    {"id": "law", "label": "Log Analytics", "icon": "azure/log_analytics", "parent": "mgmt"},
    {"id": "policy", "label": "Azure Policy", "icon": "azure/policy", "parent": "mgmt"},
    {"id": "vnet1", "label": "Spoke VNet", "icon": "azure/virtual_networks", "parent": "lz1"},
    {"id": "vm", "label": "VM workload", "icon": "azure/vm", "parent": "lz1"},
    {"id": "vnet2", "label": "Spoke VNet", "icon": "azure/virtual_networks", "parent": "lz2"},
    {"id": "webapp", "label": "Web app", "icon": "azure/app_services", "parent": "lz2"}
  ],
  "edges": [
    {"source": "hubvnet", "target": "vnet1", "type": "private", "label": "peering"},
    {"source": "hubvnet", "target": "vnet2", "type": "private", "label": "peering"},
    {"source": "vnet1", "target": "vm", "type": "flow"},
    {"source": "vnet2", "target": "webapp", "type": "flow"}
  ]
}
```

## hub-spoke-workload — one workload drawn end-to-end through hub-spoke connectivity

**Use when:** the ask is BOTH the connectivity story (on-prem/users through hub
NVA into a spoke) AND the workload inside that spoke — the most common "draw my
app on our network" request. The spoke VNet box reads as its own left-to-right
mini-spine: `gateway subnet | app tier | private endpoints | data`. This is the
team-approved reference shape. **Slots:** `lb`/`fw` are the hub inspection
chain; `web`/`api` the app tier; `db` the data slot with its private endpoint
in the `pe_col` column. If the API itself is private-endpoint-fronted, add its
PE beside the api node (accept the one short backward hop the advisory will
note, or split the app tier into web | pe | api columns). Private DNS stays in
the hub, one `dns` edge to the spoke.

```json
{
  "title": "Hub-spoke workload",
  "direction": "LR",
  "containers": [
    {"id": "spine", "style": "band", "layout": "row", "children": ["clients", "hub", "spoke"]},
    {"id": "clients", "label": "Users", "style": "zone", "layout": "column", "parent": "spine", "children": ["user"]},
    {"id": "hub", "label": "Hub", "style": "zone", "layout": "column", "parent": "spine", "children": ["hub_services"]},
    {"id": "hub_services", "label": "Shared services", "style": "group", "layout": "column", "parent": "hub", "children": ["lb", "dns", "fw"]},
    {"id": "spoke", "label": "Spoke VNet", "style": "vnet", "layout": "row", "parent": "spine", "children": ["agw_subnet", "apps", "pe_col", "data"]},
    {"id": "agw_subnet", "label": "App Gateway subnet", "style": "subnet", "layout": "column", "parent": "spoke", "children": ["agw"]},
    {"id": "apps", "label": "Application tier", "style": "group", "layout": "column", "parent": "spoke", "children": ["web", "api"]},
    {"id": "pe_col", "label": "Private endpoints / integration", "style": "subnet", "layout": "column", "parent": "spoke", "children": ["pe_db", "integration"]},
    {"id": "data", "label": "Data", "style": "group", "layout": "column", "parent": "spoke", "children": ["db"]}
  ],
  "nodes": [
    {"id": "user", "label": "User", "icon": "shape/actor", "parent": "clients"},
    {"id": "lb", "label": "Load balancer", "icon": "azure/load_balancers", "parent": "hub_services"},
    {"id": "dns", "label": "Private DNS", "icon": "azure/dns_private_zones", "parent": "hub_services"},
    {"id": "fw", "label": "Firewall / NVA", "icon": "azure/firewalls", "parent": "hub_services"},
    {"id": "agw", "label": "Application Gateway", "icon": "azure/application_gateways", "parent": "agw_subnet",
     "adornments": [{"icon": "azure/web_application_firewall", "corner": "top-right", "label": "WAF"}]},
    {"id": "web", "label": "Web app", "icon": "azure/app_services", "parent": "apps"},
    {"id": "api", "label": "API app", "icon": "azure/app_services", "parent": "apps"},
    {"id": "pe_db", "label": "pe-db", "icon": "azure/private_endpoint", "parent": "pe_col"},
    {"id": "integration", "label": "Integration subnet", "icon": "azure/subnet", "parent": "pe_col"},
    {"id": "db", "label": "Database", "icon": "azure/sql_database", "parent": "data"}
  ],
  "edges": [
    {"source": "user", "target": "lb", "type": "flow", "label": "HTTPS"},
    {"source": "lb", "target": "fw", "type": "flow", "label": "inspect"},
    {"source": "fw", "target": "agw", "type": "flow"},
    {"source": "agw", "target": "web", "type": "flow", "label": "HTTPS"},
    {"source": "web", "target": "api", "type": "flow"},
    {"source": "api", "target": "pe_db", "type": "private"},
    {"source": "pe_db", "target": "db", "type": "private"},
    {"source": "web", "target": "integration", "type": "private"},
    {"source": "api", "target": "integration", "type": "private"},
    {"source": "dns", "target": "spoke", "type": "dns"},
    {"source": "hub", "target": "spoke", "type": "private", "label": "peering"}
  ]
}
```
