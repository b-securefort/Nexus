"""Tests for text-aware diagram quality: label geometry, the B (line-over-label)
and D (label-collision) detectors, the deterministic edge-label placer, and the
emitter's positioned edge-label cells.

Built from the dun_prod_traffic_flow failure mode: a render scored A=0/C=0 yet
was unreadable because every defect was TEXT (edge labels dropped at midpoints
onto node captions and each other, lines through container titles). These tests
pin that the engine now models text.
"""

from app.diagram_ir.emit import emit_drawio
from app.diagram_ir.examples import dun_prod, flow_spine, image5_auto, tiered_tb
from app.diagram_ir.geometry import (
    check_edge_crossings,
    check_edge_overlaps,
    check_label_collisions,
    check_line_over_labels,
)
from app.diagram_ir.labels import (
    container_header_box,
    edge_label_size,
    node_label_box,
    place_edge_labels,
)
from app.diagram_ir.layout import HEADER, LABEL_MAX, layout_diagram
from app.diagram_ir.routing import RouteInfo, route_edges_gutter
from app.diagram_ir.schema import Container, Diagram, Edge, Node


def _rects_overlap(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


# ── Label geometry ─────────────────────────────────────────────────────────

class TestLabelBoxes:
    def test_node_label_box_sits_below_icon_and_fits_text(self):
        n = Node(id="pe", label="Private Endpoint PostgreSQL",
                 icon="azure/private_endpoint", x=100, y=50, w=56, h=56)
        box = node_label_box(n)
        assert box is not None
        x1, y1, x2, y2 = box
        assert y1 == 50 + 56                  # starts at the icon's bottom edge
        assert (x2 - x1) > 56                 # wider than the icon for long text
        assert (x2 - x1) <= LABEL_MAX         # but capped like the layouter
        # centered on the icon
        assert abs((x1 + x2) / 2 - (100 + 28)) < 0.01

    def test_inside_label_shapes_have_no_label_box(self):
        n = Node(id="p", label="Validate order", icon="shape/process",
                 x=0, y=0, w=120, h=48)
        assert node_label_box(n) is None      # the shape box already covers it

    def test_unlabeled_node_has_no_label_box(self):
        n = Node(id="x", label="", icon="azure/mysql", x=0, y=0)
        assert node_label_box(n) is None

    def test_container_header_box_covers_title_text_not_full_band(self):
        c = Container(id="data", label="Data & External Services",
                      style="zone", x=300, y=40, w=600, h=300)
        box = container_header_box(c)
        assert box is not None
        x1, y1, x2, y2 = box
        assert (y1, y2) == (40, 40 + HEADER)
        assert x2 - x1 < 600                  # text extent, not the whole band

    def test_edge_label_size_scales_with_text(self):
        small_w, _ = edge_label_size("HTTPS")
        big_w, _ = edge_label_size("secret database access")
        assert big_w > small_w > 0


# ── B detector: line through label text ────────────────────────────────────

def _three_in_a_row() -> Diagram:
    """a → b with a labeled blocker between them (hand-set geometry)."""
    return Diagram(nodes=[
        Node(id="a", label="A", icon="azure/mysql", x=0, y=0),
        Node(id="blocker", label="Key Vault production", icon="azure/key_vaults",
             x=200, y=0),
        Node(id="b", label="B", icon="azure/mysql", x=440, y=0),
    ], edges=[Edge(source="a", target="b")])


class TestLineOverLabels:
    def test_flags_route_through_caption(self):
        d = _three_in_a_row()
        # Synthetic route running straight through the blocker's caption zone
        # (y just below the icon row, where the label text renders).
        r = RouteInfo(1, 0.5, 0, 0.5, points=[(56, 63), (440, 63)])
        out = check_line_over_labels(d, [r])
        assert len(out) == 1
        assert "blocker" in out[0]

    def test_own_caption_is_exempt(self):
        d = _three_in_a_row()
        d.edges = [Edge(source="a", target="blocker")]
        # Bottom-face exit through a's own caption then across to the blocker.
        r = RouteInfo(0.5, 1, 0.5, 1, points=[(28, 60), (28, 90), (228, 90), (228, 60)])
        out = check_line_over_labels(d, [r])
        assert all("'A'" not in m for m in out)

    def test_router_avoids_captions(self):
        """The gutter router treats the caption zone as an obstacle, so the
        actual routed edge never triggers B."""
        d = _three_in_a_row()
        routes = route_edges_gutter(d)
        assert check_line_over_labels(d, routes) == []
        assert check_edge_crossings(d, routes) == []


# ── D detector + placer: label collisions ──────────────────────────────────

def _crossing_pair() -> Diagram:
    """Two diagonal edges crossing mid-canvas — their midpoint labels land on
    the same spot (the dun_prod pile-up in miniature)."""
    return Diagram(nodes=[
        Node(id="s1", label="S1", icon="azure/mysql", x=0, y=0),
        Node(id="s2", label="S2", icon="azure/mysql", x=0, y=300),
        Node(id="t1", label="T1", icon="azure/mysql", x=500, y=0),
        Node(id="t2", label="T2", icon="azure/mysql", x=500, y=300),
    ], edges=[
        Edge(source="s1", target="t2", label="secret database access"),
        Edge(source="s2", target="t1", label="VNet integration traffic"),
    ])


class TestLabelPlacement:
    def test_midpoint_baseline_collides(self):
        d = _crossing_pair()
        routes = route_edges_gutter(d)
        # BEFORE placement: detector falls back to midpoint boxes → collision.
        for r in routes:
            r.label_box = None
        assert len(check_label_collisions(d, routes)) >= 1

    def test_placer_resolves_the_collision(self):
        d = _crossing_pair()
        routes = route_edges_gutter(d)
        place_edge_labels(d, routes)
        assert check_label_collisions(d, routes) == []
        # Both labels actually got positions
        placed = [r for r in routes if r is not None and r.label_box is not None]
        assert len(placed) == 2
        assert not _rects_overlap(placed[0].label_box, placed[1].label_box)

    def test_placement_writes_drawio_coordinates(self):
        d = _crossing_pair()
        routes = route_edges_gutter(d)
        place_edge_labels(d, routes)
        for r in routes:
            assert r.label_t is not None
            assert -1.0 <= r.label_t <= 1.0
            assert r.label_offset is not None

    def test_unlabeled_edges_are_untouched(self):
        d = _crossing_pair()
        d.edges[0].label = ""
        routes = route_edges_gutter(d)
        place_edge_labels(d, routes)
        assert routes[0].label_t is None
        assert routes[1].label_t is not None


# ── Emitter: positioned edge-label cells ───────────────────────────────────

class TestEmitEdgeLabels:
    def test_placed_label_becomes_child_cell_with_offset(self):
        d = _crossing_pair()
        routes = route_edges_gutter(d)
        place_edge_labels(d, routes)
        xml = emit_drawio(d, routes=routes)
        assert 'id="edge0_label"' in xml
        assert 'parent="edge0"' in xml
        assert 'as="offset"' in xml
        assert "labelBackgroundColor=#FFFFFF" in xml
        # The edge cell itself must not duplicate the label text.
        assert '<mxCell id="edge0" value=""' in xml

    def test_unplaced_label_stays_on_edge_cell(self):
        d = _crossing_pair()
        xml = emit_drawio(d, routes=None)
        assert 'id="edge0_label"' not in xml
        assert 'value="secret database access"' in xml


# ── Full-pipeline regression: reference examples stay clean on all four ────

class TestPipelineScorecard:
    def _assert_clean(self, d: Diagram):
        layout_diagram(d)
        routes = route_edges_gutter(d)
        place_edge_labels(d, routes)
        assert check_edge_crossings(d, routes) == []
        assert check_line_over_labels(d, routes) == []
        assert check_edge_overlaps(d, routes) == []
        assert check_label_collisions(d, routes) == []

    def test_flow_spine_clean(self):
        self._assert_clean(flow_spine.build())

    def test_tiered_tb_clean(self):
        self._assert_clean(tiered_tb.build())

    def test_image5_auto_clean(self):
        self._assert_clean(image5_auto.build())

    def test_dun_prod_regression_clean(self):
        """The real-world failure this work came from: 18 edges, 9 verbose
        labels, a private-networking band below the app flow. Default midpoint
        labels produced 6 collisions; the placed labels must produce none."""
        d = dun_prod.build()
        layout_diagram(d)
        routes = route_edges_gutter(d)
        # Pin the baseline so the fixture keeps reproducing the defect: with
        # midpoint labels this topology collides.
        assert len(check_label_collisions(d, routes)) >= 4
        place_edge_labels(d, routes)
        self_check = (
            check_edge_crossings(d, routes)
            + check_line_over_labels(d, routes)
            + check_edge_overlaps(d, routes)
            + check_label_collisions(d, routes)
        )
        assert self_check == [], "\n".join(self_check)
