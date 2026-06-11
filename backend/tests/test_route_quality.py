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


class TestSideLaneAdvisory:
    """check_side_lane: a high-degree off-flow node buried in a flow stage
    (the conv #355 Private DNS Zones pattern) gets the band+align_to recipe;
    on-path hubs and dedicated satellite zones stay silent."""

    def _hub_spoke(self) -> Diagram:
        # hub holds fw (on the primary flow) and dns (off-flow, 3 dns edges
        # to nodes living in three other stages).
        return Diagram(
            containers=[
                Container(id="hub", label="Hub", style="zone",
                          children=["fw", "dns"]),
                Container(id="s1", label="Spoke 1", style="zone", children=["n1"]),
                Container(id="s2", label="Spoke 2", style="zone", children=["n2"]),
                Container(id="s3", label="Spoke 3", style="zone", children=["n3"]),
            ],
            nodes=[
                Node(id="user", label="User", icon="azure/mysql"),
                Node(id="fw", label="FW", icon="azure/firewalls", parent="hub"),
                Node(id="dns", label="DNS", icon="azure/dns_zones", parent="hub"),
                Node(id="n1", label="N1", icon="azure/mysql", parent="s1"),
                Node(id="n2", label="N2", icon="azure/mysql", parent="s2"),
                Node(id="n3", label="N3", icon="azure/mysql", parent="s3"),
            ],
            edges=[
                Edge(source="user", target="fw"),
                Edge(source="fw", target="n1"),
                Edge(source="fw", target="n2"),
                Edge(source="dns", target="n1", type="dns"),
                Edge(source="dns", target="n2", type="dns"),
                Edge(source="dns", target="n3", type="dns"),
            ],
        )

    def test_buried_offflow_hub_is_flagged(self):
        from app.diagram_ir.geometry import check_side_lane
        msgs = check_side_lane(self._hub_spoke())
        assert len(msgs) == 1
        assert "'dns'" in msgs[0] and "band" in msgs[0] and "align_to" in msgs[0]

    def test_onpath_hub_is_silent(self):
        # fw has degree 3 but carries the primary flow — it IS a hop.
        from app.diagram_ir.geometry import check_side_lane
        msgs = check_side_lane(self._hub_spoke())
        assert not any("'fw'" in m for m in msgs)

    def test_dedicated_satellite_zone_is_silent(self):
        # Log Analytics alone in its own monitoring container: high degree,
        # off-flow, but already side-laned — no advisory.
        from app.diagram_ir.geometry import check_side_lane
        d = self._hub_spoke()
        d.containers.append(Container(id="mon", label="Monitoring",
                                      style="monitoring", children=["la"]))
        d.nodes.append(Node(id="la", label="Logs",
                            icon="azure/log_analytics_workspaces", parent="mon"))
        d.edges += [Edge(source=n, target="la", type="telemetry")
                    for n in ("n1", "n2", "n3")]
        assert not any("'la'" in m for m in check_side_lane(d))

    def test_dun_fixture_stays_clean(self):
        from app.diagram_ir.geometry import check_side_lane
        d = dun_prod.build()
        layout_diagram(d)
        assert check_side_lane(d) == []


class TestTrunkBundling:
    """Fan-out/fan-in trunk bundling (the human '-E' comb): a blocked fan
    routes as ONE trunk that splits, exactly colinear on the shared run;
    all-straight fans stay straight; the C detector ignores the intentional
    overlap."""

    def _fan_out(self, with_blocker: bool) -> Diagram:
        nodes = [
            Node(id="hub", label="Hub", icon="azure/mysql", x=0, y=200),
            Node(id="t1", label="T1", icon="azure/mysql", x=600, y=0),
            Node(id="t2", label="T2", icon="azure/mysql", x=600, y=200),
            Node(id="t3", label="T3", icon="azure/mysql", x=600, y=400),
        ]
        if with_blocker:
            # Sits on the hub→t1 diagonal so that member can't go straight.
            nodes.append(Node(id="blk", label="Blk", icon="azure/mysql",
                              x=300, y=100))
        return Diagram(nodes=nodes, edges=[
            Edge(source="hub", target="t1"),
            Edge(source="hub", target="t2"),
            Edge(source="hub", target="t3"),
        ])

    def test_blocked_fan_bundles_into_shared_trunk(self):
        d = self._fan_out(with_blocker=True)
        routes = route_edges_gutter(d)
        fan = routes[:3]
        assert all(r is not None and r.bundled for r in fan)
        # One trunk: every member leaves from the SAME point.
        assert len({r.points[0] for r in fan}) == 1

    def test_bundle_overlap_not_flagged_as_hidden_arrow(self):
        from app.diagram_ir.geometry import check_edge_overlaps
        d = self._fan_out(with_blocker=True)
        routes = route_edges_gutter(d)
        assert check_edge_overlaps(d, routes) == []

    def test_clean_straight_fan_stays_straight(self):
        d = self._fan_out(with_blocker=False)
        routes = route_edges_gutter(d)
        assert all(r.straight and not r.bundled for r in routes)

    def test_fan_in_bundles_at_shared_target(self):
        d = Diagram(nodes=[
            Node(id="s1", label="S1", icon="azure/mysql", x=0, y=0),
            Node(id="s2", label="S2", icon="azure/mysql", x=0, y=200),
            Node(id="s3", label="S3", icon="azure/mysql", x=0, y=400),
            Node(id="sink", label="Sink", icon="azure/mysql", x=600, y=200),
            Node(id="blk", label="Blk", icon="azure/mysql", x=300, y=100),
        ], edges=[
            Edge(source="s1", target="sink"),
            Edge(source="s2", target="sink"),
            Edge(source="s3", target="sink"),
        ])
        routes = route_edges_gutter(d)
        assert all(r is not None and r.bundled for r in routes)
        # One trunk INTO the target: every member arrives at the SAME point.
        assert len({r.points[-1] for r in routes}) == 1

    def test_labeled_edges_never_bundle(self):
        d = self._fan_out(with_blocker=True)
        for e in d.edges:
            e.label = "calls"
        routes = route_edges_gutter(d)
        assert not any(r.bundled for r in routes if r is not None)


class TestFlowAxisPortBias:
    """Forward flow edges in the ambiguous-angle band keep the diagram's
    reading axis (LR: right→left ports) instead of flipping vertical; non-flow
    edges and backward edges keep the plain dominant-axis choice."""

    def _pair(self, edge_type: str) -> Diagram:
        # Centers 100px apart in x, 130px in y: |dy| > |dx| but within the
        # 1.6× bias band.
        return Diagram(direction="LR", nodes=[
            Node(id="a", label="A", icon="azure/mysql", x=0, y=0),
            Node(id="b", label="B", icon="azure/mysql", x=100, y=130),
        ], edges=[Edge(source="a", target="b", type=edge_type)])

    def test_forward_flow_edge_keeps_reading_axis(self):
        from app.diagram_ir.routing import route_edges
        r = route_edges(self._pair("flow"))[0]
        assert r.exitX == 1.0 and r.entryX == 0.0      # R → L

    def test_nonflow_edge_keeps_dominant_axis(self):
        from app.diagram_ir.routing import route_edges
        r = route_edges(self._pair("dns"))[0]
        assert r.exitY == 1.0 and r.entryY == 0.0      # B → T

    def test_backward_flow_edge_not_biased(self):
        from app.diagram_ir.routing import route_edges
        d = self._pair("flow")
        d.edges[0] = Edge(source="b", target="a", type="flow")
        r = route_edges(d)[0]
        assert r.exitY == 0.0 and r.entryY == 1.0      # T → B (dominant axis)
