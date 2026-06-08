"""Image 5 with NO coordinates — geometry is computed by layout_diagram().

Same diagram as image5.py, but every box's position/size is left to the engine;
only structure + per-container `layout` hints are authored. Invisible "band"
containers express the 2D arrangement (a top satellite row over the main flow
row; vertical MySQL+storage columns) that a single-axis flexbox can't do alone.
"""

from app.diagram_ir.schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    containers = [
        # Implicit-root child: stack a satellite band over the main flow band.
        Container(id="canvas", label="", style="band", layout="column",
                  children=["top_band", "main_band"]),
        Container(id="top_band", label="", style="band", layout="row", parent="canvas",
                  children=["sa", "dns"]),
        Container(id="main_band", label="", style="band", layout="row", parent="canvas",
                  children=["internet", "fd", "vnet", "data"]),

        Container(id="sa", label="Storage account", style="group", layout="row",
                  parent="top_band", children=["blob"], align_to="appsvc"),

        Container(id="vnet", label="10.0.0.0/16 (virtual network)", style="vnet", layout="row",
                  parent="main_band", children=["appsubnet", "pesubnet"],
                  adornments=[Adornment(icon="azure/virtual_networks", corner="top-left")]),
        Container(id="appsubnet", label="appsubnet  10.0.0.0/24", style="subnet", layout="row",
                  parent="vnet", children=["plan"]),
        Container(id="plan", label="App Service plan", style="group", layout="row",
                  parent="appsubnet", children=["appsvc"]),
        Container(id="pesubnet", label="pesubnet  10.0.1.0/24", style="subnet", layout="row",
                  parent="vnet", children=["pe"]),

        Container(id="data", label="", style="band", layout="row", parent="main_band",
                  children=["primary_col", "standby_col"]),
        Container(id="primary_col", label="", style="band", layout="column", parent="data",
                  children=["mysql_primary", "prem_primary"]),
        Container(id="standby_col", label="", style="band", layout="column", parent="data",
                  children=["mysql_standby", "prem_standby"]),
    ]

    nodes = [
        Node(id="internet", label="Internet", icon="shape/cloud", parent="main_band"),
        Node(id="fd", label="Azure Front Door + WAF", icon="azure/front_doors", parent="main_band"),
        Node(id="blob", label="Azure Blob Storage", icon="azure/blob", parent="sa"),
        Node(id="dns", label="Private DNS zone", icon="azure/dns_private_zones", parent="top_band", align_to="vnet"),
        Node(id="appsvc", label="Azure App Service", icon="azure/app_services", parent="plan"),
        Node(id="pe", label="Private endpoint", icon="azure/private_endpoint", parent="pesubnet"),
        Node(id="mysql_primary", label="MySQL flexible (primary)", icon="azure/mysql", parent="primary_col"),
        Node(id="prem_primary", label="Premium storage", icon="shape/cylinder", parent="primary_col"),
        Node(id="mysql_standby", label="MySQL flexible (standby)", icon="azure/mysql", parent="standby_col"),
        Node(id="prem_standby", label="Premium storage", icon="shape/cylinder", parent="standby_col"),
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

    return Diagram(title="App Service + MySQL flexible server (auto-layout)", direction="LR",
                   containers=containers, nodes=nodes, edges=edges)
