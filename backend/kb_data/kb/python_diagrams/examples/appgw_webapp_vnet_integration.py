"""
Application Gateway (WAF v2) -> App Service with VNet integration.

This is the diagram that motivated the spike. Key shape:
  - App Gateway WAF v2 inside the AppGW subnet of a hub VNet.
  - App Service IS NOT inside the VNet cluster. It is a PaaS resource on
    Microsoft's network. VNet integration is represented by a dashed edge
    from the Web App to the integration subnet (which carries the app's
    outbound traffic into the VNet, not the inbound request path).
  - No private endpoint - the user explicitly said they didn't want one.

The "App Gateway -> Web App" arrow is the real request path; the
"Web App -> integration subnet" dashed arrow is the outbound VNet
integration.
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.azure.network import (
    ApplicationGateway,
    PublicIpAddresses,
    Subnets,
)
from diagrams.azure.web import AppServices
from diagrams.azure.monitor import LogAnalyticsWorkspaces, Monitor
from diagrams.onprem.client import Users

with Diagram(
    "AppGW WAF v2 -> App Service (VNet integration)",
    direction="LR",
    graph_attr={
        "fontsize": "16",
        "splines": "ortho",
        "nodesep": "0.5",
        "ranksep": "0.9",
        "pad": "0.4",
    },
):
    users = Users("Internet")

    with Cluster("Hub VNet"):
        with Cluster("App Gateway Subnet"):
            pip = PublicIpAddresses("AppGW PIP")
            appgw = ApplicationGateway("AppGW WAF v2")
        with Cluster("App Service Integration Subnet"):
            integration = Subnets("vnet-integration")

    # PaaS resource - intentionally outside any VNet cluster
    webapp = AppServices("Web App")

    with Cluster("Monitoring"):
        law = LogAnalyticsWorkspaces("Log Analytics")
        mon = Monitor("Azure Monitor")

    users >> Edge(label="1 HTTPS") >> pip >> appgw
    appgw >> Edge(label="2 backend") >> webapp
    webapp >> Edge(label="3 outbound", style="dashed") >> integration

    [appgw, webapp] >> Edge(label="logs", style="dashed", color="gray") >> law
    law >> mon
