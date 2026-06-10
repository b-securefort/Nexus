"""Regression fixture: the dun_prod_traffic_flow render (2026-06-10).

This topology produced the canonical text-defect render: A=0/C=0 yet
unreadable — edge labels dropped at midpoints onto node captions and each
other ("likely main API path" over "Frontend Web App", the private-endpoint
caption pile-up), and dashed telemetry lines through container titles. Kept
as a pipeline fixture so the label-aware engine (B/D detectors + placer +
congestion routing) is measured against the real failure, not synthetic toys.

Labels are intentionally verbose/awkward — they reproduce what the authoring
model actually wrote. The skill now forbids hedging labels, but the engine
must stay readable even when the author ignores that.
"""

from ..schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    return Diagram(
        title="Dunamis Aviation Prod - User and Service Flow",
        direction="LR",
        containers=[
            Container(id="outer", label="Prod flow", style="band",
                      layout="column", children=["story", "network_band"]),
            Container(id="story", label="Application flow", style="band",
                      layout="row", parent="outer",
                      children=["users_edge", "frontend", "gateway", "backend", "data", "obs"]),
            Container(id="users_edge", label="Users & Edge", style="group",
                      layout="column", parent="story", children=["users", "afd"]),
            Container(id="frontend", label="Frontend", style="group",
                      layout="column", parent="story", children=["webapp"]),
            Container(id="gateway", label="Internal API Gateway", style="group",
                      layout="column", parent="story", children=["apim"]),
            Container(id="backend", label="Backend API", style="group",
                      layout="column", parent="story", children=["api"]),
            Container(id="data", label="Data & External Services", style="zone",
                      layout="column", parent="story",
                      children=["primary", "external", "ai"]),
            Container(id="primary", label="Primary dependencies", style="band",
                      layout="row", parent="data", children=["kv", "psql", "redis"]),
            Container(id="external", label="External / async", style="band",
                      layout="row", parent="data", children=["avinode"]),
            Container(id="ai", label="AI processing", style="band",
                      layout="row", parent="data", children=["aoai", "workflow"]),
            Container(id="obs", label="Monitoring", style="monitoring",
                      layout="column", parent="story", children=["appi", "law"]),
            Container(id="network_band", label="Private networking", style="band",
                      layout="row", parent="outer", children=["vnet"]),
            Container(id="vnet", label="Application VNet", style="vnet",
                      layout="row", parent="network_band",
                      children=["snet_apim", "snet_appint", "snet_pe"]),
            Container(id="snet_apim", label="APIM Subnet", style="subnet",
                      layout="row", parent="vnet", children=[],
                      adornments=[Adornment(icon="azure/network_security_groups",
                                            corner="top-right", label="NSG")]),
            Container(id="snet_appint", label="App Integration Subnet", style="subnet",
                      layout="row", parent="vnet", children=[]),
            Container(id="snet_pe", label="Private Endpoint Subnet", style="subnet",
                      layout="row", parent="vnet", children=["pe_kv", "pe_psql"],
                      adornments=[Adornment(icon="azure/network_security_groups",
                                            corner="top-right", label="NSG")]),
        ],
        nodes=[
            Node(id="users", label="Users", icon="shape/actor", parent="users_edge"),
            Node(id="afd", label="Front Door", icon="azure/front_doors",
                 parent="users_edge",
                 adornments=[Adornment(icon="azure/firewalls", corner="top-right",
                                       label="WAF")]),
            Node(id="webapp", label="Frontend Web App", icon="azure/app_services",
                 parent="frontend"),
            Node(id="apim", label="Internal APIM", icon="azure/api_management",
                 parent="gateway"),
            Node(id="api", label="Internal API App", icon="azure/app_services",
                 parent="backend"),
            Node(id="kv", label="Key Vault", icon="azure/key_vaults", parent="primary"),
            Node(id="psql", label="PostgreSQL", icon="azure/postgresql", parent="primary"),
            Node(id="redis", label="Redis Cache", icon="azure/cache_redis", parent="primary"),
            Node(id="avinode", label="Avinode API", icon="shape/cloud", parent="external"),
            Node(id="aoai", label="Azure OpenAI", icon="azure/openai", parent="ai"),
            Node(id="workflow", label="Workflow Apps", icon="azure/logic_apps", parent="ai"),
            Node(id="appi", label="Application Insights", icon="azure/application_insights",
                 parent="obs"),
            Node(id="law", label="Log Analytics", icon="azure/log_analytics_workspaces",
                 parent="obs"),
            Node(id="pe_kv", label="Private Endpoint Key Vault",
                 icon="azure/private_endpoint", parent="snet_pe"),
            Node(id="pe_psql", label="Private Endpoint PostgreSQL",
                 icon="azure/private_endpoint", parent="snet_pe"),
        ],
        edges=[
            Edge(source="users", target="afd", label="Users HTTPS"),
            Edge(source="afd", target="webapp", label="public entry"),
            Edge(source="webapp", target="apim", label="likely main API path"),
            Edge(source="apim", target="api", label="API backend"),
            Edge(source="api", target="kv", label="secret access"),
            Edge(source="api", target="psql", label="secret database access"),
            Edge(source="api", target="redis", label="daily cache use"),
            Edge(source="api", target="avinode", label="external API"),
            Edge(source="api", target="aoai", label="AI processing"),
            Edge(source="aoai", target="workflow"),
            Edge(source="apim", target="snet_apim", type="private", label="VNet integration"),
            Edge(source="api", target="snet_appint", type="private", label="VNet integration"),
            Edge(source="kv", target="pe_kv", type="private", label="private placement"),
            Edge(source="psql", target="pe_psql", type="private", label="private placement"),
            Edge(source="api", target="appi", type="telemetry"),
            Edge(source="kv", target="appi", type="telemetry"),
            Edge(source="psql", target="appi", type="telemetry"),
            Edge(source="appi", target="law", type="telemetry"),
        ],
    )
