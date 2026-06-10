"""Style + icon catalogs — the visual layer the structural IR tokens resolve to.

Container/edge style strings are copied verbatim from
kb_data/kb/drawio/ms_reference_style.md so the output matches the Microsoft
reference look. Icon refs ("<provider>/<name>") resolve to an Azure2/AWS4 SVG
path or, for "shape/<name>", to a built-in draw.io shape style (no image).
Adding a provider = adding entries here; the engine is untouched.
"""

# --- Container style strings (ms_reference_style.md §Container Styles) ---
CONTAINER_STYLES: dict[str, str] = {
    "vnet": (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
        "strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=12;align=left;"
        "verticalAlign=top;spacingTop=8;spacingLeft=34;"  # spacingLeft clears a top-left VNet glyph
    ),
    "vpc": (  # AWS flavour — same structural box, AWS orange border
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFF8F0;strokeColor=#ED7100;"
        "strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=12;align=left;"
        "verticalAlign=top;spacingTop=8;spacingLeft=34;"
    ),
    "subnet": (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;"
        "strokeWidth=1;fontStyle=1;fontSize=11;align=left;verticalAlign=top;"
        "spacingTop=6;spacingLeft=6;"
    ),
    "resource_group": (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#AAAAAA;"
        "strokeWidth=1;dashed=1;dashPattern=6 4;fontStyle=1;fontSize=12;align=left;"
        "verticalAlign=top;spacingTop=8;spacingLeft=8;"
    ),
    "zone": (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;"
        "strokeWidth=1;dashed=0;fontStyle=1;fontSize=11;align=center;verticalAlign=top;"
        "spacingTop=6;fontColor=#555555;"
    ),
    "monitoring": (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#BBBBBB;"
        "strokeWidth=1;dashed=0;fontStyle=1;fontSize=11;align=left;verticalAlign=top;"
        "spacingTop=6;spacingLeft=8;"
    ),
    "group": (  # plain grouping box (e.g. an App Service plan wrapper)
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#B0B0B0;"
        "strokeWidth=1;fontStyle=1;fontSize=11;align=left;verticalAlign=top;"
        "spacingTop=6;spacingLeft=6;"
    ),
    "band": (  # invisible layout grouping — arranges children, draws nothing
        "rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=none;"
        "fontStyle=1;fontSize=11;align=left;verticalAlign=top;spacingTop=4;spacingLeft=4;"
    ),
}

# --- Edge style strings (ms_reference_style.md §Connector Styles) ---
EDGE_STYLES: dict[str, str] = {
    "flow": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
        "html=1;strokeColor=#444444;strokeWidth=1.5;endArrow=block;endFill=1;"
    ),
    "private": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
        "html=1;strokeColor=#444444;strokeWidth=1;dashed=1;dashPattern=6 4;endArrow=block;endFill=1;"
    ),
    "dns": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#888888;"
        "strokeWidth=1;dashed=1;dashPattern=4 4;endArrow=open;endFill=0;"
    ),
    "telemetry": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#999999;"
        "strokeWidth=1;dashed=1;dashPattern=4 4;endArrow=block;endFill=0;"
    ),
    "replication": (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#C55A11;"
        "strokeWidth=1;dashed=1;dashPattern=6 4;endArrow=block;endFill=1;"
    ),
}

# Azure2 icon node base style (ms_reference_style.md §Icon Style). `{image}` filled per node.
_ICON_STYLE = (
    "sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;"
    "strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;"
    "html=1;shape=image;image={image};"
)

# Icon ref ("<provider>/<name>") → Azure2/AWS4 SVG path. Paths verified against
# _drawio_emitter.py's _KIND_TO_SVG map and kb/drawio/azureicons_drawio.txt.
# Keys use the snake_case Azure resource-type name; common singular/plural and
# short-form aliases are included so the model's natural guess resolves rather
# than silently falling back to a generic box.
_ICON_IMAGES: dict[str, str] = {
    # --- networking ---
    "azure/front_doors": "img/lib/azure2/networking/Front_Doors.svg",
    "azure/front_door": "img/lib/azure2/networking/Front_Doors.svg",
    "azure/application_gateways": "img/lib/azure2/networking/Application_Gateways.svg",
    "azure/application_gateway": "img/lib/azure2/networking/Application_Gateways.svg",
    "azure/app_gateway": "img/lib/azure2/networking/Application_Gateways.svg",
    "azure/firewalls": "img/lib/azure2/networking/Firewalls.svg",
    "azure/firewall": "img/lib/azure2/networking/Firewalls.svg",
    "azure/load_balancers": "img/lib/azure2/networking/Load_Balancers.svg",
    "azure/load_balancer": "img/lib/azure2/networking/Load_Balancers.svg",
    "azure/nat_gateway": "img/lib/azure2/networking/NAT_Gateway.svg",
    "azure/virtual_networks": "img/lib/azure2/networking/Virtual_Networks.svg",
    "azure/virtual_network_gateways": "img/lib/azure2/networking/Virtual_Network_Gateways.svg",
    "azure/vpn_gateway": "img/lib/azure2/networking/Virtual_Network_Gateways.svg",
    "azure/expressroute": "img/lib/azure2/networking/ExpressRoute_Circuits.svg",
    "azure/network_security_groups": "img/lib/azure2/networking/Network_Security_Groups.svg",
    "azure/private_endpoint": "img/lib/azure2/networking/Private_Endpoint.svg",
    "azure/private_link": "img/lib/azure2/networking/Private_Link.svg",
    "azure/dns_private_zones": "img/lib/azure2/networking/DNS_Private_Zones.svg",
    "azure/dns_zones": "img/lib/azure2/networking/DNS_Zones.svg",
    "azure/public_ip_addresses": "img/lib/azure2/networking/Public_IP_Addresses.svg",
    "azure/public_ip": "img/lib/azure2/networking/Public_IP_Addresses.svg",
    "azure/bastions": "img/lib/azure2/networking/Bastions.svg",
    "azure/bastion": "img/lib/azure2/networking/Bastions.svg",
    "azure/route_tables": "img/lib/azure2/networking/Route_Tables.svg",
    "azure/subnet": "img/lib/azure2/networking/Subnet.svg",
    "azure/web_application_firewall": "img/lib/azure2/networking/Web_Application_Firewall_Policies_WAF.svg",
    # --- app / compute / containers ---
    "azure/app_services": "img/lib/azure2/app_services/App_Services.svg",
    "azure/app_service": "img/lib/azure2/app_services/App_Services.svg",
    "azure/app_service_plans": "img/lib/azure2/app_services/App_Service_Plans.svg",
    "azure/function_apps": "img/lib/azure2/compute/Function_Apps.svg",
    "azure/functions": "img/lib/azure2/compute/Function_Apps.svg",
    "azure/virtual_machine": "img/lib/azure2/compute/Virtual_Machine.svg",
    "azure/vm": "img/lib/azure2/compute/Virtual_Machine.svg",
    "azure/vm_scale_sets": "img/lib/azure2/compute/VM_Scale_Sets.svg",
    "azure/vmss": "img/lib/azure2/compute/VM_Scale_Sets.svg",
    "azure/kubernetes_services": "img/lib/azure2/compute/Kubernetes_Services.svg",
    "azure/aks": "img/lib/azure2/compute/Kubernetes_Services.svg",
    "azure/container_registries": "img/lib/azure2/containers/Container_Registries.svg",
    "azure/acr": "img/lib/azure2/containers/Container_Registries.svg",
    "azure/container_instances": "img/lib/azure2/compute/Container_Instances.svg",
    "azure/container_apps": "img/lib/azure2/containers/Container_Instances.svg",
    # --- databases ---
    "azure/sql_database": "img/lib/azure2/databases/SQL_Database.svg",
    "azure/sql_db": "img/lib/azure2/databases/SQL_Database.svg",
    "azure/sql_managed_instance": "img/lib/azure2/databases/SQL_Managed_Instance.svg",
    "azure/cosmos_db": "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "azure/cosmos": "img/lib/azure2/databases/Azure_Cosmos_DB.svg",
    "azure/redis": "img/lib/azure2/databases/Cache_Redis.svg",
    "azure/cache_redis": "img/lib/azure2/databases/Cache_Redis.svg",
    "azure/postgresql": "img/lib/azure2/databases/Azure_Database_PostgreSQL_Server.svg",
    "azure/mysql": "img/lib/azure2/databases/Azure_Database_MySQL_Server.svg",
    # --- storage ---
    "azure/storage_accounts": "img/lib/azure2/storage/Storage_Accounts.svg",
    "azure/storage_account": "img/lib/azure2/storage/Storage_Accounts.svg",
    "azure/blob": "img/lib/azure2/general/Blob_Block.svg",
    # --- identity / security ---
    "azure/entra_id": "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "azure/azure_active_directory": "img/lib/azure2/identity/Azure_Active_Directory.svg",
    "azure/managed_identities": "img/lib/azure2/identity/Managed_Identities.svg",
    "azure/managed_identity": "img/lib/azure2/identity/Managed_Identities.svg",
    "azure/key_vaults": "img/lib/azure2/security/Key_Vaults.svg",
    "azure/key_vault": "img/lib/azure2/security/Key_Vaults.svg",
    "azure/defender": "img/lib/azure2/security/Azure_Defender.svg",
    "azure/sentinel": "img/lib/azure2/security/Azure_Sentinel.svg",
    # --- monitoring / management ---
    "azure/monitor": "img/lib/azure2/management_governance/Monitor.svg",
    "azure/log_analytics": "img/lib/azure2/management_governance/Log_Analytics_Workspaces.svg",
    "azure/log_analytics_workspaces": "img/lib/azure2/management_governance/Log_Analytics_Workspaces.svg",
    "azure/application_insights": "img/lib/azure2/devops/Application_Insights.svg",
    "azure/app_insights": "img/lib/azure2/devops/Application_Insights.svg",
    "azure/policy": "img/lib/azure2/management_governance/Policy.svg",
    # --- integration ---
    "azure/api_management": "img/lib/azure2/integration/API_Management_Services.svg",
    "azure/apim": "img/lib/azure2/integration/API_Management_Services.svg",
    "azure/logic_apps": "img/lib/azure2/integration/Logic_Apps.svg",
    "azure/service_bus": "img/lib/azure2/integration/Service_Bus.svg",
    "azure/event_grid": "img/lib/azure2/integration/Event_Grid_Topics.svg",
    "azure/event_hubs": "img/lib/azure2/analytics/Event_Hubs.svg",
    "azure/app_configuration": "img/lib/azure2/integration/App_Configurations.svg",
    # --- AI / data ---
    "azure/openai": "img/lib/azure2/ai_machine_learning/Azure_OpenAI.svg",
    "azure/cognitive_services": "img/lib/azure2/ai_machine_learning/Cognitive_Services.svg",
    "azure/ai_search": "img/lib/azure2/ai_machine_learning/Cognitive_Search.svg",
    "azure/machine_learning": "img/lib/azure2/ai_machine_learning/Machine_Learning_Studio_Workspaces.svg",
    # --- general ---
    "azure/subscriptions": "img/lib/azure2/general/Subscriptions.svg",
    "azure/resource_groups": "img/lib/azure2/general/Resource_Groups.svg",
}

# Built-in draw.io shapes for generic / non-branded elements ("shape/<name>").
# These make the engine usable for non-cloud flow diagrams (the multi-provider +
# generic-flow requirement) — same layout engine, different stencils.
_BUILTIN_SHAPES: dict[str, str] = {
    "cloud": (
        "ellipse;shape=cloud;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#444444;"
        "verticalLabelPosition=bottom;verticalAlign=top;align=center;"
    ),
    "cylinder": (
        "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;fillColor=#FFFFFF;"
        "strokeColor=#444444;verticalLabelPosition=bottom;verticalAlign=top;align=center;"
    ),
    # Generic flowchart vocabulary (labels render INSIDE these, not below).
    "process": "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#444444;fontColor=#1A1A1A;",
    "subprocess": "shape=process;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#444444;fontColor=#1A1A1A;",
    "decision": "rhombus;whiteSpace=wrap;html=1;fillColor=#FFF2CC;strokeColor=#D6B656;fontColor=#1A1A1A;",
    "terminator": "rounded=1;arcSize=50;whiteSpace=wrap;html=1;fillColor=#D5E8D4;strokeColor=#82B366;fontColor=#1A1A1A;",
    "document": "shape=document;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#444444;fontColor=#1A1A1A;",
    "datastore": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;fillColor=#DAE8FC;strokeColor=#6C8EBF;fontColor=#1A1A1A;",
    "queue": "shape=mxgraph.flowchart.delay;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#444444;fontColor=#1A1A1A;",
    "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;fillColor=#FFFFFF;strokeColor=#444444;",
}

# AWS service-group → tile fill (AWS4 resource-icon convention). Mirrors
# _AWS_GROUP_FILL in _drawio_emitter.py (read-only reference).
_AWS_GROUP_FILL: dict[str, str] = {
    "compute": "#ED7100", "network": "#8C4FFF", "database": "#C7131F",
    "storage": "#7AA116", "security": "#DD344C", "analytics": "#8C4FFF",
    "integration": "#E7157B", "management": "#E7157B", "general": "#7D8998",
}
# AWS4 resource-icon style: colored rounded tile + white service mark.
_AWS_ICON_STYLE = (
    "sketch=0;outlineConnect=0;gradientColor=none;dashed=0;verticalLabelPosition=bottom;"
    "verticalAlign=top;align=center;html=1;fontColor=#232F3E;fillColor={fill};"
    "strokeColor=#ffffff;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{service};"
)
# Icon ref "aws/<name>" → (aws4 resIcon stencil, service group for the fill).
_AWS_ICONS: dict[str, tuple[str, str]] = {
    "aws/route_53": ("route_53", "network"),
    "aws/cloudfront": ("cloudfront", "network"),
    "aws/waf": ("waf", "security"),
    "aws/application_load_balancer": ("application_load_balancer", "network"),
    "aws/nat_gateway": ("nat_gateway", "network"),
    "aws/vpc": ("vpc", "network"),
    "aws/api_gateway": ("api_gateway", "network"),
    "aws/ecs": ("ecs", "compute"),
    "aws/eks": ("eks", "compute"),
    "aws/lambda": ("lambda", "compute"),
    "aws/ec2": ("ec2", "compute"),
    "aws/aurora": ("aurora", "database"),
    "aws/rds": ("rds", "database"),
    "aws/dynamodb": ("dynamodb", "database"),
    "aws/elasticache": ("elasticache", "database"),
    "aws/s3": ("s3", "storage"),
    "aws/secrets_manager": ("secrets_manager", "security"),
    "aws/iam": ("identity_and_access_management", "security"),
}


def icon_known(ref: str) -> bool:
    """True if an icon ref resolves to a real stencil/shape (not the fallback box)."""
    provider, _, name = ref.partition("/")
    if provider == "shape":
        return name in _BUILTIN_SHAPES
    if provider == "aws":
        return ref in _AWS_ICONS
    return ref in _ICON_IMAGES


def suggest_icons(ref: str, limit: int = 3) -> list[str]:
    """Closest catalog refs to an unknown one, same provider preferred.

    Backs the validator's icon error so a near-miss like
    'azure/api_management_services' costs one corrected retry instead of the
    author needing the full catalog in their prompt."""
    import difflib

    provider, _, _name = ref.partition("/")
    if provider == "shape":
        pool = [f"shape/{k}" for k in _BUILTIN_SHAPES]
    elif provider == "aws":
        pool = list(_AWS_ICONS)
    elif provider == "azure":
        pool = list(_ICON_IMAGES)
    else:
        pool = [*(f"shape/{k}" for k in _BUILTIN_SHAPES), *_AWS_ICONS, *_ICON_IMAGES]
    return difflib.get_close_matches(ref, pool, n=limit, cutoff=0.5)


def container_style(token: str) -> str:
    return CONTAINER_STYLES.get(token, CONTAINER_STYLES["group"])


def edge_style(token: str) -> str:
    return EDGE_STYLES.get(token, EDGE_STYLES["flow"])


def icon_style(ref: str) -> str:
    """Resolve an icon ref to a full draw.io cell style string."""
    provider, _, name = ref.partition("/")
    if provider == "shape":
        return _BUILTIN_SHAPES.get(name, _BUILTIN_SHAPES["cloud"])
    if provider == "aws":
        spec = _AWS_ICONS.get(ref)
        if spec is None:
            return "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#888888;"
        service, group = spec
        return _AWS_ICON_STYLE.format(fill=_AWS_GROUP_FILL[group], service=service)
    image = _ICON_IMAGES.get(ref)
    if image is None:
        # Unknown icon → labelled rounded box, so a missing mapping is visible,
        # not a crash. (The future validator can flag these.)
        return "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#888888;"
    return _ICON_STYLE.format(image=image)
