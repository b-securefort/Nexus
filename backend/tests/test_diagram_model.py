"""diagram_model: SemanticGraph → View projection → IR, and the ARG importer."""

import pytest

from app.diagram_model import Relation, Resource, SemanticGraph, View, project
from app.diagram_model.azure_import import from_resource_graph
from app.diagram_ir.layout import layout_diagram
from app.diagram_ir.loader import load_ir
from app.diagram_ir.routing import route_edges_gutter
from app.diagram_ir.emit import emit_drawio
from app.diagram_ir.validate import validate_ir


def _sample_graph() -> SemanticGraph:
    return SemanticGraph(
        title="Sample",
        resources=[
            Resource(id="rg", label="rg-prod", rtype="microsoft.resources/resourcegroups"),
            Resource(id="vnet", label="vnet-hub", rtype="microsoft.network/virtualnetworks", parent="rg"),
            Resource(id="snet", label="snet-app", rtype="microsoft.network/virtualnetworks/subnets", parent="vnet"),
            Resource(id="vm", label="vm-app", rtype="microsoft.compute/virtualmachines", parent="snet", icon="azure/vm"),
            Resource(id="kv", label="kv-prod", rtype="microsoft.keyvault/vaults", parent="rg", icon="azure/key_vaults"),
            Resource(id="law", label="law-prod", rtype="microsoft.operationalinsights/workspaces", parent="rg", icon="azure/log_analytics"),
        ],
        relations=[
            Relation(source="vm", target="kv", type="private", label="secrets"),
            Relation(source="vm", target="law", type="telemetry"),
        ],
    )


class TestProjection:
    def test_identity_view_maps_tree_and_relations(self):
        ir = project(_sample_graph())
        assert {c["id"] for c in ir["containers"]} == {"rg", "vnet", "snet"}
        assert {n["id"] for n in ir["nodes"]} == {"vm", "kv", "law"}
        snet = next(c for c in ir["containers"] if c["id"] == "snet")
        assert snet["style"] == "subnet" and snet["parent"] == "vnet"
        assert {(e["source"], e["target"]) for e in ir["edges"]} == {("vm", "kv"), ("vm", "law")}

    def test_projection_is_deterministic(self):
        g = _sample_graph()
        assert project(g, View(direction="TB")) == project(g, View(direction="TB"))

    def test_identity_projection_loads_and_renders(self):
        d = load_ir(project(_sample_graph()))
        v = validate_ir(d)
        assert v.ok, v.report()
        layout_diagram(d)
        routes = route_edges_gutter(d)
        assert emit_drawio(d, routes).startswith("<mxfile")

    def test_collapse_folds_subtree_and_retargets_edges(self):
        ir = project(_sample_graph(), View(collapse=["vnet"]))
        # vnet is now a leaf node with a fold count; its subtree is gone
        ids = {n["id"] for n in ir["nodes"]}
        assert "vnet" in ids and "vm" not in ids and "snet" not in ids
        vnet = next(n for n in ir["nodes"] if n["id"] == "vnet")
        assert vnet["label"] == "vnet-hub (2)"
        # vm's relations re-target to the collapsed vnet, labels dropped
        assert {(e["source"], e["target"]) for e in ir["edges"]} == {("vnet", "kv"), ("vnet", "law")}
        assert all(e["label"] == "" for e in ir["edges"])

    def test_collapsed_view_still_renders(self):
        d = load_ir(project(_sample_graph(), View(collapse=["vnet"])))
        assert validate_ir(d).ok
        layout_diagram(d)
        assert emit_drawio(d, route_edges_gutter(d)).startswith("<mxfile")

    def test_include_keeps_ancestors_and_descendants(self):
        ir = project(_sample_graph(), View(include=["snet"]))
        assert {c["id"] for c in ir["containers"]} == {"rg", "vnet", "snet"}
        assert {n["id"] for n in ir["nodes"]} == {"vm"}
        assert ir["edges"] == []          # kv/law out of frame → edges dropped

    def test_exclude_drops_subtree_and_its_edges(self):
        ir = project(_sample_graph(), View(exclude=["vnet"]))
        assert {n["id"] for n in ir["nodes"]} == {"kv", "law"}
        assert ir["edges"] == []

    def test_unknown_ids_rejected(self):
        for bad in (View(include=["nope"]), View(exclude=["nope"]), View(collapse=["nope"])):
            with pytest.raises(ValueError):
                project(_sample_graph(), bad)

    def test_leaf_without_icon_gets_fallback(self):
        g = SemanticGraph(resources=[Resource(id="x", label="X")])
        assert project(g)["nodes"][0]["icon"] == "shape/process"


_SUB = "11111111-2222-3333-4444-555555555555"


def _arm(rg: str, provider: str, name: str) -> str:
    return f"/subscriptions/{_SUB}/resourceGroups/{rg}/providers/{provider}/{name}"


def _sample_rows() -> list[dict]:
    vnet_id = _arm("rg-prod", "Microsoft.Network/virtualNetworks", "vnet-hub")
    kv_id = _arm("rg-prod", "Microsoft.KeyVault/vaults", "kv-prod")
    return [
        {"id": vnet_id, "name": "vnet-hub", "type": "Microsoft.Network/virtualNetworks",
         "resourceGroup": "rg-prod", "subscriptionId": _SUB,
         "properties": {"subnets": [{"name": "snet-app"}, {"name": "snet-data"}],
                        "virtualNetworkPeerings": [{"properties": {"remoteVirtualNetwork": {
                            "id": _arm("rg-spoke", "Microsoft.Network/virtualNetworks", "vnet-spoke")}}}]}},
        {"id": _arm("rg-spoke", "Microsoft.Network/virtualNetworks", "vnet-spoke"),
         "name": "vnet-spoke", "type": "Microsoft.Network/virtualNetworks",
         "resourceGroup": "rg-spoke", "subscriptionId": _SUB,
         # the same peering declared from the other side must not duplicate
         "properties": {"virtualNetworkPeerings": [{"properties": {"remoteVirtualNetwork": {"id": vnet_id}}}]}},
        {"id": kv_id, "name": "kv-prod", "type": "Microsoft.KeyVault/vaults",
         "resourceGroup": "rg-prod", "subscriptionId": _SUB},
        {"id": _arm("rg-prod", "Microsoft.Network/privateEndpoints", "pe-kv"),
         "name": "pe-kv", "type": "Microsoft.Network/privateEndpoints",
         "resourceGroup": "rg-prod", "subscriptionId": _SUB,
         "properties": {"subnet": {"id": f"{vnet_id}/subnets/snet-data"},
                        "privateLinkServiceConnections": [
                            {"properties": {"privateLinkServiceId": kv_id}}]}},
        {"id": _arm("rg-prod", "Microsoft.Web/sites", "app-web"),
         "name": "app-web", "type": "Microsoft.Web/sites",
         "resourceGroup": "rg-prod", "subscriptionId": _SUB},
    ]


class TestAzureImport:
    def test_containment_tree(self):
        g = from_resource_graph(_sample_rows())
        by_id = g.by_id()
        assert by_id["rg_rg_prod__vnet_hub"].parent == "rg_rg_prod"
        assert by_id["rg_rg_prod__vnet_hub__snet_app"].parent == "rg_rg_prod__vnet_hub"
        # single subscription → no subscription wrapper
        assert not any(r.rtype == "microsoft.resources/subscriptions" for r in g.resources)

    def test_private_endpoint_reparented_and_linked(self):
        g = from_resource_graph(_sample_rows())
        pe = g.by_id()["rg_rg_prod__pe_kv"]
        assert pe.parent == "rg_rg_prod__vnet_hub__snet_data"
        assert any(r.source == pe.id and r.target == "rg_rg_prod__kv_prod"
                   and r.type == "private" for r in g.relations)

    def test_peering_deduped_to_one_relation(self):
        g = from_resource_graph(_sample_rows())
        peerings = [r for r in g.relations if r.label == "peering"]
        assert len(peerings) == 1

    def test_icons_from_type_map(self):
        g = from_resource_graph(_sample_rows())
        assert g.by_id()["rg_rg_prod__app_web"].icon == "azure/app_services"
        assert g.by_id()["rg_rg_prod__kv_prod"].icon == "azure/key_vaults"

    def test_reimport_yields_identical_graph(self):
        assert from_resource_graph(_sample_rows()) == from_resource_graph(_sample_rows())

    def test_imported_graph_projects_and_renders(self):
        d = load_ir(project(from_resource_graph(_sample_rows())))
        v = validate_ir(d)
        assert v.ok, v.report()
        layout_diagram(d)
        assert emit_drawio(d, route_edges_gutter(d)).startswith("<mxfile")
