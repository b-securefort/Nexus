"""Tests for the python-to-drawio emitter pure functions.

The pipeline integration (run user code → dot -Tjson → emit XML) is exercised
end-to-end via manual smoke runs that need a real Graphviz install. Here we
test only the pieces that don't need a subprocess: icon mapping, DOT regex
extraction, container styling, coordinate translation, and XML emission.
"""

import pytest

from app.tools.generic._drawio_emitter import (
    _AWS_GROUP_FILL,
    _Cluster,
    _Edge,
    _Node,
    _cluster_style,
    _extract_node_images,
    _safe_id,
    aws_group_fill,
    build_capture_script,
    emit_drawio,
    map_aws_icon,
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


# ── map_aws_icon ──────────────────────────────────────────────────────────


def test_map_aws_icon_compute_ec2():
    """A canonical mingrammer AWS compute image maps to the drawio aws4 stencil."""
    assert map_aws_icon(
        "/x/y/z/resources/aws/compute/ec2.png",
    ) == "ec2"


def test_map_aws_icon_storage_s3_long_filename():
    """mingrammer's S3 file is `simple-storage-service-s3.png`; drawio's shape
    is the short `s3`. The map must bridge the two naming conventions."""
    assert map_aws_icon(
        "/r/resources/aws/storage/simple-storage-service-s3.png",
    ) == "s3"


def test_map_aws_icon_network_alb():
    assert map_aws_icon(
        "/r/resources/aws/network/elb-application-load-balancer.png",
    ) == "application_load_balancer"


def test_map_aws_icon_network_transit_gateway():
    """Transit Gateway is the canonical hub-and-spoke connector — added
    after the initial MVP because the first hub-and-spoke smoke diagram
    had no way to model VPC interconnect."""
    assert map_aws_icon(
        "/r/resources/aws/network/transit-gateway.png",
    ) == "transit_gateway"


def test_map_aws_icon_security_iam_long_name():
    """IAM ships under aws.security with the longest filename in the catalog;
    the map keeps the drawio shape name verbatim (no truncation)."""
    assert map_aws_icon(
        "/r/resources/aws/security/identity-and-access-management-iam.png",
    ) == "identity_and_access_management"


def test_map_aws_icon_handles_windows_backslashes():
    assert map_aws_icon(
        r"C:\Program Files\diagrams\resources\aws\database\dynamodb.png",
    ) == "dynamodb"


def test_map_aws_icon_unknown_aws_path_returns_none():
    """An aws image outside the curated coverage (e.g. mobile / quantum /
    satellite namespaces, or the long tail of analytics services) falls
    through to None so the emitter uses the rectangle fallback rather than
    silently producing an invalid shape= name."""
    assert map_aws_icon(
        "/r/resources/aws/quantum/braket.png",
    ) is None


def test_map_aws_icon_no_input_returns_none():
    assert map_aws_icon(None) is None
    assert map_aws_icon("") is None


def test_emit_drawio_uses_aws_style_for_aws_node():
    """An AWS-mapped node renders via the `mxgraph.aws4.resourceIcon`
    wrapper with the service mark referenced through `resIcon=` — this
    yields the colored-tile-with-white-icon appearance from AWS's official
    icon set, not the rectangle fallback the validator now blocks.
    """
    nodes = [
        _Node(id="ec2_a", label="Web", abs_x=100, abs_y=100, w=56, h=56,
              icon_path="/x/resources/aws/compute/ec2.png"),
    ]
    xml = emit_drawio("AWS test", [], nodes, [], 500, 300)
    # Wrapper stencil + service mark.
    assert "shape=mxgraph.aws4.resourceIcon" in xml
    assert "resIcon=mxgraph.aws4.ec2" in xml
    # Should NOT fall through to the rectangle-fallback style.
    assert "#F8F8F8" not in xml


def test_emit_drawio_aws_style_includes_group_fill_color():
    """The `resourceIcon` wrapper tints its tile with `fillColor`. Without
    it, the headless renderer produces a white-on-white tile (invisible —
    the bug caught by the initial smoke test). Lock the style template so
    a future edit can't silently drop `fillColor=` and re-trigger the
    invisible-icon regression.
    """
    nodes = [
        _Node(id="ec2_a", label="Web", abs_x=100, abs_y=100, w=56, h=56,
              icon_path="/x/resources/aws/compute/ec2.png"),
    ]
    xml = emit_drawio("AWS test", [], nodes, [], 500, 300)
    # The compute group is orange (#ED7100) per the AWS official palette.
    assert "resIcon=mxgraph.aws4.ec2" in xml
    assert "fillColor=#ED7100" in xml


# ── aws_group_fill ────────────────────────────────────────────────────────


def test_aws_group_fill_known_groups_match_palette():
    """All canonical AWS service groups resolve to their official-palette
    fill color. Locks the palette so a typo (e.g. compute -> red) is caught
    by a unit test rather than the smoke render."""
    assert aws_group_fill("/r/resources/aws/compute/ec2.png") == "#ED7100"
    assert aws_group_fill("/r/resources/aws/network/vpc.png") == "#8C4FFF"
    assert aws_group_fill("/r/resources/aws/database/rds.png") == "#C7131F"
    assert aws_group_fill("/r/resources/aws/storage/simple-storage-service-s3.png") == "#7AA116"
    assert aws_group_fill("/r/resources/aws/security/waf.png") == "#DD344C"
    assert aws_group_fill("/r/resources/aws/analytics/athena.png") == "#8C4FFF"
    assert aws_group_fill("/r/resources/aws/ml/sagemaker.png") == "#01A88D"
    assert aws_group_fill("/r/resources/aws/integration/eventbridge.png") == "#E7157B"
    assert aws_group_fill("/r/resources/aws/management/cloudwatch.png") == "#E7157B"
    assert aws_group_fill("/r/resources/aws/iot/iot-core.png") == "#E7157B"
    assert aws_group_fill("/r/resources/aws/devtools/codepipeline.png") == "#C925D1"
    assert aws_group_fill("/r/resources/aws/general/users.png") == "#7D8998"


def test_aws_group_fill_unknown_group_falls_back_to_compute_orange():
    """An aws path under a group we haven't enumerated (e.g. quantum) still
    returns a valid fill — the renderer needs *some* color or the stencil
    goes invisible. Compute-orange is the canonical 'unspecified service'
    color in AWS's own diagrams."""
    fallback = aws_group_fill("/r/resources/aws/quantum/braket.png")
    assert fallback == _AWS_GROUP_FILL["compute"]


def test_aws_group_fill_none_input_returns_default():
    """A node with no icon_path (e.g. AzureGeneric-style hint) still gets a
    valid fill so the emit code path can rely on aws_group_fill never
    returning None."""
    assert aws_group_fill(None) == _AWS_GROUP_FILL["compute"]
    assert aws_group_fill("") == _AWS_GROUP_FILL["compute"]


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


def test_emit_drawio_badge_clears_label_render_zone_on_short_labeled_edge():
    """On a short edge with a remaining text label, the badge must clear the
    validator's badge-vs-label-render-zone check (50px horizontal, 40px
    vertical around the edge midpoint). Short labeled horizontal edges with
    the old t=0.30 + 20px lift packed badge centers at ~20px below midpoint
    and ~20% of span away — failing the 40px y-check for any edge shorter
    than ~250px.
    """
    import re as _re

    # 160px horizontal edge with a residual "HTTPS" label after badge extraction.
    nodes = [
        _Node(id="a", label="A", abs_x=100, abs_y=100, w=56, h=56),
        _Node(id="b", label="B", abs_x=300, abs_y=100, w=56, h=56),
    ]
    edges = [
        _Edge(id="e0", source="a", target="b", label="1 HTTPS",
              spline_start=(120, 128), spline_end=(280, 128)),
    ]
    xml = emit_drawio("T", [], nodes, edges, 500, 300)

    # Find the badge's geometry — `<mxGeometry x="..." y="..." width="26" height="26" .../>`
    # appears immediately after the badge cell opens.
    badge_section = xml.split('id="badge_1"', 1)[1]
    m = _re.search(r'mxGeometry x="(-?\d+)" y="(-?\d+)" width="26" height="26"', badge_section)
    assert m, "badge geometry not found"
    bx, by = int(m.group(1)), int(m.group(2))
    bcx, bcy = bx + 13, by + 13  # badge center per validator convention

    mid_x = (120 + 280) / 2  # 200
    mid_y = (128 + 128) / 2  # 128
    # Validator hint check: collision iff |bcx-mid_x|<50 AND |bcy-mid_y|<40.
    # At least one axis must clear.
    assert abs(bcx - mid_x) >= 50 or abs(bcy - mid_y) >= 40, (
        f"badge center ({bcx}, {bcy}) sits inside label-render zone of "
        f"midpoint ({mid_x}, {mid_y}); will trigger validator [hint] collision."
    )


def test_emit_drawio_badge_clears_label_render_zone_on_short_labeled_vertical_edge():
    """The bumped-placement logic has two branches: horizontal edges use a
    42px vertical lift, vertical edges use a 46px horizontal nudge. This
    test exercises the vertical-edge branch (otherwise it's dead-code from
    a test-coverage standpoint) and verifies the badge still clears the
    validator's badge-vs-label-render-zone check."""
    import re as _re

    # 160px vertical edge with a residual "https" label.
    nodes = [
        _Node(id="a", label="A", abs_x=100, abs_y=100, w=56, h=56),
        _Node(id="b", label="B", abs_x=100, abs_y=300, w=56, h=56),
    ]
    edges = [
        _Edge(id="e0", source="a", target="b", label="1 https",
              spline_start=(128, 120), spline_end=(128, 280)),
    ]
    xml = emit_drawio("T", [], nodes, edges, 300, 500)

    badge_section = xml.split('id="badge_1"', 1)[1]
    m = _re.search(r'mxGeometry x="(-?\d+)" y="(-?\d+)" width="26" height="26"', badge_section)
    assert m, "badge geometry not found"
    bx, by = int(m.group(1)), int(m.group(2))
    bcx, bcy = bx + 13, by + 13

    mid_x = (128 + 128) / 2  # 128
    mid_y = (120 + 280) / 2  # 200
    # Validator hint check passes iff at least one axis clears the threshold.
    assert abs(bcx - mid_x) >= 50 or abs(bcy - mid_y) >= 40, (
        f"badge center ({bcx}, {bcy}) collides with label-render zone of "
        f"midpoint ({mid_x}, {mid_y}) on vertical edge."
    )


def test_emit_drawio_badge_placement_unchanged_for_long_labeled_edge():
    """The tightened placement only kicks in when span < 240px. Longer
    labeled edges keep the original t=0.30 + 20px lift so we don't
    visually shift every badge in every existing diagram."""
    import re as _re

    # 500px horizontal edge — comfortably above the short-edge threshold.
    nodes = [
        _Node(id="a", label="A", abs_x=100, abs_y=100, w=56, h=56),
        _Node(id="b", label="B", abs_x=640, abs_y=100, w=56, h=56),
    ]
    edges = [
        _Edge(id="e0", source="a", target="b", label="1 HTTPS",
              spline_start=(120, 128), spline_end=(620, 128)),
    ]
    xml = emit_drawio("T", [], nodes, edges, 800, 300)
    # Original heuristic: t=0.30, perp=-20 → cx = 120 + 500*0.30 = 270, cy = 108.
    # bx = 257, by = 95.
    badge_section = xml.split('id="badge_1"', 1)[1]
    m = _re.search(r'mxGeometry x="(-?\d+)" y="(-?\d+)" width="26" height="26"', badge_section)
    assert m
    assert (int(m.group(1)), int(m.group(2))) == (257, 95)


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
