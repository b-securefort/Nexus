"""Build a SemanticGraph from Azure Resource Graph rows — diagrams from truth.

The hard part of an architecture diagram is choosing what to show, not
inventing what exists. This importer takes the rows an `az_resource_graph`
query already returns (`id, name, type, resourceGroup, subscriptionId,
properties`) and builds the graph mechanically, so the agent curates a View
over real topology instead of recalling it from chat memory.

v1 scope (one relation type at a time, like the KB importer grew):
  * containment — subscription (only when >1) ▸ resource group ▸ resource
  * VNet subnets from `properties.subnets[]`, as children of their VNet
  * private endpoints re-parented into their subnet; a `private` relation to
    the private-link target when that target is in the import
  * VNet peerings as `private` "peering" relations (deduped A↔B)

Ids are deterministic slugs of resourceGroup/name, so re-importing the same
scope yields the same graph and Views/edits built on it stay valid.
"""

from __future__ import annotations

import re

from .graph import Relation, Resource, SemanticGraph

# Provider type → catalog icon ref. Unknown types render as a generic box via
# the projection fallback — never a failed import.
_TYPE_ICON = {
    "microsoft.web/sites": "azure/app_services",
    "microsoft.web/serverfarms": "azure/app_service_plans",
    "microsoft.sql/servers": "azure/sql_database",
    "microsoft.sql/servers/databases": "azure/sql_database",
    "microsoft.dbforpostgresql/flexibleservers": "azure/postgresql",
    "microsoft.dbformysql/flexibleservers": "azure/mysql",
    "microsoft.documentdb/databaseaccounts": "azure/cosmos_db",
    "microsoft.cache/redis": "azure/redis",
    "microsoft.storage/storageaccounts": "azure/storage_accounts",
    "microsoft.keyvault/vaults": "azure/key_vaults",
    "microsoft.network/virtualnetworks": "azure/virtual_networks",
    "microsoft.network/virtualnetworks/subnets": "azure/subnet",
    "microsoft.network/privateendpoints": "azure/private_endpoint",
    "microsoft.network/privatednszones": "azure/dns_private_zones",
    "microsoft.network/azurefirewalls": "azure/firewalls",
    "microsoft.network/bastionhosts": "azure/bastions",
    "microsoft.network/applicationgateways": "azure/application_gateways",
    "microsoft.network/frontdoors": "azure/front_doors",
    "microsoft.cdn/profiles": "azure/front_doors",
    "microsoft.network/virtualnetworkgateways": "azure/virtual_network_gateways",
    "microsoft.network/loadbalancers": "azure/load_balancers",
    "microsoft.network/publicipaddresses": "azure/public_ip_addresses",
    "microsoft.network/networksecuritygroups": "azure/network_security_groups",
    "microsoft.compute/virtualmachines": "azure/vm",
    "microsoft.compute/virtualmachinescalesets": "azure/vm_scale_sets",
    "microsoft.containerservice/managedclusters": "azure/aks",
    "microsoft.containerregistry/registries": "azure/container_registries",
    "microsoft.app/containerapps": "azure/container_apps",
    "microsoft.apimanagement/service": "azure/api_management",
    "microsoft.servicebus/namespaces": "azure/service_bus",
    "microsoft.eventhub/namespaces": "azure/event_hubs",
    "microsoft.eventgrid/topics": "azure/event_grid",
    "microsoft.logic/workflows": "azure/logic_apps",
    "microsoft.cognitiveservices/accounts": "azure/cognitive_services",
    "microsoft.search/searchservices": "azure/ai_search",
    "microsoft.insights/components": "azure/application_insights",
    "microsoft.operationalinsights/workspaces": "azure/log_analytics",
    "microsoft.managedidentity/userassignedidentities": "azure/managed_identities",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def from_resource_graph(rows: list[dict], title: str = "") -> SemanticGraph:
    g = SemanticGraph(title=title or "Imported Azure topology")
    arm_to_rid: dict[str, str] = {}      # lowercase ARM id → graph resource id

    subs = sorted({str(r.get("subscriptionId", "")) for r in rows if r.get("subscriptionId")})
    multi_sub = len(subs) > 1
    if multi_sub:
        for sub in subs:
            g.resources.append(Resource(
                id=f"sub_{_slug(sub[:8])}", label=f"Subscription {sub[:8]}",
                rtype="microsoft.resources/subscriptions",
            ))

    rgs: dict[tuple[str, str], str] = {}  # (sub, rg-lower) → container id
    def _rg_container(sub: str, rg: str) -> str:
        key = (sub, rg.lower())
        if key not in rgs:
            rid = f"rg_{_slug(rg)}" if not multi_sub else f"sub_{_slug(sub[:8])}__rg_{_slug(rg)}"
            rgs[key] = rid
            g.resources.append(Resource(
                id=rid, label=rg, rtype="microsoft.resources/resourcegroups",
                parent=f"sub_{_slug(sub[:8])}" if multi_sub else None,
            ))
        return rgs[key]

    # --- pass 1: resources, containment, subnets ---
    for row in rows:
        rtype = str(row.get("type", "")).lower()
        name, rg, sub = str(row.get("name", "")), str(row.get("resourceGroup", "")), str(row.get("subscriptionId", ""))
        if not name or not rg:
            continue
        rid = f"{_rg_container(sub, rg)}__{_slug(name)}"
        g.resources.append(Resource(
            id=rid, label=name, rtype=rtype, parent=_rg_container(sub, rg),
            icon=_TYPE_ICON.get(rtype, ""),
            meta={"arm_id": str(row.get("id", "")), "location": str(row.get("location", ""))},
        ))
        arm_to_rid[str(row.get("id", "")).lower()] = rid

        if rtype == "microsoft.network/virtualnetworks":
            for sn in (row.get("properties") or {}).get("subnets") or []:
                sn_name = str(sn.get("name", ""))
                if not sn_name:
                    continue
                sn_id = f"{rid}__{_slug(sn_name)}"
                g.resources.append(Resource(
                    id=sn_id, label=sn_name,
                    rtype="microsoft.network/virtualnetworks/subnets", parent=rid,
                ))
                arm_to_rid[f"{str(row.get('id', '')).lower()}/subnets/{sn_name.lower()}"] = sn_id

    # --- pass 2: relations + re-parenting that needs the full id map ---
    by_id = g.by_id()
    for row in rows:
        rtype = str(row.get("type", "")).lower()
        rid = arm_to_rid.get(str(row.get("id", "")).lower())
        props = row.get("properties") or {}
        if rid is None:
            continue

        if rtype == "microsoft.network/privateendpoints":
            sn_arm = str((props.get("subnet") or {}).get("id", "")).lower()
            if sn_arm in arm_to_rid:      # draw the PE where it lives: in its subnet
                by_id[rid].parent = arm_to_rid[sn_arm]
            for conn in props.get("privateLinkServiceConnections") or []:
                target_arm = str((conn.get("properties") or {}).get("privateLinkServiceId", "")).lower()
                if target_arm in arm_to_rid:
                    g.relations.append(Relation(source=rid, target=arm_to_rid[target_arm], type="private"))

        elif rtype == "microsoft.network/virtualnetworks":
            for peering in props.get("virtualNetworkPeerings") or []:
                remote_arm = str(((peering.get("properties") or {}).get("remoteVirtualNetwork") or {}).get("id", "")).lower()
                remote = arm_to_rid.get(remote_arm)
                if remote and not any(                       # A↔B declares twice; draw once
                    {rel.source, rel.target} == {rid, remote} for rel in g.relations
                ):
                    g.relations.append(Relation(source=rid, target=remote, type="private", label="peering"))

    return g
