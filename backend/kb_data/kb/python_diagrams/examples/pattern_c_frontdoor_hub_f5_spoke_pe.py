"""
Pattern C: Azure Front Door + Hub F5 NAT (Public VIP) + Spoke App Gateway +
Private Endpoint -> Web App (PaaS).

Mirrors `kb/drawio/examples/pattern_c_frontdoor_hub_f5_nat_spoke_pe.drawio`
in Python form. Use this as a starting point for any "AFD + hub firewall/LB +
private spoke origin" architecture.

The Web App is intentionally OUTSIDE the Spoke VNet cluster - PaaS services
live on Microsoft's network. The Private Endpoint inside the spoke is what
makes the path private; the dashed link to the Web App represents the
Private Link service connection.
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import (
    FrontDoors,
    LoadBalancers,
    ApplicationGateway,
    PrivateEndpoint,
    PublicIpAddresses,
)
from diagrams.azure.web import AppServices
from diagrams.azure.monitor import LogAnalyticsWorkspaces, Monitor
from diagrams.onprem.client import Users

with Diagram(
    "Pattern C — AFD + Hub F5 NAT + Spoke PE",
    direction="LR",
    graph_attr={
        "fontsize": "16",
        "splines": "ortho",
        "nodesep": "0.6",
        "ranksep": "1.0",
        "pad": "0.4",
    },
):
    users = Users("Internet")
    fd = FrontDoors("Azure Front Door")

    with Cluster("Hub VNet"):
        with Cluster("Public Subnet"):
            hub_pip = PublicIpAddresses("Hub VIP")
            f5 = LoadBalancers("F5 NVA\n(Public VIP NAT)")

    with Cluster("Spoke VNet"):
        with Cluster("AppGW Subnet"):
            appgw_pip = PublicIpAddresses("AppGW PIP")
            appgw = ApplicationGateway("AppGW WAF v2")
        with Cluster("PE Subnet"):
            pe = PrivateEndpoint("Web App PE")

    webapp = AppServices("Web App (PaaS)")

    with Cluster("Monitoring"):
        law = LogAnalyticsWorkspaces("Log Analytics")
        mon = Monitor("Azure Monitor")

    users >> Edge(label="1 HTTPS") >> fd
    fd >> Edge(label="2") >> hub_pip
    hub_pip >> f5
    f5 >> Edge(label="3 NAT") >> appgw_pip
    appgw_pip >> appgw
    appgw >> Edge(label="4 backend") >> pe
    pe >> Edge(label="Private Link", style="dashed") >> webapp

    [appgw, webapp, f5] >> Edge(label="logs", style="dashed", color="gray") >> law
    law >> mon
