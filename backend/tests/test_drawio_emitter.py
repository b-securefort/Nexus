"""Tests for the python-to-drawio emitter pure functions.

The pipeline integration (run user code → dot -Tjson → emit XML) is exercised
end-to-end via manual smoke runs that need a real Graphviz install. Here we
test only the pieces that don't need a subprocess: icon mapping, DOT regex
extraction, container styling, coordinate translation, and XML emission.
"""

import pytest

from app.tools.generic._drawio_emitter import (
    _Cluster,
    _Edge,
    _Node,
    _cluster_style,
    _extract_node_images,
    _safe_id,
    build_capture_script,
    emit_drawio,
    map_icon,
    translate_layout,
)


# ── map_icon ──────────────────────────────────────────────────────────────


def test_map_icon_mingrammer_network():
    """A canonical mingrammer image path maps to the Azure2 SVG."""
    svg = map_icon(
        "C:/some/path/diagrams/resources/azure/network/application-gateway.png",
        None,
    )
    assert svg == "img/lib/azure2/networking/Application_Gateways.svg"


def test_map_icon_mingrammer_web_app_service():
    """The .web AppServices class — the one the LLM should use."""
    svg = map_icon(
        "/x/y/z/resources/azure/web/app-services.png",
        None,
    )
    assert svg == "img/lib/azure2/app_services/App_Services.svg"


def test_map_icon_mingrammer_compute_app_service():
    """Mingrammer re-exports AppServices under .compute too; both should map."""
    svg = map_icon(
        "/x/y/resources/azure/compute/app-services.png",
        None,
    )
    assert svg == "img/lib/azure2/app_services/App_Services.svg"


def test_map_icon_handles_windows_backslashes():
    """Mingrammer DOT output contains backslash separators on Windows."""
    svg = map_icon(
        r"C:\Program Files\diagrams\resources\azure\monitor\monitor.png",
        None,
    )
    assert svg == "img/lib/azure2/management_governance/Monitor.svg"


def test_map_icon_mixed_separators():
    """The actual mingrammer image attribute mixes / and \\ on Windows."""
    svg = map_icon(
        r"E:\app\resources/azure/network\public-ip-addresses.png",
        None,
    )
    assert svg == "img/lib/azure2/networking/Public_IP_Addresses.svg"


def test_map_icon_unknown_path_returns_none():
    assert map_icon("/some/unknown/path/foo.png", None) is None


def test_map_icon_no_inputs_returns_none():
    assert map_icon(None, None) is None
    assert map_icon("", "") is None


def test_map_icon_azure_kind_aliases():
    """AzureGeneric(azure_icon='...') hints map via the alias table."""
    assert map_icon(None, "bastion") == "img/lib/azure2/networking/Bastions.svg"
    assert map_icon(None, "waf_policy") == (
        "img/lib/azure2/networking/Web_Application_Firewall_Policies_WAF.svg"
    )
    # Both private_endpoint and private_link were added because the model
    # uses them interchangeably.
    assert map_icon(None, "private_endpoint") == (
        "img/lib/azure2/networking/Private_Endpoint.svg"
    )
    assert map_icon(None, "private_link") == (
        "img/lib/azure2/networking/Private_Link.svg"
    )
    # App Service alias — closed the gap where AzureGeneric(azure_icon="app_service")
    # fell through to a plain rectangle in v1.
    assert map_icon(None, "app_service") == (
        "img/lib/azure2/app_services/App_Services.svg"
    )


def test_map_icon_azure_kind_is_case_insensitive():
    assert map_icon(None, "BASTION") == "img/lib/azure2/networking/Bastions.svg"


def test_map_icon_azure_kind_takes_precedence_over_image_path():
    """If both inputs are provided, the explicit kind hint wins."""
    svg = map_icon(
        "/x/resources/azure/web/app-services.png",
        "key_vault",
    )
    assert svg == "img/lib/azure2/security/Key_Vaults.svg"


# ── _extract_node_images ──────────────────────────────────────────────────


def test_extract_node_images_canonical_dot():
    """A DOT block as mingrammer would emit it — multiple nodes with images."""
    dot = """
digraph G {
    node [shape=box];
    "07937b95fdce493da72d725bc77ad9ec" [label=Internet image="E:/foo/resources/onprem/client/users.png"]
    de91b9c9a9984bc399cfa9ac377ea62e [label="AppGW PIP" image="E:/foo/resources/azure/network/public-ip-addresses.png"]
}
"""
    images = _extract_node_images(dot)
    assert images["07937b95fdce493da72d725bc77ad9ec"] == "E:/foo/resources/onprem/client/users.png"
    assert images["de91b9c9a9984bc399cfa9ac377ea62e"] == "E:/foo/resources/azure/network/public-ip-addresses.png"


def test_extract_node_images_skips_global_attribute_blocks():
    """The `node [...]`, `edge [...]`, `graph [...]` lines must not be captured."""
    dot = """
digraph G {
    graph [label="X"]
    node [shape=box]
    edge [color=red]
    "abc123" [label=Foo image="x/y.png"]
}
"""
    images = _extract_node_images(dot)
    assert list(images.keys()) == ["abc123"]


def test_extract_node_images_captures_azure_icon_hint():
    """When AzureGeneric passes azure_icon, the attribute is recorded so the
    emitter can pick the right SVG via the alias table."""
    dot = """
digraph G {
    "node1" [label=Bastion azure_icon="bastion"]
}
"""
    images = _extract_node_images(dot)
    assert images["node1::azure_icon"] == "bastion"


def test_extract_node_images_node_id_starting_with_digit():
    """Mingrammer IDs are hex UUIDs; many start with a digit. Earlier regex
    rejected these and silently dropped icons."""
    dot = """
digraph G {
    "059c7717d77147d1b5cbc0b5056616bf" [label="VM" image="r/azure/compute/vm.png"]
}
"""
    images = _extract_node_images(dot)
    assert "059c7717d77147d1b5cbc0b5056616bf" in images


# ── _safe_id ──────────────────────────────────────────────────────────────


def test_safe_id_passes_alphanumeric():
    assert _safe_id("cluster_appgw_subnet") == "cluster_appgw_subnet"


def test_safe_id_replaces_spaces():
    """drawio cell IDs containing spaces break orthogonal edge routing."""
    assert _safe_id("cluster_App Gateway Subnet") == "cluster_App_Gateway_Subnet"


def test_safe_id_replaces_special_chars():
    assert _safe_id('cluster_"quoted"') == "cluster__quoted_"
    assert _safe_id("a/b\\c") == "a_b_c"


# ── _cluster_style ────────────────────────────────────────────────────────


def test_cluster_style_vnet_is_blue_dashed():
    """VNet containers must use the Microsoft blue + dashed pattern."""
    style = _cluster_style("Hub VNet")
    assert "#0078D4" in style
    assert "dashed=1" in style


def test_cluster_style_subnet_is_pale_blue():
    style = _cluster_style("AppGW subnet")
    assert "#F0F7FF" in style
    assert "#9BC2E6" in style


def test_cluster_style_monitoring_is_gray():
    style = _cluster_style("Monitoring")
    assert "#F5F5F5" in style


def test_cluster_style_identity_is_amber():
    style = _cluster_style("Identity")
    assert "#FFF8E5" in style


def test_cluster_style_default_for_unknown_label():
    """Unknown cluster labels get a neutral container style — never crash."""
    style = _cluster_style("Some random label")
    assert "rounded=0" in style


# ── build_capture_script ──────────────────────────────────────────────────


def test_build_capture_script_injects_azure_generic():
    user_code = "from diagrams import Diagram"
    out = build_capture_script(user_code, "C:/tmp/x.dot")
    assert "class AzureGeneric" in out
    # The user's code is preserved verbatim after the header.
    assert user_code in out


def test_build_capture_script_disables_render_to_capture_dot():
    """The header monkey-patches Diagram.render so the user's script writes
    DOT source to our path instead of producing a PNG. Without this the
    emitter has no layout to translate."""
    out = build_capture_script("", "C:/tmp/x.dot")
    assert "_capture_render" in out
    assert "self.dot.source" in out


def test_build_capture_script_strips_filename_path_safely():
    """The dot_path is interpolated as a raw repr, so Windows paths with
    backslashes remain valid Python string literals."""
    out = build_capture_script("", r"C:\tmp\x.dot")
    # repr() of a Windows path yields a raw-safe literal that parses.
    assert r"'C:\\tmp\\x.dot'" in out or r"'C:\tmp\x.dot'" in out


# ── translate_layout ──────────────────────────────────────────────────────


def _minimal_layout() -> dict:
    """A two-node, one-cluster Graphviz JSON layout, hand-built so the
    translate_layout test doesn't need to call `dot`. Coordinates match
    what Graphviz produces for a simple LR graph."""
    return {
        "bb": "0,0,400,200",
        "objects": [
            {
                "_gvid": 0,
                "name": "cluster_hub",
                "label": "Hub VNet",
                "bb": "20,20,200,180",
                "subgraphs": [],
                "nodes": [1, 2],
            },
            {
                "_gvid": 1,
                "name": "a",
                "label": "A",
                "pos": "60,100",
                "width": "1",
                "height": "1",
            },
            {
                "_gvid": 2,
                "name": "b",
                "label": "B",
                "pos": "160,100",
                "width": "1",
                "height": "1",
            },
        ],
        "edges": [
            {
                "tail": 1, "head": 2, "label": "1 step", "style": "",
                "_ldraw_": [{"op": "T", "pt": [110, 100], "text": "1 step"}],
                "_draw_": [{"op": "b", "points": [[80, 100], [80, 100], [140, 100], [140, 100]]}],
            },
        ],
    }


def test_translate_layout_parents_nodes_to_their_cluster():
    """The graphviz JSON lists descendant nodes inside the cluster; the
    translator must assign each node's parent to the deepest cluster that
    contains it."""
    clusters, nodes, edges, w, h = translate_layout(_minimal_layout())
    assert len(clusters) == 1
    assert clusters[0].id == "cluster_hub"
    # Both nodes parent to the hub cluster, not to canvas.
    for n in nodes:
        assert n.parent_id == "cluster_hub"


def test_translate_layout_emits_edge_with_label_and_style():
    _, _, edges, _, _ = translate_layout(_minimal_layout())
    assert len(edges) == 1
    e = edges[0]
    assert e.label == "1 step"
    assert e.style_kind == "solid"
    # Both endpoints and the spline-derived positions should be populated.
    assert e.spline_start is not None and e.spline_end is not None


def test_translate_layout_picks_up_dashed_style():
    layout = _minimal_layout()
    layout["edges"][0]["style"] = "dashed"
    _, _, edges, _, _ = translate_layout(layout)
    assert edges[0].style_kind == "dashed"


def test_translate_layout_applies_image_map_to_nodes():
    """Images come from the separately-extracted DOT regex, not from the
    layout JSON (we strip image= before piping through `dot -Tjson` so the
    JSON doesn't get corrupted)."""
    layout = _minimal_layout()
    images = {
        "a": "/some/path/resources/azure/web/app-services.png",
        "b::azure_icon": "bastion",
    }
    _, nodes, _, _, _ = translate_layout(layout, images=images)
    by_id = {n.id: n for n in nodes}
    assert by_id["a"].icon_path == images["a"]
    assert by_id["b"].azure_icon == "bastion"


# ── emit_drawio ───────────────────────────────────────────────────────────


def test_emit_drawio_basic_xml_shape():
    """Smoke test for the XML emitter: contains the boilerplate, the title
    cell, and the right number of <mxCell> entries."""
    clusters = [
        _Cluster(id="cluster_hub", label="Hub VNet",
                 abs_x=100, abs_y=100, w=300, h=200, parent_id="1"),
    ]
    nodes = [
        _Node(id="a", label="A", abs_x=140, abs_y=140, w=56, h=56,
              parent_id="cluster_hub", icon_path="/x/resources/azure/web/app-services.png"),
    ]
    edges: list[_Edge] = []
    xml = emit_drawio("Test diagram", clusters, nodes, edges, 600, 400)

    assert xml.startswith('<mxfile')
    assert 'name="Page-1"' in xml
    # Title cell carries the diagram title verbatim.
    assert 'value="Test diagram"' in xml
    # The App Services SVG must have been resolved from the icon path.
    assert "App_Services.svg" in xml
    # Cluster cell parented to canvas, node parented to cluster.
    assert 'parent="1"' in xml
    assert 'parent="cluster_hub"' in xml


def test_emit_drawio_extracts_numbered_badge_from_edge_label():
    """An edge label starting with `<digit> <text>` is split: the digit
    becomes a numbered green badge, the rest becomes the edge label."""
    nodes = [
        _Node(id="a", label="A", abs_x=100, abs_y=100, w=56, h=56),
        _Node(id="b", label="B", abs_x=300, abs_y=100, w=56, h=56),
    ]
    edges = [
        _Edge(id="e0", source="a", target="b", label="1 HTTPS",
              spline_start=(120, 128), spline_end=(280, 128)),
    ]
    xml = emit_drawio("T", [], nodes, edges, 500, 300)

    # A separate badge cell exists with value="1".
    assert 'id="badge_1"' in xml
    assert 'value="1"' in xml
    # The edge label has been stripped of the leading digit + space.
    assert 'value="HTTPS"' in xml
    # The literal "1 HTTPS" should NOT appear as an edge label — it's split.
    assert 'value="1 HTTPS"' not in xml


def test_emit_drawio_emits_label_offset_when_collision_detected():
    """When translate_layout sets label_offset_y to dodge a collision, the
    edge geometry must carry the corresponding `<mxPoint as="offset">`."""
    nodes = [
        _Node(id="a", label="A", abs_x=100, abs_y=100, w=56, h=56),
        _Node(id="b", label="B", abs_x=300, abs_y=100, w=56, h=56),
    ]
    edges = [
        _Edge(id="e0", source="a", target="b", label="logs",
              label_offset_y=-24),
    ]
    xml = emit_drawio("T", [], nodes, edges, 500, 300)
    assert '<mxPoint as="offset" x="0" y="-24"/>' in xml


def test_emit_drawio_falls_back_to_rect_for_unmapped_node():
    """A node with no icon_path and no azure_icon hint should still render
    (as a rounded rectangle), not crash or omit the cell."""
    nodes = [
        _Node(id="unknown", label="Mystery", abs_x=100, abs_y=100, w=56, h=56),
    ]
    xml = emit_drawio("T", [], nodes, [], 500, 300)
    # The rect-fallback style uses fillColor=#F8F8F8 (see _RECT_FALLBACK_STYLE).
    assert "#F8F8F8" in xml
    assert 'value="Mystery"' in xml
