# Python Diagrams — quick reference

The `generate_python_diagram` tool renders architecture diagrams from Python
code using the `diagrams` library (mingrammer/diagrams). It is an
alternative to the draw.io toolchain — you declare the graph shape
(containers, nodes, edges) and Graphviz lays it out automatically. No
manual pixel coordinates, no validator round-trips for overlap or padding.

## When to use this vs draw.io

| Use Python diagrams | Use draw.io |
|---|---|
| Quick architecture sketch | Diagram needs interactive post-edit in diagrams.net |
| Multi-iteration request (user keeps changing it) | Final polished output for a slide deck |
| Cluster/container nesting > 2 deep | Specific Microsoft-reference visual styling required |
| Edge routing is annoying | Diagram needs custom callouts, badges, annotations |

Default to Python diagrams for the *first* version. Switch to draw.io only if
the user explicitly needs an editable .drawio file or the result needs Microsoft-
reference polish.

## Core syntax

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import ApplicationGateway, PublicIpAddresses
from diagrams.azure.web import AppServices
from diagrams.onprem.client import Users

with Diagram("My Architecture", direction="LR"):
    user = Users("Internet")
    with Cluster("Hub VNet"):
        with Cluster("AppGW Subnet"):
            pip = PublicIpAddresses("PIP")
            appgw = ApplicationGateway("WAF v2")
    webapp = AppServices("Web App")

    user >> pip >> appgw >> webapp
```

You don't write `show=`, `filename=`, or `outformat=` on the `Diagram(...)`
call — the tool injects them. Everything else (title, direction, graph
attributes) you control.

## Edge styles

```python
# Plain edge with arrow
a >> b

# Reverse direction
a << b

# Bidirectional
a - b   # use Edge(forward=True, reverse=True) for explicit two-arrow

# Labelled edge
a >> Edge(label="HTTPS") >> b

# Dashed edge (telemetry, integration, virtual link)
a >> Edge(style="dashed", color="gray", label="logs") >> b

# Fan-in (multiple sources to one target)
[a, b, c] >> Edge(label="emit") >> sink

# Fan-out (one source to many)
source >> Edge(label="broadcast") >> [a, b, c]
```

## Graph-level attributes

Pass `graph_attr={...}` to `Diagram()` to tune layout:

```python
with Diagram(
    "Title",
    direction="LR",                            # LR, RL, TB, BT
    graph_attr={
        "fontsize": "16",
        "splines": "ortho",   # orthogonal edges (recommended for arch diagrams)
        "nodesep": "0.6",     # space between sibling nodes
        "ranksep": "1.0",     # space between layers
    },
):
    ...
```

## Common Azure imports

```python
# Networking
from diagrams.azure.network import (
    ApplicationGateway, Firewall, FrontDoors, LoadBalancers,
    NetworkSecurityGroupsClassic, PrivateEndpoint, PublicIpAddresses,
    Subnets, VirtualNetworks, VirtualNetworkGateways, ExpressrouteCircuits,
    DNSZones, DNSPrivateZones,
)

# Compute / Web
from diagrams.azure.web import AppServices, APIManagement, AppServicePlans
from diagrams.azure.compute import (
    AKS, ContainerInstances, ContainerApps, FunctionApps,
    VM, VMScaleSet, ACR,
)

# Database / Storage
from diagrams.azure.database import (
    CosmosDb, SQLDatabases, SQLManagedInstances, CacheForRedis,
)
from diagrams.azure.storage import StorageAccounts, BlobStorage

# Identity / Security
from diagrams.azure.security import KeyVaults, Sentinel
from diagrams.azure.identity import (
    ActiveDirectory, ManagedIdentities, EntraConnect,
)

# Monitor
from diagrams.azure.monitor import Monitor, LogAnalyticsWorkspaces, ApplicationInsights

# Integration
from diagrams.azure.integration import ServiceBus, EventGridTopics, LogicApps

# Users / generic
from diagrams.onprem.client import Users, Client
from diagrams.azure.general import Resource
```

## Architectural rules — same as for draw.io

The library doesn't enforce these — you have to put nodes in the right
clusters yourself.

1. **PaaS services are NOT inside a VNet cluster.** App Service, Cosmos DB,
   Key Vault, Storage, etc. live at the top level (outside `with Cluster("VNet"):`).
   If you need to show private access, add a `PrivateEndpoint` inside the
   consuming subnet and connect it with a dashed edge to the PaaS node.
2. **Identity and DNS are top-level too.** Managed Identity, Entra ID,
   Private DNS Zones — never inside a subnet cluster.
3. **Monitoring is its own cluster, outside the VNet.** Log Analytics,
   Application Insights, Monitor — put them in `with Cluster("Monitoring"):`
   beside the VNet cluster, not inside it.
4. **Resources inside a subnet go inside that subnet's cluster.** VMs, AKS
   nodes, AppGW, Private Endpoints, NICs.
5. **Numbered flow labels go on edges.** Use `Edge(label="1 HTTPS")` rather
   than free-floating annotation nodes — the library has no concept of badges.

## Examples in this directory

- `examples/pattern_c_frontdoor_hub_f5_spoke_pe.py` — AFD → Hub F5 NVA →
  Spoke AppGW → PE → Web App (Pattern C from the draw.io KB).
- `examples/appgw_webapp_vnet_integration.py` — App Gateway WAF v2 in front
  of an App Service that uses VNet integration. Shows the "PaaS outside the
  VNet" rule explicitly.

When a user request matches one of these, read the example and adapt rather
than writing from scratch.
