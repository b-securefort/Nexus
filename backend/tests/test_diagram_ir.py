"""Tests for the structural Diagram IR: loader (schema), validator (ref-integrity),
layout engine, and emitter."""

import pytest

from app.diagram_ir.emit import emit_drawio
from app.diagram_ir.examples import image5_auto
from app.diagram_ir.layout import layout_diagram
from app.diagram_ir.loader import IRSchemaError, load_ir
from app.diagram_ir.schema import Container, Diagram, Edge, Node
from app.diagram_ir.validate import validate_ir


# --- valid reference IR ---

def test_image5_auto_validates_clean():
    v = validate_ir(image5_auto.build())
    assert v.ok, v.report()
    assert not v.warnings, v.report()


def test_image5_auto_full_pipeline_renders_xml():
    d = layout_diagram(image5_auto.build())
    xml = emit_drawio(d)
    assert xml.startswith("<mxfile")
    assert "img/lib/azure2/app_services/App_Services.svg" in xml  # icon resolved
    # layout assigned real geometry (no zeros left on the VNet)
    vnet = next(c for c in d.containers if c.id == "vnet")
    assert vnet.w > 0 and vnet.h > 0 and vnet.x > 0


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
