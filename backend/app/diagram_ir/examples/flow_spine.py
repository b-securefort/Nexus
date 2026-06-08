"""Canonical LR flow-spine: the Microsoft reference shape with a clear head and tail.

This is the template the diagram skills point authors at. The whole picture reads
left → right because the **outermost** container runs *with* `direction` (LR ⇒
`layout="row"`), and its children are the pipeline stages in flow order:

    ingress (head) → app tier → private data → monitoring (tail)

Each stage is a vertical (`column`) cluster, so related resources stack
top-and-down *within* the stage without breaking the left-to-right spine. Compare
the anti-pattern that produced "no head, no tail": an LR diagram whose outer
container is `layout="column"` holding `row` bands — that stacks the flow
*downward*, perpendicular to the reading direction, and just sprawls.

Renders A=0 / C=0 (see tests/test_diagram_ir.py).
"""

from app.diagram_ir.schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    containers = [
        # Spine: outer row runs WITH the LR direction. Children = stages, head→tail.
        Container(id="spine", label="", style="band", layout="row",
                  children=["ingress", "apptier", "datatier", "obs"]),
        # Head: the entry/identity stage, leftmost.
        Container(id="ingress", label="Edge / Identity", style="group", layout="column",
                  parent="spine", children=["afd", "mi"]),
        Container(id="apptier", label="App tier", style="group", layout="column",
                  parent="spine", children=["appsvc", "apim", "redis"]),
        # Private data: PEs in one column, their targets in the next (PE → target
        # reads left→right, never stacked in-band).
        Container(id="datatier", label="Private data", style="group", layout="row",
                  parent="spine", children=["pes", "targets"]),
        Container(id="pes", label="", style="band", layout="column", parent="datatier",
                  children=["pe_kv", "pe_psql"]),
        Container(id="targets", label="", style="band", layout="column", parent="datatier",
                  children=["kv", "psql"]),
        # Tail: monitoring, rightmost.
        Container(id="obs", label="Monitoring", style="monitoring", layout="column",
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
        Node(id="pe_kv", label="pe-kv", icon="azure/private_endpoint", parent="pes"),
        Node(id="pe_psql", label="pe-psql", icon="azure/private_endpoint", parent="pes"),
        Node(id="kv", label="Key Vault", icon="azure/key_vaults", parent="targets"),
        Node(id="psql", label="PostgreSQL", icon="azure/postgresql", parent="targets"),
        Node(id="appi", label="App Insights", icon="azure/application_insights", parent="obs"),
        Node(id="law", label="Log Analytics", icon="azure/log_analytics", parent="obs"),
    ]

    edges = [
        Edge(source="afd", target="appsvc", type="flow", label="HTTPS"),
        Edge(source="appsvc", target="apim", type="flow", label="API"),
        Edge(source="appsvc", target="redis", type="private", label="cache"),
        Edge(source="appsvc", target="pe_kv", type="private"),
        Edge(source="pe_kv", target="kv", type="private"),
        Edge(source="appsvc", target="pe_psql", type="private"),
        Edge(source="pe_psql", target="psql", type="private"),
        Edge(source="appsvc", target="mi", type="flow", label="MI"),
        Edge(source="appsvc", target="appi", type="telemetry", label="logs"),
        Edge(source="appi", target="law", type="telemetry"),
    ]

    return Diagram(title="LR flow spine", direction="LR",
                   containers=containers, nodes=nodes, edges=edges)
