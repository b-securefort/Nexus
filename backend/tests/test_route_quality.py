"""Route-quality tests: edges should travel the whitespace gutters BETWEEN
containers, not through unrelated boxes or along their borders.

Born from the fft_prod_traffic_flow review (2026-06-10): a render scored
A=B=C=D=0 yet looked wrong — six long edges spent 3,494px combined INSIDE
containers they had nothing to do with, hugging borders and forming a
parallel loom. The A* cost model now penalizes foreign-interior transit and
border-hugging, adds gutter corridor lines to the grid, and rejects straight
connectors that cut long paths through someone else's box.
"""

from app.diagram_ir.examples import dun_prod
from app.diagram_ir.layout import layout_diagram
from app.diagram_ir.routing import (
    _ancestor_ids,
    _transit_len,
    _visible_rects,
    route_edges_gutter,
)
from app.diagram_ir.schema import Container, Diagram, Edge, Node


def _foreign_transit(d: Diagram, routes) -> float:
    boxes = {b.id: b for b in (*d.containers, *d.nodes)}
    visible = _visible_rects(d)
    total = 0.0
    for e, r in zip(d.edges, routes):
        if r is None:
            continue
        allowed = _ancestor_ids(e.source, boxes) | _ancestor_ids(e.target, boxes)
        total += sum(
            _transit_len(r.points[k], r.points[k + 1], rect)
            for k in range(len(r.points) - 1)
            for cid, rect in visible.items() if cid not in allowed
        )
    return total


def _wall_scene(zone_y1: float, zone_y2: float) -> Diagram:
    """A → B with an unrelated visible zone between them (hand-set geometry).
    The zone holds its own node so it isn't empty; A/B are outside it."""
    return Diagram(
        containers=[Container(id="zone", label="Other things", style="zone",
                              children=["inner"],
                              x=200, y=zone_y1, w=400, h=zone_y2 - zone_y1)],
        nodes=[
            Node(id="a", label="A", icon="azure/mysql", x=0, y=80),
            Node(id="b", label="B", icon="azure/mysql", x=800, y=80),
            Node(id="inner", label="Inner", icon="azure/mysql", parent="zone",
                 x=372, y=(zone_y1 + zone_y2) / 2 - 28),
        ],
        edges=[Edge(source="a", target="b")],
    )


class TestForeignInteriorAvoidance:
    def test_routes_around_when_gutter_exists(self):
        # Zone from y=60..360; A/B centers at y=108. The straight line would
        # spend 400px inside the zone; the gutter above (y≈48) is cheap.
        d = _wall_scene(60, 360)
        routes = route_edges_gutter(d)
        r = routes[0]
        assert r is not None and not r.straight
        assert _foreign_transit(d, routes) < 50.0

    def test_still_crosses_when_detour_is_absurd(self):
        # Penalty, not prohibition: a wall spanning ±2000px makes the detour
        # far costlier than crossing — the edge must still get through.
        d = _wall_scene(-2000, 2000)
        routes = route_edges_gutter(d)
        r = routes[0]
        assert r is not None
        assert _foreign_transit(d, routes) > 300.0   # crossed the wall

    def test_straight_rejected_for_long_foreign_transit(self):
        # The diagonal clears every icon/caption, but spends ~400px inside the
        # foreign zone — that must fall back to a routed (non-straight) edge.
        d = _wall_scene(60, 360)
        # Move the inner node out of the line of sight so only the transit
        # rule can reject straightness.
        inner = next(n for n in d.nodes if n.id == "inner")
        inner.x, inner.y = 372, 280
        routes = route_edges_gutter(d)
        assert routes[0] is not None
        assert routes[0].straight is False


class TestRouteQualityRegression:
    def test_dun_prod_foreign_transit_stays_bounded(self):
        """Fixture guard: measured 310px after the cost model landed (the
        pre-model router was structurally similar for this topology). A big
        jump means a routing change regressed gutter preference."""
        d = dun_prod.build()
        layout_diagram(d)
        routes = route_edges_gutter(d)
        assert _foreign_transit(d, routes) < 500.0


class TestFlowPlacementAdvisory:
    """check_flow_placement: rank-adjacent hops drawn far apart get flagged
    with a suggested traffic-ordered spine; good layouts stay silent."""

    def _chain(self, positions: dict[str, tuple[float, float]]) -> Diagram:
        """a -> b -> c at hand-set positions (top-level nodes = stages)."""
        return Diagram(
            nodes=[Node(id=i, label=i.upper(), icon="azure/mysql",
                        x=x, y=y) for i, (x, y) in positions.items()],
            edges=[Edge(source="a", target="b"), Edge(source="b", target="c")],
        )

    def test_far_midflow_hop_is_flagged_with_suggestion(self):
        from app.diagram_ir.geometry import check_flow_placement
        # b (hop 1) parked 900px right while c (hop 2) is back at the left —
        # the APIM-in-the-tail-block pattern in miniature.
        d = self._chain({"a": (0, 0), "b": (900, 0), "c": (0, 150)})
        msgs = check_flow_placement(d)
        assert sum("[far-hop]" in m for m in msgs) == 2
        assert any("[placement]" in m and "a -> " in m for m in msgs)

    def test_adjacent_layout_is_silent(self):
        from app.diagram_ir.geometry import check_flow_placement
        d = self._chain({"a": (0, 0), "b": (150, 0), "c": (300, 0)})
        assert check_flow_placement(d) == []

    def test_side_flows_do_not_pollute_ranks(self):
        from app.diagram_ir.geometry import check_flow_placement
        # Primary chain a->b->c drawn tight; a separate 2-node side story
        # (x -> y) drawn 1000px apart must NOT be flagged — it has its own
        # entry point and isn't part of the primary flow.
        d = self._chain({"a": (0, 0), "b": (150, 0), "c": (300, 0)})
        d.nodes += [Node(id="x", label="X", icon="azure/mysql", x=0, y=400),
                    Node(id="y", label="Y", icon="azure/mysql", x=1100, y=400)]
        d.edges += [Edge(source="x", target="y")]
        assert check_flow_placement(d) == []

    def test_telemetry_edges_never_flagged(self):
        from app.diagram_ir.geometry import check_flow_placement
        d = self._chain({"a": (0, 0), "b": (150, 0), "c": (300, 0)})
        d.nodes.append(Node(id="appi", label="Insights",
                            icon="azure/application_insights", x=1200, y=0))
        d.edges.append(Edge(source="c", target="appi", type="telemetry"))
        assert check_flow_placement(d) == []

    def test_dun_fixture_stays_clean(self):
        from app.diagram_ir.geometry import check_flow_placement
        d = dun_prod.build()
        layout_diagram(d)
        assert check_flow_placement(d) == []
