"""Tests for the structural Diagram IR: loader (schema), validator (ref-integrity),
layout engine, and emitter."""

import pytest

from app.diagram_ir.emit import emit_drawio
from app.diagram_ir.examples import flow_spine, image5_auto, tiered_tb
from app.diagram_ir.geometry import check_edge_crossings, check_edge_overlaps
from app.diagram_ir.layout import layout_diagram
from app.diagram_ir.loader import IRSchemaError, load_ir
from app.diagram_ir.routing import route_edges_gutter
from app.diagram_ir.schema import Container, Diagram, Edge, Node
from app.diagram_ir.validate import validate_ir


# --- valid reference IR ---

def test_image5_auto_validates_clean():
    v = validate_ir(image5_auto.build())
    assert v.ok, v.report()
    assert not v.warnings, v.report()


# --- canonical LR flow spine: head on the left, tail on the right, clean route ---

def test_flow_spine_is_clean_and_directional():
    d = flow_spine.build()
    v = validate_ir(d)
    assert v.ok, v.report()
    layout_diagram(d)
    routes = route_edges_gutter(d)
    # the spine reads left→right with no icon-crossings or hidden arrows
    assert not check_edge_crossings(d, routes)
    assert not check_edge_overlaps(d, routes)
    # head (ingress/Front Door) sits left of the tail (monitoring)
    xs = {n.id: n.x for n in d.nodes}
    assert xs["afd"] < xs["appsvc"] < xs["kv"] < xs["appi"]


def test_tiered_tb_is_clean_and_directional():
    d = tiered_tb.build()
    v = validate_ir(d)
    assert v.ok, v.report()
    layout_diagram(d)
    routes = route_edges_gutter(d)
    assert not check_edge_crossings(d, routes)
    assert not check_edge_overlaps(d, routes)
    # head (edge tier) sits above the tail (monitoring) — flow reads top→bottom
    ys = {n.id: n.y for n in d.nodes}
    assert ys["afd"] < ys["appsvc"] < ys["kv"] < ys["appi"]


def test_image5_auto_full_pipeline_renders_xml():
    d = layout_diagram(image5_auto.build())
    xml = emit_drawio(d)
    assert xml.startswith("<mxfile")
    assert "img/lib/azure2/app_services/App_Services.svg" in xml  # icon resolved
    # layout assigned real geometry (no zeros left on the VNet)
    vnet = next(c for c in d.containers if c.id == "vnet")
    assert vnet.w > 0 and vnet.h > 0 and vnet.x > 0


# --- layout fidelity: container label-fit + satellite alignment ---

def test_container_width_fits_its_own_label():
    """A subnet whose label is far wider than its single narrow child must size
    to the label, not clip it (the 'Public subnet us-eas…' regression)."""
    from app.diagram_ir.layout import CHAR_W
    label = "Public subnet  us-east-1a"
    d = Diagram(direction="LR",
                containers=[Container(id="sub", label=label, style="subnet", children=["n"])],
                nodes=[Node(id="n", label="x", icon="azure/mysql", parent="sub")])
    layout_diagram(d)
    sub = d.containers[0]
    assert sub.w >= len(label) * CHAR_W      # the whole label fits inside the box
    child = d.nodes[0]                        # ...and the narrow child re-centered
    assert abs((child.x + child.w / 2) - (sub.x + sub.w / 2)) < 1.0


def _satellite_diagram(*sat_targets) -> Diagram:
    """canvas[ top-row(satellites), main-row(a, tgt, b) ] — satellites align_to tgt."""
    sats = [Node(id=f"s{i}", label=f"S{i}", icon="azure/mysql", parent="top", align_to=t)
            for i, t in enumerate(sat_targets)]
    return Diagram(direction="LR", containers=[
        Container(id="canvas", label="", style="band", layout="column", children=["top", "main"]),
        Container(id="top", label="", style="band", layout="row", parent="canvas",
                  children=[s.id for s in sats]),
        Container(id="main", label="", style="band", layout="row", parent="canvas",
                  children=["a", "tgt", "b"]),
    ], nodes=[*sats,
        Node(id="a", label="A", icon="azure/mysql", parent="main"),
        Node(id="tgt", label="Target", icon="azure/mysql", parent="main"),
        Node(id="b", label="B", icon="azure/mysql", parent="main"),
    ])


def test_align_to_centers_satellite_over_target():
    d = _satellite_diagram("tgt")
    layout_diagram(d)
    sat = next(n for n in d.nodes if n.id == "s0")
    tgt = next(n for n in d.nodes if n.id == "tgt")
    assert abs((sat.x + sat.w / 2) - (tgt.x + tgt.w / 2)) < 1.0


def test_aligned_siblings_do_not_overlap():
    """Two satellites targeting the same element must spread, not stack."""
    d = _satellite_diagram("tgt", "tgt")
    layout_diagram(d)
    s0, s1 = (next(n for n in d.nodes if n.id == sid) for sid in ("s0", "s1"))
    lo, hi = sorted((s0, s1), key=lambda n: n.x)
    assert lo.x + lo.w <= hi.x + 0.01        # no horizontal overlap


# --- layer 2: referential integrity (hard errors) ---

def _two_box_diagram(**edge_kwargs) -> Diagram:
    return Diagram(
        containers=[Container(id="vnet", label="VNet", style="vnet", children=["app"])],
        nodes=[Node(id="app", label="App", icon="azure/app_services", parent="vnet")],
        edges=[Edge(**edge_kwargs)] if edge_kwargs else [],
    )


def test_dangling_parent_is_error():
    d = Diagram(nodes=[Node(id="app", label="A", icon="azure/app_services", parent="ghost")])
    v = validate_ir(d)
    assert not v.ok
    assert any("parent 'ghost' does not exist" in e for e in v.errors)


def test_parent_children_disagreement_is_error():
    # node says parent=vnet, but vnet.children doesn't list it
    d = Diagram(
        containers=[Container(id="vnet", label="V", style="vnet", children=[])],
        nodes=[Node(id="app", label="A", icon="azure/app_services", parent="vnet")],
    )
    v = validate_ir(d)
    assert not v.ok
    assert any("disagree" in e for e in v.errors)


def test_edge_endpoint_missing_is_error():
    d = _two_box_diagram(source="app", target="ghost")
    v = validate_ir(d)
    assert not v.ok
    assert any("target 'ghost' does not exist" in e for e in v.errors)


def test_parent_is_node_is_error():
    d = Diagram(
        nodes=[
            Node(id="a", label="A", icon="azure/app_services"),
            Node(id="b", label="B", icon="azure/mysql", parent="a"),
        ],
    )
    v = validate_ir(d)
    assert not v.ok
    assert any("is a node, not a container" in e for e in v.errors)


def test_cycle_is_error():
    d = Diagram(containers=[
        Container(id="x", label="X", style="band", parent="y", children=["y"]),
        Container(id="y", label="Y", style="band", parent="x", children=["x"]),
    ])
    v = validate_ir(d)
    assert not v.ok
    assert any("cycle" in e for e in v.errors)


def test_duplicate_id_is_error():
    d = Diagram(nodes=[
        Node(id="dup", label="A", icon="azure/mysql"),
        Node(id="dup", label="B", icon="azure/mysql"),
    ])
    v = validate_ir(d)
    assert not v.ok
    assert any("duplicate id 'dup'" in e for e in v.errors)


# --- layer 1: token / icon legality ---

def test_unknown_style_is_error():
    d = Diagram(containers=[Container(id="c", label="C", style="bogus")])
    v = validate_ir(d)
    assert any("unknown style 'bogus'" in e for e in v.errors)


def test_unknown_icon_is_error():
    d = Diagram(nodes=[Node(id="n", label="N", icon="azure/not_a_real_icon")])
    v = validate_ir(d)
    assert any("not in catalog" in e for e in v.errors)


# --- style is optional: a forgotten style is inferred, not rejected ---

def test_missing_style_inferred_and_validates():
    # A container omitting `style` used to hard-fail the whole IR (the cause of
    # the model giving up and shipping a dumbed-down diagram). Now it's inferred:
    # inside a vnet -> subnet; otherwise -> group.
    data = {
        "containers": [
            {"id": "vnet", "label": "VNet", "style": "vnet", "children": ["snet"]},
            {"id": "snet", "label": "snet-app", "parent": "vnet", "children": ["app"]},
            {"id": "misc", "label": "Misc", "children": ["kv"]},
        ],
        "nodes": [
            {"id": "app", "label": "App", "icon": "azure/app_services", "parent": "snet"},
            {"id": "kv", "label": "KV", "icon": "azure/key_vaults", "parent": "misc"},
        ],
    }
    d = load_ir(data)
    styles = {c.id: c.style for c in d.containers}
    assert styles == {"vnet": "vnet", "snet": "subnet", "misc": "group"}
    assert validate_ir(d).ok


def test_missing_style_inferred_when_child_listed_before_parent():
    # Inference must not depend on container ordering in the list.
    data = {
        "containers": [
            {"id": "snet", "label": "snet", "parent": "vnet", "children": []},
            {"id": "vnet", "label": "VNet", "style": "vnet", "children": ["snet"]},
        ],
    }
    d = load_ir(data)
    assert {c.id: c.style for c in d.containers} == {"snet": "subnet", "vnet": "vnet"}


# --- advisory warnings (never block) ---

def test_isolated_node_warns_not_errors():
    d = Diagram(nodes=[Node(id="lonely", label="L", icon="azure/mysql")])
    v = validate_ir(d)
    assert v.ok                       # not blocked
    assert any("isolated" in w for w in v.warnings)


# --- loader (schema layer 1) ---

def test_load_ir_json_roundtrip():
    data = {
        "title": "t", "direction": "LR",
        "containers": [{"id": "vnet", "label": "VNet", "style": "vnet", "children": ["app"]}],
        "nodes": [{"id": "app", "label": "App", "icon": "azure/app_services", "parent": "vnet"}],
        "edges": [{"source": "app", "target": "app", "type": "flow"}],
    }
    d = load_ir(data)
    assert isinstance(d, Diagram)
    assert d.containers[0].style == "vnet"
    assert d.nodes[0].parent == "vnet"


def test_load_ir_parses_align_to():
    data = {
        "nodes": [
            {"id": "sat", "label": "S", "icon": "azure/mysql", "align_to": "tgt"},
            {"id": "tgt", "label": "T", "icon": "azure/mysql"},
        ],
        "containers": [{"id": "grp", "label": "G", "style": "group", "align_to": "tgt"}],
    }
    d = load_ir(data)
    assert d.nodes[0].align_to == "tgt"
    assert d.containers[0].align_to == "tgt"
    assert d.nodes[1].align_to is None     # absent → None


def test_same_band_align_to_is_ignored_and_warns():
    # align_to a sibling in the same band used to stack the two boxes on one line,
    # overlapping labels and collapsing chained edges into a single connector
    # (the "parallel lines to the same place" look). The engine must ignore it.
    data = {
        "direction": "LR",
        "containers": [
            {"id": "band", "style": "band", "layout": "row",
             "children": ["pe", "kv"]},
        ],
        "nodes": [
            {"id": "pe", "label": "pe-kv", "icon": "azure/private_endpoint",
             "parent": "band", "align_to": "kv"},
            {"id": "kv", "label": "kv", "icon": "azure/key_vaults", "parent": "band"},
        ],
    }
    d = load_ir(data)
    v = validate_ir(d)
    assert v.ok
    assert any("same band" in w for w in v.warnings)
    layout_diagram(d)
    pe = next(n for n in d.nodes if n.id == "pe")
    kv = next(n for n in d.nodes if n.id == "kv")
    # ignored => the two siblings keep their distinct row positions (not stacked).
    assert abs(pe.x - kv.x) > 1.0


def test_dangling_align_to_warns_not_errors():
    d = Diagram(nodes=[
        Node(id="a", label="A", icon="azure/mysql", align_to="ghost"),
        Node(id="b", label="B", icon="azure/mysql"),
    ], edges=[Edge("a", "b", "flow")])
    v = validate_ir(d)
    assert v.ok                                        # cosmetic hint never blocks
    assert any("align_to 'ghost' does not exist" in w for w in v.warnings)


def test_load_ir_missing_required_field_raises():
    with pytest.raises(IRSchemaError) as ei:
        load_ir({"nodes": [{"label": "no id or icon"}]})
    assert "missing required field 'id'" in str(ei.value)


def test_load_ir_wrong_type_raises():
    with pytest.raises(IRSchemaError) as ei:
        load_ir({"nodes": [{"id": 123, "icon": "azure/mysql"}]})
    assert "expected str" in str(ei.value)


# --- layer 3 (post-layout, advisory): cross-icon detection ---

def test_edge_through_icon_detected():
    """A -> C with B sitting directly between them on a row: every L-route
    crosses B, so it must be flagged."""
    from app.diagram_ir.geometry import check_edge_crossings
    d = Diagram(nodes=[
        Node(id="a", label="A", icon="azure/mysql", x=0, y=0, w=56, h=56),
        Node(id="b", label="B", icon="azure/mysql", x=200, y=0, w=56, h=56),
        Node(id="c", label="C", icon="azure/mysql", x=400, y=0, w=56, h=56),
    ], edges=[Edge("a", "c", "flow")])
    crossings = check_edge_crossings(d)
    assert len(crossings) == 1
    assert "edge-through-icon" in crossings[0] and "b" in crossings[0]


def test_clean_route_not_flagged():
    """A -> C with B well off the straight path: a clean L-route exists."""
    from app.diagram_ir.geometry import check_edge_crossings
    d = Diagram(nodes=[
        Node(id="a", label="A", icon="azure/mysql", x=0, y=0, w=56, h=56),
        Node(id="b", label="B", icon="azure/mysql", x=200, y=400, w=56, h=56),
        Node(id="c", label="C", icon="azure/mysql", x=400, y=0, w=56, h=56),
    ], edges=[Edge("a", "c", "flow")])
    assert check_edge_crossings(d) == []


def test_edge_overlap_detected():
    """Two edges leaving the same source toward targets in the same direction
    share their leaving segment -> one hides behind the other (C case)."""
    from app.diagram_ir.geometry import check_edge_overlaps
    d = Diagram(nodes=[
        Node(id="s", label="S", icon="azure/mysql", x=0, y=0, w=56, h=56),
        Node(id="t1", label="T1", icon="azure/mysql", x=300, y=-100, w=56, h=56),
        Node(id="t2", label="T2", icon="azure/mysql", x=300, y=100, w=56, h=56),
    ], edges=[Edge("s", "t1", "flow"), Edge("s", "t2", "flow")])
    overlaps = check_edge_overlaps(d)
    assert len(overlaps) == 1
    assert "edge-overlap" in overlaps[0]


def test_transversal_crossing_not_flagged_as_overlap():
    """A '+' crossing (perpendicular) must NOT be flagged as an overlap."""
    from app.diagram_ir.geometry import check_edge_overlaps
    d = Diagram(nodes=[
        Node(id="a", label="A", icon="azure/mysql", x=0, y=100, w=56, h=56),
        Node(id="b", label="B", icon="azure/mysql", x=400, y=100, w=56, h=56),
        Node(id="c", label="C", icon="azure/mysql", x=200, y=0, w=56, h=56),
        Node(id="e", label="E", icon="azure/mysql", x=200, y=300, w=56, h=56),
    ], edges=[Edge("a", "b", "flow"), Edge("c", "e", "flow")])
    assert check_edge_overlaps(d) == []
