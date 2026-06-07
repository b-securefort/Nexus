"""Image 5 (App Service + MySQL flexible server) as a hand-authored Diagram IR.

Render-first seed: geometry is hand-set so we can validate the IR's
expressiveness + the emitter's styling against the real Microsoft diagram BEFORE
building the layout engine. Reverse-engineered from the published reference:
- Internet -> Front Door+WAF -> App Service (in appsubnet, inside a plan group)
- App Service -> Private Endpoint (in pesubnet) -> MySQL flexible (primary)
- MySQL primary -> standby (replication); each -> Premium storage
- App Service -> Blob (static web content); Private DNS zone linked to the VNet
"""

from app.diagram_ir.schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    containers = [
        Container(id="vnet", label="10.0.0.0/16 (virtual network)", style="vnet",
                  x=250, y=180, w=420, h=250,
                  children=["appsubnet", "pesubnet"],
                  adornments=[Adornment(icon="azure/virtual_networks", corner="top-left")]),
        Container(id="appsubnet", label="appsubnet  10.0.0.0/24", style="subnet",
                  parent="vnet", x=270, y=242, w=180, h=158, children=["plan"]),
        Container(id="plan", label="App Service plan", style="group",
                  parent="appsubnet", x=285, y=282, w=150, h=104, children=["appsvc"]),
        Container(id="pesubnet", label="pesubnet  10.0.1.0/24", style="subnet",
                  parent="vnet", x=480, y=242, w=170, h=158, children=["pe"]),
        Container(id="sa", label="Storage account", style="group",
                  x=300, y=44, w=170, h=92, children=["blob"]),
    ]

    nodes = [
        Node(id="internet", label="Internet", icon="shape/cloud", x=20, y=300, w=72, h=50),
        Node(id="fd", label="Azure Front Door + WAF", icon="azure/front_doors", x=150, y=296, w=56, h=56),
        Node(id="blob", label="Azure Blob Storage", icon="azure/blob", parent="sa", x=362, y=70, w=48, h=48),
        Node(id="dns", label="Private DNS zone", icon="azure/dns_private_zones", x=620, y=56, w=48, h=48),
        Node(id="appsvc", label="Azure App Service", icon="azure/app_services", parent="plan", x=330, y=306, w=56, h=56),
        Node(id="pe", label="Private endpoint", icon="azure/private_endpoint", parent="pesubnet", x=542, y=306, w=56, h=56),
        Node(id="mysql_primary", label="MySQL flexible (primary)", icon="azure/mysql", x=740, y=300, w=56, h=56),
        Node(id="prem_primary", label="Premium storage", icon="shape/cylinder", x=745, y=402, w=46, h=50),
        Node(id="mysql_standby", label="MySQL flexible (standby)", icon="azure/mysql", x=920, y=300, w=56, h=56),
        Node(id="prem_standby", label="Premium storage", icon="shape/cylinder", x=925, y=402, w=46, h=50),
    ]

    edges = [
        Edge("internet", "fd", "flow"),
        Edge("fd", "appsvc", "flow"),
        Edge("appsvc", "blob", "private", "Static web content"),
        Edge("appsvc", "pe", "flow"),
        Edge("pe", "mysql_primary", "flow", "Private endpoint"),
        Edge("mysql_primary", "prem_primary", "flow"),
        Edge("mysql_standby", "prem_standby", "flow"),
        Edge("mysql_primary", "mysql_standby", "replication", "sync replication"),
        Edge("dns", "vnet", "dns", "Linked"),
    ]

    return Diagram(title="App Service + MySQL flexible server", direction="LR",
                   containers=containers, nodes=nodes, edges=edges)
