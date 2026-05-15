"""
Convert a mingrammer `diagrams` script into a `.drawio` XML file.

The pipeline:

  1. **Capture** — the user's Python script runs in a subprocess. We monkey-
     patch `Diagram.render` to do nothing (skip PNG output) and instead dump
     `self.dot.source` to a known path. We also expose a header-defined
     `AzureGeneric` class for services mingrammer doesn't ship — it renders
     as a plain rounded box in Graphviz, but carries an `azure_icon` hint
     used by the drawio emitter to upgrade to the real Azure2 SVG.

  2. **Layout** — we run `dot -Tjson` against the captured DOT source. This
     gives us absolute coordinates (Graphviz space, points) for every node
     and bounding boxes for every cluster. Edges carry tail/head/label.

  3. **Emit** — we translate Graphviz coordinates to drawio space (top-left
     origin, pixels) and emit one `<mxCell>` per cluster, node, and edge.
     Each node gets the Azure2 icon style if its mingrammer image path maps
     to a known SVG, otherwise it falls back to a labelled rectangle.

The emitter intentionally does NOT solve everything draw.io can — it gives
you a structurally correct, validator-clean diagram as a starting point.
Numbered flow badges are extracted from edges whose label starts with a
digit (e.g. `"1 HTTPS"`) and dropped near the edge midpoint as separate
ellipse cells.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.sax.saxutils as sx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# --- Icon mapping --------------------------------------------------------
#
# Maps the tail of mingrammer's `image=` attribute (which is an absolute
# path like `.../diagrams/resources/azure/network/application-gateway.png`)
# to the equivalent draw.io Azure2 stencil path. Built by hand from the
# common-case table in the drawio-diagrammer SKILL. Unmapped icons fall
# back to a labelled rectangle in the emitter.
#
# When a user uses our `AzureGeneric` helper, they pass an `azure_icon`
# string like `"bastions"` or `"key_vaults"`; that gets looked up in the
# `_KIND_TO_SVG` table below — a smaller, alias-style map keyed by kind.

_MINGRAMMER_TO_AZURE2: dict[str, str] = {
    # network
    "azure/network/application-gateway.png": "img/lib/azure2/networking/Application_Gateways.svg",
    "azure/network/front-doors.png":         "img/lib/azure2/networking/Front_Doors.svg",
    "azure/network/load-balancers.png":      "img/lib/azure2/networking/Load_Balancers.svg",
    "azure/network/firewall.png":            "img/lib/azure2/networking/Firewalls.svg",
    "azure/network/public-ip-addresses.png": "img/lib/azure2/networking/Public_IP_Addresses.svg",
    "azure/network/private-endpoint.png":    "img/lib/azure2/networking/Private_Endpoint.svg",
    "azure/network/virtual-networks.png":    "img/lib/azure2/networking/Virtual_Networks.svg",
    "azure/network/subnets.png":             "img/lib/azure2/networking/Subnet.svg",
    "azure/network/dns-zones.png":           "img/lib/azure2/networking/DNS_Zones.svg",
    "azure/network/dns-private-zones.png":   "img/lib/azure2/networking/DNS_Private_Zones.svg",
    "azure/network/nat-gateway.png":         "img/lib/azure2/networking/NAT_Gateway.svg",
    "azure/network/expressroute-circuits.png": "img/lib/azure2/networking/ExpressRoute_Circuits.svg",
    "azure/network/virtual-network-gateways.png": "img/lib/azure2/networking/Virtual_Network_Gateways.svg",
    "azure/network/network-security-groups-classic.png": "img/lib/azure2/networking/Network_Security_Groups.svg",
    "azure/network/cdn-profiles.png":        "img/lib/azure2/networking/CDN_Profiles.svg",
    "azure/network/traffic-manager-profiles.png": "img/lib/azure2/networking/Traffic_Manager_Profiles.svg",
    "azure/network/network-watcher.png":     "img/lib/azure2/networking/Network_Watcher.svg",
    "azure/network/route-tables.png":        "img/lib/azure2/networking/Route_Tables.svg",
    "azure/network/network-interfaces.png":  "img/lib/azure2/networking/Network_Interfaces.svg",
    # web / app services
    "azure/web/app-services.png":            "img/lib/azure2/app_services/App_Services.svg",
    "azure/web/api-management-services.png": "img/lib/azure2/integration/API_Management_Services.svg",
    "azure/web/app-service-plans.png":       "img/lib/azure2/app_services/App_Service_Plans.svg",
    "azure/web/app-service-environments.png": "img/lib/azure2/app_services/App_Service_Environments.svg",
    "azure/web/notification-hub-namespaces.png": "img/lib/azure2/integration/Notification_Hubs.svg",
    "azure/web/signalr.png":                 "img/lib/azure2/web/SignalR.svg",
    # compute.AppServices — mingrammer also re-exports App Services under
    # azure.compute (with a different image-path tail), so we need both.
    "azure/compute/app-services.png":        "img/lib/azure2/app_services/App_Services.svg",
    # appservices subdir — another mingrammer module name variant
    "azure/appservices/app-services.png":    "img/lib/azure2/app_services/App_Services.svg",
    # compute
    "azure/compute/vm.png":                  "img/lib/azure2/compute/Virtual_Machine.svg",
    "azure/compute/virtual-machine.png":     "img/lib/azure2/compute/Virtual_Machine.svg",
    "azure/compute/vm-scale-set.png":        "img/lib/azure2/compute/VM_Scale_Sets.svg",
    "azure/compute/vm-scale-sets.png":       "img/lib/azure2/compute/VM_Scale_Sets.svg",
    "azure/compute/kubernetes-services.png": "img/lib/azure2/compute/Kubernetes_Services.svg",
    "azure/compute/function-apps.png":       "img/lib/azure2/compute/Function_Apps.svg",
    "azure/compute/container-instances.png": "img/lib/azure2/compute/Container_Instances.svg",
    "azure/compute/container-registries.png": "img/lib/azure2/containers/Container_Registries.svg",
    "azure/compute/acr.png":                 "img/lib/azure2/containers/Container_Registries.svg",
    "azure/compute/aks.png":                 "img/lib/azure2/compute/Kubernetes_Services.svg",
    "azure/compute/container-apps.png":      "img/lib/azure2/containers/Container_Instances.svg",
    "azure/compute/availability-sets.png":   "img/lib/azure2/compute/Availability_Sets.svg",
    "azure/compute/batch-accounts.png":      "img/lib/azure2/compute/Batch_Accounts.svg",
    "azure/compute/service-fabric-clusters.png": "img/lib/azure2/compute/Service_Fabric_Clusters.svg",
    "azure/compute/disks.png":               "img/lib/azure2/compute/Managed_Disks.svg",
    "azure/compute/sap-hana-on-azure.png":   "img/lib/azure2/compute/SAP_Azure.svg",
    # database (legacy singular module)
    "azure/database/sql-databases.png":      "img/lib/azure2/databases/SQL_Database.svg",
    "azure/database/sql-managed-instances.png": "img/lib/azure2/databases/SQL_Managed_Instance.svg",
    "azure/database/cosmos-db.png":          "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "azure/database/cache-for-redis.png":    "img/lib/azure2/databases/Cache_Redis.svg",
    "azure/database/database-for-postgresql-servers.png": "img/lib/azure2/databases/Azure_Database_PostgreSQL_Server.svg",
    "azure/database/database-for-mysql-servers.png": "img/lib/azure2/databases/Azure_Database_MySQL_Server.svg",
    "azure/database/database-for-mariadb-servers.png": "img/lib/azure2/databases/Azure_Database_MariaDB_Server.svg",
    "azure/database/data-factory.png":       "img/lib/azure2/databases/Data_Factory.svg",
    "azure/database/data-lake.png":          "img/lib/azure2/storage/Data_Lake_Storage.svg",
    "azure/database/synapse-analytics.png":  "img/lib/azure2/databases/Azure_Synapse_Analytics.svg",
    "azure/database/sql-datawarehouse.png":  "img/lib/azure2/databases/SQL_Data_Warehouses.svg",
    "azure/database/sql-vm.png":             "img/lib/azure2/databases/SQL_Server_on_Virtual_Machines.svg",
    # databases (newer plural module — different filenames)
    "azure/databases/sql-database.png":      "img/lib/azure2/databases/SQL_Database.svg",
    "azure/databases/sql-managed-instance.png": "img/lib/azure2/databases/SQL_Managed_Instance.svg",
    "azure/databases/azure-cosmos-db.png":   "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "azure/databases/cache-redis.png":       "img/lib/azure2/databases/Cache_Redis.svg",
    "azure/databases/azure-database-postgresql-server.png": "img/lib/azure2/databases/Azure_Database_PostgreSQL_Server.svg",
    "azure/databases/azure-database-mysql-server.png": "img/lib/azure2/databases/Azure_Database_MySQL_Server.svg",
    "azure/databases/azure-synapse-analytics.png": "img/lib/azure2/databases/Azure_Synapse_Analytics.svg",
    "azure/databases/data-factories.png":    "img/lib/azure2/databases/Data_Factory.svg",
    # storage
    "azure/storage/storage-accounts.png":    "img/lib/azure2/storage/Storage_Accounts.svg",
    "azure/storage/blob-storage.png":        "img/lib/azure2/storage/Storage_Accounts.svg",
    "azure/storage/azure-netapp-files.png":  "img/lib/azure2/storage/Azure_NetApp_Files.svg",
    "azure/storage/data-lake-storage.png":   "img/lib/azure2/storage/Data_Lake_Storage.svg",
    "azure/storage/data-share-invitations.png": "img/lib/azure2/storage/Data_Share_Invitations.svg",
    # security
    "azure/security/key-vaults.png":         "img/lib/azure2/security/Key_Vaults.svg",
    "azure/security/sentinel.png":           "img/lib/azure2/security/Azure_Sentinel.svg",
    "azure/security/defender.png":           "img/lib/azure2/security/Azure_Defender.svg",
    "azure/security/security-center.png":    "img/lib/azure2/security/Security_Center.svg",
    "azure/security/multifactor-authentication.png": "img/lib/azure2/security/Multifactor_Authentication.svg",
    # identity
    "azure/identity/managed-identities.png": "img/lib/azure2/identity/Managed_Identities.svg",
    "azure/identity/active-directory.png":   "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "azure/identity/azure-active-directory.png": "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "azure/identity/enterprise-applications.png": "img/lib/azure2/identity/Enterprise_Applications.svg",
    "azure/identity/groups.png":             "img/lib/azure2/identity/Groups.svg",
    "azure/identity/users.png":              "img/lib/azure2/identity/Users.svg",
    "azure/identity/entra-managed-identities.png": "img/lib/azure2/identity/Managed_Identities.svg",
    "azure/identity/ad-b2c.png":             "img/lib/azure2/identity/Azure_AD_B2C.svg",
    "azure/identity/conditional-access.png": "img/lib/azure2/identity/Conditional_Access.svg",
    # monitor / management
    "azure/monitor/monitor.png":             "img/lib/azure2/management_governance/Monitor.svg",
    "azure/monitor/log-analytics-workspaces.png": "img/lib/azure2/management_governance/Log_Analytics_Workspaces.svg",
    "azure/monitor/application-insights.png":     "img/lib/azure2/devops/Application_Insights.svg",
    "azure/monitor/metrics.png":             "img/lib/azure2/management_governance/Metrics.svg",
    "azure/monitor/network-watcher.png":     "img/lib/azure2/networking/Network_Watcher.svg",
    "azure/managementgovernance/policy.png": "img/lib/azure2/management_governance/Policy.svg",
    "azure/managementgovernance/azure-arc.png": "img/lib/azure2/hybrid_multicloud/Azure_Arc.svg",
    "azure/managementgovernance/blueprints.png": "img/lib/azure2/management_governance/Blueprints.svg",
    "azure/managementgovernance/recovery-services-vaults.png": "img/lib/azure2/management_governance/Recovery_Services_Vaults.svg",
    "azure/managementgovernance/cost-management-and-billing.png": "img/lib/azure2/management_governance/Cost_Management_and_Billing.svg",
    "azure/managementgovernance/automation-accounts.png": "img/lib/azure2/management_governance/Automation_Accounts.svg",
    # integration
    "azure/integration/service-bus.png":     "img/lib/azure2/integration/Service_Bus.svg",
    "azure/integration/azure-service-bus.png": "img/lib/azure2/integration/Service_Bus.svg",
    "azure/integration/event-grid-topics.png": "img/lib/azure2/integration/Event_Grid_Topics.svg",
    "azure/integration/event-grid-subscriptions.png": "img/lib/azure2/integration/Event_Grid_Subscriptions.svg",
    "azure/integration/event-grid-domains.png": "img/lib/azure2/integration/Event_Grid_Domains.svg",
    "azure/integration/logic-apps.png":      "img/lib/azure2/integration/Logic_Apps.svg",
    "azure/integration/api-management.png":  "img/lib/azure2/integration/API_Management_Services.svg",
    "azure/integration/api-management-services.png": "img/lib/azure2/integration/API_Management_Services.svg",
    "azure/integration/data-factories.png":  "img/lib/azure2/databases/Data_Factory.svg",
    "azure/integration/app-configuration.png": "img/lib/azure2/integration/App_Configurations.svg",
    # analytics / iot
    "azure/analytics/event-hubs.png":        "img/lib/azure2/analytics/Event_Hubs.svg",
    "azure/analytics/stream-analytics-jobs.png": "img/lib/azure2/analytics/Stream_Analytics_Jobs.svg",
    "azure/analytics/data-factories.png":    "img/lib/azure2/databases/Data_Factory.svg",
    "azure/iot/iot-hub.png":                 "img/lib/azure2/iot/IoT_Hub.svg",
    "azure/iot/iot-central-applications.png": "img/lib/azure2/iot/IoT_Central_Applications.svg",
    # AI / ML
    "azure/aimachinelearning/azure-openai.png": "img/lib/azure2/ai_machine_learning/Azure_OpenAI.svg",
    "azure/aimachinelearning/cognitive-services.png": "img/lib/azure2/ai_machine_learning/Cognitive_Services.svg",
    "azure/aimachinelearning/machine-learning.png": "img/lib/azure2/ai_machine_learning/Machine_Learning_Studio_Workspaces.svg",
    "azure/aimachinelearning/cognitive-search.png": "img/lib/azure2/ai_machine_learning/Cognitive_Search.svg",
    "azure/aimachinelearning/bot-services.png": "img/lib/azure2/ai_machine_learning/Bot_Services.svg",
    "azure/aimachinelearning/ai-studio.png": "img/lib/azure2/ai_machine_learning/AI_Studio.svg",
    # devops
    "azure/devops/devops.png":               "img/lib/azure2/devops/Azure_DevOps.svg",
    "azure/devops/pipelines.png":            "img/lib/azure2/devops/Pipelines.svg",
    "azure/devops/repos.png":                "img/lib/azure2/devops/Repos.svg",
    "azure/devops/artifacts.png":            "img/lib/azure2/devops/Artifacts.svg",
    "azure/devops/boards.png":               "img/lib/azure2/devops/Boards.svg",
    "azure/devops/test-plans.png":           "img/lib/azure2/devops/Test_Plans.svg",
    # general / onprem
    "azure/general/resource.png":            "img/lib/azure2/general/Resource.svg",
    "azure/general/subscriptions.png":       "img/lib/azure2/general/Subscriptions.svg",
    "azure/general/resourcegroups.png":      "img/lib/azure2/general/Resource_Groups.svg",
    "azure/general/tag.png":                 "img/lib/azure2/general/Tag.svg",
    "onprem/client/users.png":               "img/lib/azure2/general/Globe.svg",
    "onprem/client/user.png":                "img/lib/azure2/general/Globe.svg",
    "onprem/client/client.png":              "img/lib/azure2/general/Globe.svg",
}

# Aliases for AzureGeneric(..., azure_icon="bastions"). Anything not in the
# mingrammer catalog that the LLM wants to draw goes through here. Keys are
# lowercase; both singular and plural forms are accepted where applicable.
_KIND_TO_SVG: dict[str, str] = {
    # network
    "bastion":         "img/lib/azure2/networking/Bastions.svg",
    "bastions":        "img/lib/azure2/networking/Bastions.svg",
    "waf_policy":      "img/lib/azure2/networking/Web_Application_Firewall_Policies_WAF.svg",
    "waf_policies":    "img/lib/azure2/networking/Web_Application_Firewall_Policies_WAF.svg",
    "private_link":    "img/lib/azure2/networking/Private_Link.svg",
    "private_endpoint":"img/lib/azure2/networking/Private_Endpoint.svg",
    "private_endpoints":"img/lib/azure2/networking/Private_Endpoint.svg",
    "vnet_peering":    "img/lib/azure2/networking/Virtual_Networks.svg",
    "vnet":            "img/lib/azure2/networking/Virtual_Networks.svg",
    "subnet":          "img/lib/azure2/networking/Subnet.svg",
    "subnets":         "img/lib/azure2/networking/Subnet.svg",
    "nsg":             "img/lib/azure2/networking/Network_Security_Groups.svg",
    "public_ip":       "img/lib/azure2/networking/Public_IP_Addresses.svg",
    "public_ip_address":"img/lib/azure2/networking/Public_IP_Addresses.svg",
    "public_ip_addresses":"img/lib/azure2/networking/Public_IP_Addresses.svg",
    "ddos":            "img/lib/azure2/networking/DDoS_Protection_Plans.svg",
    "service_endpoint":"img/lib/azure2/networking/Service_Endpoint_Policies.svg",
    "route_server":    "img/lib/azure2/networking/Route_Tables.svg",
    "route_table":     "img/lib/azure2/networking/Route_Tables.svg",
    "front_door":      "img/lib/azure2/networking/Front_Doors.svg",
    "app_gateway":     "img/lib/azure2/networking/Application_Gateways.svg",
    "application_gateway":"img/lib/azure2/networking/Application_Gateways.svg",
    "firewall":        "img/lib/azure2/networking/Firewalls.svg",
    "load_balancer":   "img/lib/azure2/networking/Load_Balancers.svg",
    "nat_gateway":     "img/lib/azure2/networking/NAT_Gateway.svg",
    "expressroute":    "img/lib/azure2/networking/ExpressRoute_Circuits.svg",
    "vpn_gateway":     "img/lib/azure2/networking/Virtual_Network_Gateways.svg",
    "dns_zone":        "img/lib/azure2/networking/DNS_Zones.svg",
    "private_dns_zone":"img/lib/azure2/networking/DNS_Private_Zones.svg",
    # web / app services (the icons the agent kept failing to render correctly)
    "app_service":     "img/lib/azure2/app_services/App_Services.svg",
    "app_services":    "img/lib/azure2/app_services/App_Services.svg",
    "web_app":         "img/lib/azure2/app_services/App_Services.svg",
    "function_app":    "img/lib/azure2/compute/Function_Apps.svg",
    "function":        "img/lib/azure2/compute/Function_Apps.svg",
    # compute
    "vm":              "img/lib/azure2/compute/Virtual_Machine.svg",
    "virtual_machine": "img/lib/azure2/compute/Virtual_Machine.svg",
    "vmss":            "img/lib/azure2/compute/VM_Scale_Sets.svg",
    "aks":             "img/lib/azure2/compute/Kubernetes_Services.svg",
    "kubernetes":      "img/lib/azure2/compute/Kubernetes_Services.svg",
    "acr":             "img/lib/azure2/containers/Container_Registries.svg",
    "container_registry":"img/lib/azure2/containers/Container_Registries.svg",
    "container_instance":"img/lib/azure2/compute/Container_Instances.svg",
    "container_app":   "img/lib/azure2/containers/Container_Instances.svg",
    "container_apps":  "img/lib/azure2/containers/Container_Instances.svg",
    # databases / storage
    "sql_db":          "img/lib/azure2/databases/SQL_Database.svg",
    "sql_database":    "img/lib/azure2/databases/SQL_Database.svg",
    "sql_mi":          "img/lib/azure2/databases/SQL_Managed_Instance.svg",
    "cosmos_db":       "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "cosmos":          "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "redis":           "img/lib/azure2/databases/Cache_Redis.svg",
    "postgresql":      "img/lib/azure2/databases/Azure_Database_PostgreSQL_Server.svg",
    "mysql":           "img/lib/azure2/databases/Azure_Database_MySQL_Server.svg",
    "storage":         "img/lib/azure2/storage/Storage_Accounts.svg",
    "storage_account": "img/lib/azure2/storage/Storage_Accounts.svg",
    "blob_storage":    "img/lib/azure2/storage/Storage_Accounts.svg",
    # identity / security
    "entra_id":        "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "entra":           "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "managed_identity":"img/lib/azure2/identity/Managed_Identities.svg",
    "conditional_access": "img/lib/azure2/identity/Conditional_Access.svg",
    "key_vault":       "img/lib/azure2/security/Key_Vaults.svg",
    "defender":        "img/lib/azure2/security/Azure_Defender.svg",
    "sentinel":        "img/lib/azure2/security/Azure_Sentinel.svg",
    # governance
    "policy":          "img/lib/azure2/management_governance/Policy.svg",
    "blueprint":       "img/lib/azure2/management_governance/Blueprints.svg",
    "lighthouse":      "img/lib/azure2/management_governance/Azure_Lighthouse.svg",
    "arc":             "img/lib/azure2/hybrid_multicloud/Azure_Arc.svg",
    "automation":      "img/lib/azure2/management_governance/Automation_Accounts.svg",
    "recovery_vault":  "img/lib/azure2/management_governance/Recovery_Services_Vaults.svg",
    # AI / data
    "openai":          "img/lib/azure2/ai_machine_learning/Azure_OpenAI.svg",
    "cognitive":       "img/lib/azure2/ai_machine_learning/Cognitive_Services.svg",
    "ml":              "img/lib/azure2/ai_machine_learning/Machine_Learning_Studio_Workspaces.svg",
    "ai_search":       "img/lib/azure2/ai_machine_learning/Cognitive_Search.svg",
    # devops
    "devops":          "img/lib/azure2/devops/Azure_DevOps.svg",
    "azure_devops":    "img/lib/azure2/devops/Azure_DevOps.svg",
    # monitor (so AzureGeneric(azure_icon="application_insights") works too)
    "monitor":         "img/lib/azure2/management_governance/Monitor.svg",
    "log_analytics":   "img/lib/azure2/management_governance/Log_Analytics_Workspaces.svg",
    "application_insights":"img/lib/azure2/devops/Application_Insights.svg",
    "app_insights":    "img/lib/azure2/devops/Application_Insights.svg",
    # web/integration
    "apim":            "img/lib/azure2/integration/API_Management_Services.svg",
    "logic_app":       "img/lib/azure2/integration/Logic_Apps.svg",
    "service_bus":     "img/lib/azure2/integration/Service_Bus.svg",
    "event_grid":      "img/lib/azure2/integration/Event_Grid_Topics.svg",
    "event_hub":       "img/lib/azure2/analytics/Event_Hubs.svg",
    "app_config":      "img/lib/azure2/integration/App_Configurations.svg",
    # general
    "subscription":    "img/lib/azure2/general/Subscriptions.svg",
    "resource_group":  "img/lib/azure2/general/Resource_Groups.svg",
    "globe":           "img/lib/azure2/general/Globe.svg",
}


def map_icon(image_path: str | None, azure_icon: str | None) -> str | None:
    """Given a mingrammer image path and/or an azure_icon hint, return the
    drawio Azure2 SVG path, or None if neither resolves."""
    if azure_icon:
        v = _KIND_TO_SVG.get(azure_icon.lower())
        if v:
            return v
    if image_path:
        norm = image_path.replace("\\", "/")
        # mingrammer path looks like ".../diagrams/resources/azure/network/foo.png"
        m = re.search(r"resources/([^/]+/[^/]+/[^/]+\.png)$", norm)
        if m:
            return _MINGRAMMER_TO_AZURE2.get(m.group(1))
    return None


# --- Style strings -------------------------------------------------------
#
# Container styles vary by depth and cluster kind. The detection is keyword-
# based on the cluster label so the LLM doesn't need to know about styles.

_AZURE_ICON_STYLE = (
    "sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;"
    "fillColor=#FFFFFF;strokeColor=none;dashed=0;verticalLabelPosition=bottom;"
    "verticalAlign=top;align=center;html=1;shape=image;image={image};"
)
_RECT_FALLBACK_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#F8F8F8;strokeColor=#666666;"
    "fontColor=#1A1A1A;fontSize=11;"
)
_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "html=1;strokeColor=#444444;strokeWidth=1.5;endArrow=block;endFill=1;"
)
_EDGE_STYLE_DASHED = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#999999;"
    "strokeWidth=1;dashed=1;dashPattern=4 4;endArrow=block;endFill=0;"
)
_BADGE_STYLE = (
    "ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;"
    "fontStyle=1;fontSize=11;align=center;verticalAlign=middle;html=1;"
)
_TITLE_STYLE = (
    "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
    "fontStyle=1;fontSize=16;fontColor=#1A1A1A;"
)


def _cluster_style(label: str) -> str:
    lab = label.lower()
    if "vnet" in lab or "virtual network" in lab or "vpc" in lab:
        return (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;"
            "strokeColor=#0078D4;strokeWidth=2;dashed=1;dashPattern=8 4;"
            "fontStyle=1;fontSize=12;align=left;verticalAlign=top;"
            "spacingTop=8;spacingLeft=12;"
        )
    if "subnet" in lab or "snet" in lab:
        return (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;"
            "strokeColor=#9BC2E6;strokeWidth=1;fontStyle=1;fontSize=11;"
            "align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;"
        )
    if "monitor" in lab or "observab" in lab:
        return (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;"
            "strokeColor=#BBBBBB;strokeWidth=1;fontStyle=1;fontSize=11;"
            "align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;"
        )
    if "identity" in lab or "entra" in lab or "ad b2c" in lab:
        return (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFF8E5;"
            "strokeColor=#D4A017;strokeWidth=1;fontStyle=1;fontSize=11;"
            "align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;"
        )
    # Default — neutral container (subscription, resource group, generic zone)
    return (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;"
        "strokeColor=#BBBBBB;strokeWidth=1;fontStyle=1;fontSize=11;"
        "align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;"
    )


# --- IR --------------------------------------------------------------------

@dataclass
class _Cluster:
    id: str
    label: str
    abs_x: float
    abs_y: float
    w: float
    h: float
    parent_id: str = "1"
    child_clusters: list[str] = field(default_factory=list)
    child_nodes: list[str] = field(default_factory=list)


@dataclass
class _Node:
    id: str
    label: str
    abs_x: float  # top-left in drawio space (absolute)
    abs_y: float
    w: float
    h: float
    parent_id: str = "1"
    icon_path: str | None = None  # mingrammer image path (kept for mapping)
    azure_icon: str | None = None  # AzureGeneric kind hint


@dataclass
class _Edge:
    id: str
    source: str
    target: str
    label: str = ""
    style_kind: str = "solid"  # solid | dashed
    # Graphviz-reported absolute label centre (drawio space), or None when
    # the edge has no label. Used for label-vs-label collision detection.
    label_pos: tuple[float, float] | None = None
    # Per-edge vertical label offset applied by collision detection.
    label_offset_y: int = 0
    # Spline endpoints in drawio absolute coords — the first sample is
    # where the edge leaves the source, the last is where it enters the
    # target. Used to place numbered badges along the edge instead of on
    # top of the label.
    spline_start: tuple[float, float] | None = None
    spline_end: tuple[float, float] | None = None


# --- Capture header ------------------------------------------------------
#
# This is the wrapper code that runs in the subprocess BEFORE the user's
# script. It exposes `AzureGeneric` and monkey-patches mingrammer so that
# `with Diagram(...)` writes the DOT source to a known path instead of
# rendering a PNG.

_CAPTURE_HEADER_TEMPLATE = '''\
# --- Auto-injected capture header ---
import os
import diagrams as _diagrams

class AzureGeneric(_diagrams.Node):
    """Mingrammer-compatible node for Azure services that don\'t ship with
    the diagrams library. In Graphviz it renders as a labelled rounded box;
    in drawio it is upgraded to the matching Azure2 SVG via the azure_icon
    hint (e.g. AzureGeneric("Bastion", azure_icon="bastions")).
    """
    _provider = "azure"
    _type = "generic"
    _icon_dir = None
    _icon = None

    def __init__(self, label="", *, azure_icon=None, **attrs):
        if azure_icon:
            attrs["azure_icon"] = azure_icon
        super().__init__(label, **attrs)

def _capture_render(self):
    out_path = {dot_path!r}
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(self.dot.source)

def _capture_exit(self, exc_type, exc_value, traceback):
    self.render()
    _diagrams.setdiagram(None)

_diagrams.Diagram.render = _capture_render
_diagrams.Diagram.__exit__ = _capture_exit
# --- end capture header ---
'''


def build_capture_script(user_code: str, dot_path: Path) -> str:
    header = _CAPTURE_HEADER_TEMPLATE.format(dot_path=str(dot_path))
    return header + "\n" + user_code


# --- Layout extraction ----------------------------------------------------

def _resolve_dot(env: dict[str, str]) -> str:
    """Resolve the `dot` executable using the PATH from the prepared env so
    we don't rely on the parent process's PATH (which may not have been
    refreshed since Graphviz was installed)."""
    dot = shutil.which("dot", path=env.get("PATH"))
    if dot:
        return dot
    # Last-resort: known Windows install locations.
    if sys.platform == "win32":
        for d in (r"C:\Program Files\Graphviz\bin", r"C:\Program Files (x86)\Graphviz\bin"):
            cand = Path(d, "dot.exe")
            if cand.is_file():
                return str(cand)
    raise RuntimeError(
        "Graphviz `dot` binary not found. Install Graphviz "
        "(`winget install Graphviz.Graphviz` on Windows) and restart the backend."
    )


# Graphviz's `dot -Tjson` corrupts its output when an `image=...` attribute
# references a PNG it can't embed: the loadimage plugin emits warnings and
# leaves empty `_draw_` fields, producing invalid JSON. We don't need the
# images for *layout* — only for icon mapping — so strip them before piping
# through dot and re-attach them afterward by node name.
_IMAGE_ATTR_RE = re.compile(r'\bimage\s*=\s*"[^"]*"\s*,?\s*', re.IGNORECASE)
# Capture each `node_id [ ... image="..." ... ]` so we can rebuild a
# node_name -> image_path map ourselves. Node IDs in mingrammer output are
# hex UUID strings (often starting with a digit), so the identifier
# character class must include digits as a first character. The negative
# lookahead skips global attribute lines (`node [...]`, `edge [...]`,
# `graph [...]`, `subgraph ...`).
_NODE_ATTR_BLOCK_RE = re.compile(
    r'(?m)^\s*"?(?!(?:node|edge|graph|subgraph)\b)([A-Za-z0-9_][A-Za-z0-9_]*)"?\s*\[(?P<attrs>[^\]]*)\]',
)
_INNER_KV_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _extract_node_images(dot_source: str) -> dict[str, str]:
    """Walk the DOT source, find `image="..."` attributes per node, return
    a {node_name: image_path} dict. Used after layout to re-attach icons
    that we stripped before piping through `dot -Tjson`."""
    out: dict[str, str] = {}
    for m in _NODE_ATTR_BLOCK_RE.finditer(dot_source):
        node_name = m.group(1)
        attrs = m.group("attrs")
        kvs = dict(_INNER_KV_RE.findall(attrs))
        if "image" in kvs:
            out[node_name] = kvs["image"]
        if "azure_icon" in kvs:
            # Stash under a synthetic key so the emitter can find it
            out[node_name + "::azure_icon"] = kvs["azure_icon"]
    return out


def run_dot_layout(dot_source: str, env: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Pipe (image-stripped) DOT source through `dot -Tjson`. Returns the
    parsed JSON layout plus a map of node_name -> original image path so the
    emitter can map icons after layout."""
    dot_exe = _resolve_dot(env)
    images = _extract_node_images(dot_source)
    sanitized = _IMAGE_ATTR_RE.sub("", dot_source)
    result = subprocess.run(
        [dot_exe, "-Tjson"],
        input=sanitized,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dot -Tjson failed: {result.stderr[:500]}")
    try:
        return json.loads(result.stdout), images
    except json.JSONDecodeError as e:
        snippet = result.stdout[max(0, e.pos - 80):e.pos + 80]
        raise RuntimeError(
            f"dot -Tjson produced invalid JSON at char {e.pos}: {e.msg}. "
            f"Snippet: {snippet!r}"
        ) from e


# --- Coordinate translation ----------------------------------------------

# Padding added to each cluster's drawn bbox so it doesn't crowd its children
# in drawio terms. Graphviz already adds some padding; we extend slightly to
# match the validator's 40px containment rule.
_CLUSTER_EXTRA_PAD = 24
# Drawio canvas margin around the whole diagram
_CANVAS_MARGIN = 60
# Fixed icon size in drawio space - keeps every resource the same visual size
# so the validator's overlap rules behave predictably.
_ICON_SIZE = 56


def _parse_bb(s: str) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in s.split(","))
    return x1, y1, x2, y2


def _parse_pos(s: str) -> tuple[float, float]:
    x, y = (float(v) for v in s.split(","))
    return x, y


def translate_layout(
    layout: dict, images: dict[str, str] | None = None,
) -> tuple[list[_Cluster], list[_Node], list[_Edge], int, int]:
    """Translate Graphviz JSON layout into drawio absolute coordinates.

    `images` is a {node_name: image_path} map extracted from the original
    DOT source (since dot -Tjson is fed an image-stripped copy).
    Returns (clusters, nodes, edges, canvas_width, canvas_height).
    """
    images = images or {}
    bb_x1, bb_y1, bb_x2, bb_y2 = _parse_bb(layout["bb"])
    max_y = bb_y2
    objects = layout.get("objects", [])

    # Pass 1: collect raw cluster + node info indexed by gvid.
    clusters_by_gvid: dict[int, _Cluster] = {}
    nodes_by_gvid: dict[int, _Node] = {}

    for obj in objects:
        gvid = obj.get("_gvid")
        name = obj.get("name", "")
        if name.startswith("cluster_"):
            cx1, cy1, cx2, cy2 = _parse_bb(obj["bb"])
            label = obj.get("label", "") or name.removeprefix("cluster_").replace("_", " ").title()
            # Pad slightly + flip Y. Top-left in drawio = (cx1, max_y - cy2).
            pad = _CLUSTER_EXTRA_PAD
            ax = cx1 - pad + _CANVAS_MARGIN
            ay = (max_y - cy2) - pad + _CANVAS_MARGIN
            w = (cx2 - cx1) + 2 * pad
            h = (cy2 - cy1) + 2 * pad
            clusters_by_gvid[gvid] = _Cluster(
                id=name, label=label, abs_x=ax, abs_y=ay, w=w, h=h,
            )
        else:
            # Node — name is the user/auto id from mingrammer
            label = obj.get("label", "")
            try:
                gcx, gcy = _parse_pos(obj["pos"])
            except (KeyError, ValueError):
                continue  # skip nodes Graphviz didn't position
            # width/height are in inches; convert to points (72 per inch)
            w_pt = float(obj.get("width", "1")) * 72
            h_pt = float(obj.get("height", "1")) * 72
            top_left_x = (gcx - w_pt / 2) + _CANVAS_MARGIN
            top_left_y = (max_y - gcy - h_pt / 2) + _CANVAS_MARGIN
            # Drop the Graphviz box; use a fixed icon size at the node centre.
            # The label sits below the icon in drawio (verticalLabelPosition=bottom),
            # so we keep the icon aligned with where the Graphviz centre was.
            cx = top_left_x + w_pt / 2
            cy = top_left_y + h_pt / 2
            nodes_by_gvid[gvid] = _Node(
                id=name,
                label=label,
                abs_x=cx - _ICON_SIZE / 2,
                abs_y=cy - _ICON_SIZE / 2,
                w=_ICON_SIZE,
                h=_ICON_SIZE,
                icon_path=images.get(name) or obj.get("image"),
                azure_icon=images.get(name + "::azure_icon") or obj.get("azure_icon"),
            )

    # Pass 2: parent relationships. Each cluster lists subgraphs (child
    # clusters by gvid) and nodes (descendant node gvids). Compute the
    # *immediate* parent of every node by descending: parent = deepest
    # cluster that lists the node in its `nodes` list.
    for obj in objects:
        name = obj.get("name", "")
        if not name.startswith("cluster_"):
            continue
        gvid = obj["_gvid"]
        for sub_id in obj.get("subgraphs", []):
            if sub_id in clusters_by_gvid:
                clusters_by_gvid[sub_id].parent_id = clusters_by_gvid[gvid].id
                clusters_by_gvid[gvid].child_clusters.append(clusters_by_gvid[sub_id].id)

    # Build a "node -> deepest cluster" map.
    # Walk clusters in topological order so deeper clusters win over their
    # ancestors via simple assignment.
    def cluster_depth(c: _Cluster, seen: set[str] | None = None) -> int:
        seen = seen or set()
        if c.parent_id == "1" or c.parent_id in seen:
            return 0
        parent = next((x for x in clusters_by_gvid.values() if x.id == c.parent_id), None)
        if parent is None:
            return 0
        seen.add(c.id)
        return 1 + cluster_depth(parent, seen)

    cluster_list = sorted(clusters_by_gvid.values(), key=cluster_depth)
    # Process shallow to deep so that the deepest assignment wins
    for c in cluster_list:
        for node_gvid in next(
            (obj.get("nodes", []) for obj in objects if obj.get("_gvid") == int(c.id.split("_")[-1].isdigit() and 0)),
            [],
        ):
            pass  # we'll do this differently below

    # Re-do node parenting from the raw objects (simpler).
    # For each cluster object, set parent_id of every listed node — deeper
    # clusters overwrite shallower because we sort by depth ascending then
    # descending; the final write for the deepest cluster wins.
    objects_by_gvid = {obj.get("_gvid"): obj for obj in objects}
    sorted_cluster_gvids = sorted(
        clusters_by_gvid.keys(),
        key=lambda gv: cluster_depth(clusters_by_gvid[gv]),
    )
    for gv in sorted_cluster_gvids:
        obj = objects_by_gvid[gv]
        for node_gv in obj.get("nodes", []):
            if node_gv in nodes_by_gvid:
                nodes_by_gvid[node_gv].parent_id = clusters_by_gvid[gv].id

    # Build child_nodes list per cluster (needed later for any pass-2 fixes,
    # not strictly required for emission but nice for debugging).
    for n in nodes_by_gvid.values():
        if n.parent_id != "1":
            cl = next((c for c in clusters_by_gvid.values() if c.id == n.parent_id), None)
            if cl is not None:
                cl.child_nodes.append(n.id)

    # Pass 3: edges
    edges_out: list[_Edge] = []
    for i, e in enumerate(layout.get("edges", [])):
        tail = e.get("tail")
        head = e.get("head")
        if tail is None or head is None:
            continue
        src = nodes_by_gvid.get(tail)
        tgt = nodes_by_gvid.get(head)
        if src is None or tgt is None:
            continue
        label = e.get("label", "") or ""
        style = (e.get("style") or "").lower()
        kind = "dashed" if "dashed" in style or "dotted" in style else "solid"

        # Pull the exact label centre Graphviz computed. The `_ldraw_` array
        # contains the operations for drawing the label; we want the `T`
        # (text) op's `pt`. Convert from Graphviz space (bottom-left, points)
        # to drawio absolute (top-left, pixels) using the same Y-flip the
        # nodes use.
        label_pos: tuple[float, float] | None = None
        for op in e.get("_ldraw_", []):
            if op.get("op") == "T" and "pt" in op:
                gpx, gpy = op["pt"]
                label_pos = (gpx + _CANVAS_MARGIN, (max_y - gpy) + _CANVAS_MARGIN)
                break

        # Pull the spline endpoints from the edge's `_draw_` `b` op so we can
        # position numbered badges along the edge instead of on top of the
        # label. The bezier points list goes [start, c1, c2, end, ...] with
        # additional segments concatenated; we take the very first and very
        # last sample.
        spline_start: tuple[float, float] | None = None
        spline_end: tuple[float, float] | None = None
        for op in e.get("_draw_", []):
            if op.get("op") in ("b", "B") and "points" in op:
                pts = op["points"]
                if pts:
                    sx, sy = pts[0]
                    ex, ey = pts[-1]
                    spline_start = (sx + _CANVAS_MARGIN, (max_y - sy) + _CANVAS_MARGIN)
                    spline_end = (ex + _CANVAS_MARGIN, (max_y - ey) + _CANVAS_MARGIN)
                break

        edges_out.append(_Edge(
            id=f"e{i}", source=src.id, target=tgt.id, label=label,
            style_kind=kind, label_pos=label_pos,
            spline_start=spline_start, spline_end=spline_end,
        ))

    # Detect labels whose Graphviz-computed render positions collide and
    # bump one of each pair upward so drawio places them apart. Two labels
    # within ~60px horizontal and ~24px vertical of each other would render
    # on top of each other. We keep the first label in place and offset
    # subsequent colliders cumulatively.
    placed: list[tuple[float, float, _Edge]] = []
    for e in edges_out:
        if e.label_pos is None or not e.label.strip():
            continue
        lx, ly = e.label_pos
        bump = 0
        for px, py, _ in placed:
            if abs(lx - px) < 60 and abs((ly + bump) - py) < 24:
                bump -= 24
        e.label_offset_y = bump
        placed.append((lx, ly + bump, e))

    # Canvas size: max extent of any cell + margin
    all_cells: list[tuple[float, float, float, float]] = [
        (c.abs_x, c.abs_y, c.w, c.h) for c in clusters_by_gvid.values()
    ] + [
        (n.abs_x, n.abs_y, n.w, n.h) for n in nodes_by_gvid.values()
    ]
    if all_cells:
        max_x = max(x + w for x, _, w, _ in all_cells)
        max_y_drawio = max(y + h for _, y, _, h in all_cells)
    else:
        max_x = max_y_drawio = 800
    canvas_w = int(max_x + _CANVAS_MARGIN)
    canvas_h = int(max_y_drawio + _CANVAS_MARGIN + 40)  # extra for title

    return (
        list(clusters_by_gvid.values()),
        list(nodes_by_gvid.values()),
        edges_out,
        canvas_w,
        canvas_h,
    )


# --- XML emission ---------------------------------------------------------

def _esc(s: str) -> str:
    # All cells use html=1 so literal \n must become <br> for drawio to render
    # a visual line break. Do the substitution before XML-escaping so the angle
    # brackets in <br> aren't double-escaped.
    return sx.escape(s.replace("\n", "<br>"), {'"': "&quot;"})


def _safe_id(s: str) -> str:
    """Drawio cell IDs must be unique and shouldn't contain spaces or quotes."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", s)


def emit_drawio(
    title: str,
    clusters: Iterable[_Cluster],
    nodes: Iterable[_Node],
    edges: Iterable[_Edge],
    canvas_w: int,
    canvas_h: int,
) -> str:
    out: list[str] = []
    out.append('<mxfile host="app.diagrams.net" version="24.7.17" type="device">')
    out.append(f'  <diagram id="d1" name="Page-1">')
    out.append(
        f'    <mxGraphModel dx="{canvas_w}" dy="{canvas_h}" grid="1" gridSize="10" '
        f'page="1" pageScale="1" pageWidth="{canvas_w}" pageHeight="{canvas_h}">'
    )
    out.append("      <root>")
    out.append('        <mxCell id="0"/>')
    out.append('        <mxCell id="1" parent="0"/>')

    # Title
    out.append(
        f'        <mxCell id="title" value="{_esc(title)}" style="{_TITLE_STYLE}" vertex="1" parent="1">'
    )
    out.append(
        f'          <mxGeometry x="30" y="20" width="{max(600, canvas_w - 60)}" height="36" as="geometry"/>'
    )
    out.append("        </mxCell>")

    # Index cells by id for parent-relative geometry
    cluster_index = {c.id: c for c in clusters}
    node_index = {n.id: n for n in nodes}

    def parent_offset(parent_id: str) -> tuple[float, float]:
        # In drawio, a cell's <mxGeometry x=...> is relative to its IMMEDIATE
        # parent. Both clusters and nodes store `abs_x` in drawio-absolute
        # space, so the relative offset is just `child.abs_x - parent.abs_x`.
        # Do NOT walk the chain — each ancestor's abs_x is itself already
        # absolute and would otherwise be double-counted.
        if parent_id == "1":
            return (0.0, 0.0)
        p = cluster_index.get(parent_id)
        if p is None:
            return (0.0, 0.0)
        return (p.abs_x, p.abs_y)

    # Containers first (parents before children so drawio z-orders sensibly).
    # Sort by depth ascending.
    def depth(c: _Cluster) -> int:
        d = 0
        cur = c
        while cur.parent_id != "1" and cur.parent_id in cluster_index:
            cur = cluster_index[cur.parent_id]
            d += 1
            if d > 20:
                break
        return d

    for c in sorted(clusters, key=depth):
        cid = _safe_id(c.id)
        ox, oy = parent_offset(c.parent_id)
        rx = c.abs_x - ox
        ry = c.abs_y - oy
        out.append(
            f'        <mxCell id="{cid}" value="{_esc(c.label)}" '
            f'style="{_cluster_style(c.label)}" vertex="1" parent="{_safe_id(c.parent_id)}">'
        )
        out.append(
            f'          <mxGeometry x="{int(rx)}" y="{int(ry)}" '
            f'width="{int(c.w)}" height="{int(c.h)}" as="geometry"/>'
        )
        out.append("        </mxCell>")

    # Nodes
    for n in nodes:
        nid = _safe_id(n.id)
        ox, oy = parent_offset(n.parent_id)
        rx = n.abs_x - ox
        ry = n.abs_y - oy
        svg = map_icon(n.icon_path, n.azure_icon)
        if svg:
            style = _AZURE_ICON_STYLE.format(image=svg)
        else:
            style = _RECT_FALLBACK_STYLE
        out.append(
            f'        <mxCell id="{nid}" value="{_esc(n.label)}" '
            f'style="{style}" vertex="1" parent="{_safe_id(n.parent_id)}">'
        )
        out.append(
            f'          <mxGeometry x="{int(rx)}" y="{int(ry)}" '
            f'width="{int(n.w)}" height="{int(n.h)}" as="geometry"/>'
        )
        out.append("        </mxCell>")

    # Edges
    badge_count = 0
    for e in edges:
        eid = _safe_id(e.id)
        style = _EDGE_STYLE_DASHED if e.style_kind == "dashed" else _EDGE_STYLE
        # Extract numbered badge if the label starts with a digit followed by space.
        label = e.label
        badge_value: str | None = None
        m = re.match(r"^\s*(\d+)\s+(.+)$", label)
        if m:
            badge_value = m.group(1)
            label = m.group(2)
        out.append(
            f'        <mxCell id="{eid}" value="{_esc(label)}" '
            f'style="{style}" edge="1" parent="1" '
            f'source="{_safe_id(e.source)}" target="{_safe_id(e.target)}">'
        )
        if e.label_offset_y:
            # Push the rendered label off other labels that would collide
            # at the same midpoint. drawio reads the offset from the inner
            # `<mxPoint as="offset">` on the edge geometry.
            out.append('          <mxGeometry relative="1" as="geometry">')
            out.append(f'            <mxPoint as="offset" x="0" y="{e.label_offset_y}"/>')
            out.append('          </mxGeometry>')
        else:
            out.append('          <mxGeometry relative="1" as="geometry"/>')
        out.append("        </mxCell>")

        if badge_value:
            badge_count += 1
            # Place the badge near the START of the edge spline (roughly 30%
            # along, so it sits on the connector but clear of the label
            # which Graphviz puts at the midpoint). This is the Microsoft-
            # reference convention: the numbered step lives next to where
            # the step originates, not on top of the label text.
            bx: int
            by: int
            if e.spline_start and e.spline_end:
                sx, sy = e.spline_start
                ex, ey = e.spline_end
                t = 0.30
                cx = sx + (ex - sx) * t
                cy = sy + (ey - sy) * t
                # Nudge the badge above a horizontal edge or to the side of
                # a vertical edge so it doesn't sit on top of the line.
                dx = ex - sx
                dy = ey - sy
                if abs(dx) >= abs(dy):
                    cy -= 20  # horizontal edge — lift badge above the line
                else:
                    cx -= 24  # vertical edge — push badge to the left
                bx = int(cx - 13)
                by = int(cy - 13)
            elif e.label_pos is not None:
                lx, ly = e.label_pos
                bx = int(lx - 13)
                by = int(ly - 40 + e.label_offset_y)
            else:
                src = node_index.get(e.source)
                tgt = node_index.get(e.target)
                if not (src and tgt):
                    continue
                src_ox, src_oy = parent_offset(src.parent_id)
                tgt_ox, tgt_oy = parent_offset(tgt.parent_id)
                scx = src.abs_x + src_ox + src.w / 2
                scy = src.abs_y + src_oy + src.h / 2
                tcx = tgt.abs_x + tgt_ox + tgt.w / 2
                tcy = tgt.abs_y + tgt_oy + tgt.h / 2
                bx = int((scx + tcx) / 2 - 13)
                by = int((scy + tcy) / 2 - 36)
            out.append(
                f'        <mxCell id="badge_{badge_count}" value="{badge_value}" '
                f'style="{_BADGE_STYLE}" vertex="1" parent="1">'
            )
            out.append(
                f'          <mxGeometry x="{bx}" y="{by}" width="26" height="26" as="geometry"/>'
            )
            out.append("        </mxCell>")

    out.append("      </root>")
    out.append("    </mxGraphModel>")
    out.append("  </diagram>")
    out.append("</mxfile>")
    return "\n".join(out)


# --- Entry point used by the tool -----------------------------------------

def pipeline(
    user_code: str,
    sandbox: Path,
    stem: str,
    title: str,
    env: dict[str, str],
    python_exe: str,
    timeout: int,
    subprocess_flags: dict,
) -> tuple[str, str]:
    """Run capture -> dot -Tjson -> emit. Returns (drawio_xml, python_source).

    Raises RuntimeError with a human-readable message on failure.
    """
    dot_path = sandbox / f"{stem}.dot"
    if dot_path.exists():
        dot_path.unlink()

    script_path = sandbox / f"{stem}.py"
    wrapped = build_capture_script(user_code, dot_path)
    script_path.write_text(wrapped, encoding="utf-8")

    proc = subprocess.run(
        [python_exe, str(script_path)],
        cwd=str(sandbox),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        **subprocess_flags,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(
            f"capture script failed (exit {proc.returncode}). "
            f"stderr: {stderr[-1000:]}"
        )

    if not dot_path.exists():
        raise RuntimeError(
            f"capture completed but no DOT file written at {dot_path}. "
            "Did your code include a `with Diagram(...)` block?"
        )

    dot_source = dot_path.read_text(encoding="utf-8")
    layout, images = run_dot_layout(dot_source, env=env)
    clusters, nodes, edges, w, h = translate_layout(layout, images=images)
    xml = emit_drawio(title, clusters, nodes, edges, w, h)
    return xml, wrapped
