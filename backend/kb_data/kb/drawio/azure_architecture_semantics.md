# Azure Architecture Semantics for Diagrams

This guide is for the diagramming agent. It defines **what each Azure component is, where it lives, and what it must connect to** to be modelled correctly. Use it whenever you're tempted to drop a security or identity icon into a diagram for "good measure" — every component must serve a clear architectural role and be drawn at the right plane.

The validator catches layout mistakes (overlap, parenting, observability inside VNets). It does not catch **architectural mistakes** like putting a Managed Identity inside a subnet, or showing a Private Endpoint in the same subnet as the resource it exposes. That's what this guide is for.

---

## The mental model: three planes

Azure resources sit in one of three planes. Drawing them at the wrong plane is the most common architectural error.

| Plane | What it is | Where it goes in a diagram |
|---|---|---|
| **Network plane (subnet-resident)** | Real NICs, IPs, traffic flows through them | Inside a subnet container |
| **PaaS / data plane** | Managed services with optional private network presence (via PE or VNet integration) | Outside the VNet — connected to a subnet via Private Endpoint or VNet integration |
| **Control plane / global / regional** | Identity, monitoring, policy, DNS zones, Entra ID | Outside the VNet entirely — at canvas level or in a dedicated zone |

Quick sanity check before placing any icon: **"Does this resource own a NIC in a subnet?"** If yes → subnet-resident. If no → put it outside the VNet.

---

## Per-component reference

### Subnet-resident (drawn inside a subnet container)

These have actual NICs and live in a subnet. Their `parent` is the subnet's cell ID.

| Component | Subnet placement | Notes |
|---|---|---|
| Virtual Machine (VM) | App / workload subnet | Has NIC + optional public IP |
| VM Scale Set | App / workload subnet | One subnet for the whole set |
| Palo Alto NVA / F5 BIG-IP VE | Dedicated NVA subnet (often two: untrust/trust) | Deployed as VMs. Need NSGs, route tables, optional public IP on the untrust NIC |
| Application Gateway / WAF | **Dedicated** AppGW subnet | One AppGW per subnet; has frontend IP (public or private) |
| Azure Firewall | **AzureFirewallSubnet** (named exactly) | Managed firewall service, not a VM |
| Bastion | **AzureBastionSubnet** (named exactly) | For RDP/SSH jump access |
| VPN / ExpressRoute Gateway | **GatewaySubnet** (named exactly) | Hub-to-on-prem connectivity |
| Internal Load Balancer (Standard) | The subnet of the frontend IP | Frontend IP is what the subnet shows |
| Private Endpoint (PE) | **Dedicated PE subnet in the *consuming* VNet** | NEVER in the same subnet as the PaaS service it exposes. The PE *is* the consumer's view of the PaaS service. |
| NAT Gateway | Associated with a subnet (one or many) | Outbound SNAT, no NIC of its own — represented at the subnet boundary |
| AKS node pool | App / workload subnet | The control plane is managed (not a subnet resident); only the nodes live in a subnet |
| SQL Managed Instance | Dedicated MI subnet | One of the few PaaS services that's truly subnet-injected |

### PaaS / data services (NOT subnet-resident)

These run on Microsoft-managed infrastructure. Their network presence in *your* VNet is via a **Private Endpoint** in your subnet, OR via **VNet integration** for outbound calls. Draw the icon outside the VNet, and connect it to the consuming subnet via a PE.

| Component | How to draw it |
|---|---|
| App Service / Web App / Function App | Icon outside the VNet. To make it privately reachable: PE in a consuming subnet. To let it call into the VNet outbound: a separate VNet-integration subnet. **The App Service itself is never inside a subnet.** |
| Azure Container Apps | Same: outside the VNet. Can be deployed into an internal environment with subnet integration. |
| AKS control plane | Outside the VNet (managed). Nodes are subnet-resident; control plane is not. |
| API Management — External tier | Outside the VNet (public PaaS) |
| API Management — Internal tier | Subnet-injected (treat as subnet-resident) |
| SQL Database (PaaS) | Outside the VNet, accessed via PE |
| Cosmos DB | Outside the VNet, accessed via PE |
| Storage Account | Outside the VNet, accessed via PE per service (blob, file, table, queue have separate PEs) |
| Container Registry | Outside the VNet, accessed via PE |
| Key Vault | Outside the VNet, accessed via PE. Or via service endpoint for legacy designs. |
| Service Bus, Event Hub, Event Grid | Outside the VNet, accessed via PE |
| Redis Cache (Premium) | Outside the VNet, accessed via PE (or VNet-injected on older Premium) |
| PostgreSQL / MySQL Flexible Server | Either VNet-integrated (subnet-resident) OR PE-accessed depending on the deployment mode. Diagrams should reflect the chosen mode. |

### Control plane / global / regional (always outside VNets)

These are identity, observability, governance, and global routing services. They have no NIC and no subnet. Put them at canvas level or in a dedicated zone.

| Component | Where to draw |
|---|---|
| Front Door | Canvas level (global edge service) |
| Traffic Manager | Canvas level (global DNS-based routing) |
| CDN | Canvas level |
| Azure DNS (public) | Canvas level |
| **Private DNS zones** | Canvas level or a small "DNS" zone box. **NEVER inside a subnet.** They are linked to VNets via a "VNet Link" (drawn as a thin connector or annotation). |
| **Managed Identity** | Canvas level, anchored near Entra ID. Or as a small badge attached to its parent resource (Web App, VM). **NEVER inside a subnet** — it's an Entra ID object. |
| Microsoft Entra ID | Canvas level — usually top-right or bottom |
| Azure Monitor, Log Analytics, Application Insights | Dedicated Monitoring zone outside any VNet |
| Microsoft Sentinel | In the Monitoring or Security zone, outside VNets |
| Azure Policy, Defender for Cloud | Canvas level or a Governance zone |
| Recovery Services Vault | Canvas level |

---

## Component relationships — what must connect to what

A component shown on a diagram needs a reason to be there. Use this list to verify each icon is doing real work.

| If you draw... | It must connect to... | Otherwise it's decoration |
|---|---|---|
| **Managed Identity** | A target service the parent resource calls (Key Vault, SQL DB, Storage, ACR, etc.) — show the arrow from the parent resource to the target labelled with MI | Remove it. An MI with no consumer is just a label. |
| **Private Endpoint** | One specific PaaS service (the icon outside the VNet). The PE sits in a *different* subnet from any other PEs only by convention; multiple PEs can share a subnet. | Remove it or pair it with the actual PaaS resource. |
| **Private DNS zone** | At least one VNet (drawn as a "VNet link" connector) AND the PE whose hostname it resolves | Remove it. A DNS zone connected to nothing serves no purpose. |
| **Key Vault** | A consumer (App Service, VM, AKS) authenticating via MI or a service principal | Remove it. |
| **Container Registry** | A consumer (AKS, ACI, App Service) pulling images | Remove it. |
| **Application Gateway / WAF** | A backend pool (App Service, VMs, Internal LB) | A standalone AppGW with no backend is a no-op. |
| **NSG** | A subnet (preferred) or a NIC | Drop the NSG icon if you can't tie it to the subnet/NIC it protects. |
| **NAT Gateway** | The subnet(s) it provides outbound for | Drop it if no subnet is shown using it. |
| **Front Door** | An origin (App Service public hostname / Application Gateway public IP / NVA public IP / Storage / etc.) | An AFD with no origin shown is incomplete. |

---

## Common reference patterns for "Front Door + Web App in spoke"

Three valid patterns. Pick the one that matches the user's intent — don't blend them.

### Pattern A — AFD Premium with Private Link (no hub firewall on ingress)

```
Internet → Front Door Premium (WAF) → Private Link → PE of Web App in spoke
```

Components:
- Front Door **Premium** with WAF policy
- Private Link Service approval
- Web App with `publicNetworkAccess=Disabled`
- Private Endpoint of the Web App in `snet-pe` of the spoke
- Private DNS zone `privatelink.azurewebsites.net` linked to the spoke VNet
- (Optional) Key Vault, Storage, etc. accessed by the app via separate PEs

When to use: the user wants a **private origin** with global edge and built-in WAF. No hub NVAs on the ingress path.

### Pattern B — AFD → Hub WAF/Firewall (public origin)

```
Internet → Front Door → Public IP on Hub (App Gateway / NVA) → through firewall → spoke private IP (via VNet peering)
```

Components:
- Front Door (Standard or Premium)
- Hub VNet with NVA-untrust subnet (Palo Alto external NIC + public IP), NVA-trust subnet (internal NIC), AppGW subnet OR AzureFirewallSubnet
- F5 / Palo Alto / Azure Firewall as the inspecting layer in the hub
- Hub-to-spoke VNet peering with `allowGatewayTransit` if needed
- UDR in spoke pointing 0.0.0.0/0 to hub firewall private IP (forced tunneling)
- Web App reached either via its public hostname (with AFD-ID lockdown) or via PE in spoke
- Private DNS zone for `privatelink.azurewebsites.net` linked to **both** hub and spoke VNets if PE is used

When to use: the user has a centralized inspection mandate (hub NVAs are required for compliance) and accepts a longer hop count.

### Pattern C — AFD → Hub F5 with public VIP, NAT to spoke PE private IP (the team's pattern)

```
Internet → AFD (with custom domain) → A-record points to Hub F5 public VIP
       → F5 (with optional Palo Alto inline) inspects, terminates TLS or passes-through
       → F5 NATs/forwards to private IP of Web App's Private Endpoint in spoke (via hub-spoke peering)
       → PE in snet-pe in spoke → Web App
```

Components:
- Front Door (Premium or Standard) with custom domain
- DNS A-record (in public DNS) for the custom domain pointing to F5 public VIP — the AFD origin
- Hub VNet with F5 BIG-IP VE (NVA-untrust subnet for public-side NIC + public IP, NVA-trust subnet for internal NIC)
- F5 virtual server with VIP (public) → pool member = private IP of spoke PE
- Optional Palo Alto inline (in its own subnets, with UDRs forcing F5 traffic through it)
- Hub-to-spoke VNet peering
- Private Endpoint for the Web App in `snet-pe` of the spoke
- Web App with custom domain matching AFD's custom domain (host header passthrough)
- Private DNS zone `privatelink.azurewebsites.net` linked to the **hub** VNet (so F5 can resolve the PE FQDN to its private IP) — and to the spoke VNet for any internal callers

When to use: the user wants AFD's global reach **and** mandatory hub NVA inspection **and** a private origin in the spoke. The hub F5 acts as a public-fronting reverse proxy that bridges the AFD-public-origin model to a spoke-private-target model.

Subtle correctness points to draw:
- The Web App must accept the custom domain as a hostname (App Service custom domain binding)
- TLS cert lives either on F5 (passthrough mode) or on App Service (re-encrypt mode)
- The PE's private IP is what F5 targets — show this with a labelled arrow F5 → PE
- The Private DNS zone linked to the hub VNet is what makes the FQDN resolve to the PE's private IP from F5's vantage point

---

## Common misuses to avoid (with corrections)

| Misuse | Why it's wrong | Correction |
|---|---|---|
| Managed Identity inside a subnet | MI is an Entra ID object, not a network resource | Place it at canvas level or as a small attached badge on its parent resource |
| Private Endpoint in the same subnet as the PaaS service it exposes | The PE is the consumer's view of the service; they're not co-located | Put the PE in a dedicated PE subnet of the *consuming* VNet; show the PaaS service as an icon outside the VNet |
| Private DNS zone parented to a subnet | DNS zones are regional, not subnet-scoped | Place at canvas level; show "VNet Link" connectors to the VNets that use the zone |
| App Service / Web App icon inside a subnet | App Service is PaaS — runs on Microsoft's infrastructure | Place outside the VNet; connect via PE to the consuming subnet |
| Front Door inside any container | AFD is global | Always at canvas level |
| Azure Monitor / Log Analytics / Sentinel inside a VNet | Regional managed services | Dedicated Monitoring zone outside all VNets |
| Empty VNet container with services in a sibling generic box | The services belong inside the VNet, in proper subnets | Place service subnets inside the VNet container; remove the redundant outer box |
| NVAs (Palo Alto, F5) shown without subnets | Real NVAs are VMs in subnets with NSGs and route tables | Put them in named subnets (`snet-nva-untrust`, `snet-nva-trust`) inside the hub VNet; show the public IP on the untrust side |
| Multiple PEs without showing the Private DNS zone that resolves them | Without the zone link, name resolution to the PE doesn't work | Always show the DNS zone and a "VNet link" arrow when PEs are present |
| "Private Link" as a deployable icon in a subnet | Private Link is a service category, not a deployable resource | Use the Private Endpoint icon (which *is* deployable) and the Private Link Service icon (for AFD/AppGW publishing patterns), not a generic "Private Link" |
| `Inspect` / `Route` as connector labels | These describe what a box does, not the traffic on the wire | Label connectors with protocols/ports (HTTPS:443, TCP:1433) or hop intent (NAT, DNS lookup) |

---

## Quick reasoning checklist before you place each icon

For every icon you're about to add, answer:

1. **Plane**: subnet-resident, PaaS, or control-plane?
2. **Parent**: which container is it correctly placed in?
3. **Connection**: what does it talk to, and is that drawn?
4. **Reason for inclusion**: is it doing real work in this diagram, or is it decoration?

If you can't answer all four, drop the icon or reposition it. A diagram with 8 correct components beats one with 15 misplaced ones.
