"""Canonical TB (top→bottom) tier stack — the other half of the flow-spine pair.

The mirror of flow_spine.py. Here the architecture is a classic **layered/n-tier**
stack, so it reads top → bottom: the outermost container runs *with* the TB
direction (`layout="column"`), and each stage is a **horizontal** (`row`) tier
that spreads left-and-right:

    edge/identity (head, top)
        ↓
    app tier
        ↓
    private data
        ↓
    monitoring (tail, bottom)

Choose TB over LR when the picture is tier-shaped — a few fat tiers each holding
several parallel resources (a wide row fits them better than a tall column), or
when convention puts the client/edge on top and data at the bottom. Choose LR
(see flow_spine.py) for a long, thin request pipeline that reads like a sentence.

Renders A=0 / C=0 (see tests/test_diagram_ir.py).
"""

from app.diagram_ir.schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    containers = [
        # Spine: outer column runs WITH the TB direction; children = tiers, top→bottom.
        Container(id="spine", label="", style="band", layout="column",
                  children=["ingress", "apptier", "datatier", "obs"]),
        # Head: edge/identity tier, on top. Each tier is a ROW (spreads left↔right).
        Container(id="ingress", label="Edge / Identity", style="group", layout="row",
                  parent="spine", children=["afd", "mi"]),
        Container(id="apptier", label="App tier", style="group", layout="row",
                  parent="spine", children=["appsvc", "apim", "redis"]),
        Container(id="datatier", label="Private data", style="group", layout="row",
                  parent="spine", children=["pe_kv", "kv", "pe_psql", "psql"]),
        # Tail: monitoring, at the bottom.
        Container(id="obs", label="Monitoring", style="monitoring", layout="row",
                  parent="spine", children=["appi", "law"]),
    ]

    nodes = [
        Node(id="afd", label="Front Door", icon="azure/front_doors", parent="ingress",
             adornments=[Adornment(icon="azure/web_application_firewall",
                                   corner="top-right", label="WAF")]),
        Node(id="mi", label="Managed Identity", icon="azure/managed_identities", parent="ingress"),
        Node(id="appsvc", label="App Service", icon="azure/app_services", parent="apptier"),
        Node(id="apim", label="API Management", icon="azure/api_management", parent="apptier"),
        Node(id="redis", label="Redis", icon="azure/redis", parent="apptier"),
        Node(id="pe_kv", label="pe-kv", icon="azure/private_endpoint", parent="datatier"),
        Node(id="kv", label="Key Vault", icon="azure/key_vaults", parent="datatier"),
        Node(id="pe_psql", label="pe-psql", icon="azure/private_endpoint", parent="datatier"),
        Node(id="psql", label="PostgreSQL", icon="azure/postgresql", parent="datatier"),
        Node(id="appi", label="App Insights", icon="azure/application_insights", parent="obs"),
        Node(id="law", label="Log Analytics", icon="azure/log_analytics", parent="obs"),
    ]

    edges = [
        Edge(source="afd", target="appsvc", type="flow", label="HTTPS"),
        Edge(source="appsvc", target="apim", type="flow", label="API"),
        Edge(source="appsvc", target="redis", type="private"),
        Edge(source="appsvc", target="pe_kv", type="private"),
        Edge(source="pe_kv", target="kv", type="private"),
        Edge(source="appsvc", target="pe_psql", type="private"),
        Edge(source="pe_psql", target="psql", type="private"),
        Edge(source="appsvc", target="mi", type="flow"),
        Edge(source="appsvc", target="appi", type="telemetry"),
        Edge(source="appi", target="law", type="telemetry"),
    ]

    return Diagram(title="TB tiered stack", direction="TB",
                   containers=containers, nodes=nodes, edges=edges)
