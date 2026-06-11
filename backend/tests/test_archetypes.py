"""Archetype skeletons must stay detector-clean.

The KB doc kb/patterns/diagram-archetypes.md is what the agent copies as a
blueprint base, so every skeleton in it must load, validate, lay out, route,
and pass every geometry detector with zero defects — a skeleton that starts
life with collisions poisons every diagram derived from it.
"""

import pytest

from app.diagram_ir.archetypes import load_archetypes
from app.diagram_ir.emit import emit_drawio
from app.diagram_ir.geometry import (
    check_backward_hop,
    check_box_overlaps,
    check_edge_crossings,
    check_edge_overlaps,
    check_flow_placement,
    check_label_collisions,
    check_line_over_labels,
    check_side_lane,
)
from app.diagram_ir.labels import place_edge_labels
from app.diagram_ir.layout import layout_diagram
from app.diagram_ir.loader import load_ir
from app.diagram_ir.routing import route_edges_gutter
from app.diagram_ir.validate import validate_ir

ARCHETYPES = load_archetypes()

EXPECTED = {
    "n-tier-web-app", "hub-spoke-network", "hub-spoke-workload",
    "event-driven", "rag-ai-app", "cicd-flow", "landing-zone",
}


def test_library_is_complete():
    assert set(ARCHETYPES) == EXPECTED


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_archetype_skeleton_is_detector_clean(slug):
    d = load_ir(ARCHETYPES[slug]["ir"])
    v = validate_ir(d)
    assert v.ok, v.report()

    layout_diagram(d)
    routes = route_edges_gutter(d)
    place_edge_labels(d, routes)

    # The full scorecard the generate tool runs: A/B/C/D all zero...
    assert check_edge_crossings(d, routes) == []
    assert check_line_over_labels(d, routes) == []
    assert check_edge_overlaps(d, routes) == []
    assert check_label_collisions(d, routes) == []
    assert check_box_overlaps(d) == []
    # ...and no placement advisories: a template must not ship with the
    # structural smells it exists to prevent.
    assert check_flow_placement(d) == []
    assert check_backward_hop(d) == []
    assert check_side_lane(d) == []

    xml = emit_drawio(d, routes)
    assert xml.startswith("<mxfile")
