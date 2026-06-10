"""Label geometry + deterministic edge-label placement.

The layout/routing engine knows every box and every polyline, yet draw.io was
left to drop each edge label at the path midpoint — with several long edges
converging, midpoints cluster and the text lands on node labels, container
titles, and each other (the dominant defect in real renders; icons stayed
clean because only icons were modeled). This module makes TEXT first-class
geometry:

  * `node_label_box`       — the text zone under (or inside) a node icon
  * `container_header_box` — the title-text extent in a container's header band
  * `edge_label_size`      — estimated w/h of an edge label string
  * `place_edge_labels`    — for each labeled edge, walk candidate anchors along
    the routed polyline (longest segment first, perpendicular offsets, both
    sides) and pick the first position whose label box collides with nothing;
    falls back to the least-overlap candidate. Results are written onto the
    RouteInfo (`label_t`, `label_offset`, `label_box`) for the emitter and the
    D-detector.

Everything here is estimate-based (CHAR_W-style text metrics, same convention
as layout.py) — good enough to keep text out of text, which is what readers
notice.
"""

from __future__ import annotations

from .layout import CHAR_W, HEADER, LABEL_INSET, _label_w
from .schema import Container, Diagram, Node

Rect = tuple[float, float, float, float]

# Corner-adornment geometry. Owned here (not emit.py) so the emitter, the
# placer, the router, and the detectors all share one set of numbers.
ADORN_SIZE = 24.0
ADORN_PAD = 6.0
_ADORN_LABEL_CHAR_W = 5.2     # adornment labels render small (~10px font)
_ADORN_LABEL_H = 12.0

# Edge-label text metrics (draw.io default edge font ≈ 11px).
EDGE_LABEL_CHAR_W = 5.8
EDGE_LABEL_LINE_H = 15.0
EDGE_LABEL_PAD = 4.0          # white background padding around the text
# Gap between the line and the near edge of the label box.
_LINE_GAP = 4.0
# Node label box height per line (matches layout.LABEL_H for one line).
_NODE_LABEL_LINE_H = 14.0

# shape/* tokens whose label renders INSIDE the shape (flowchart vocabulary),
# so the shape box itself already covers the text. Everything else (azure/*,
# aws/*, shape/cloud, shape/actor) renders the label BELOW the icon.
_INSIDE_LABEL_SHAPES = {
    "process", "subprocess", "decision", "terminator",
    "document", "datastore", "queue", "cylinder",
}


def _label_renders_below(icon_ref: str) -> bool:
    provider, _, name = (icon_ref or "").partition("/")
    if provider == "shape":
        return name not in _INSIDE_LABEL_SHAPES
    return True


def node_label_box(n: Node) -> Rect | None:
    """The text zone a node's label occupies. For below-icon labels this is the
    strip under the icon, as wide as the (capped) label estimate; for
    inside-label shapes the shape box already covers the text → None."""
    if not n.label or not _label_renders_below(n.icon):
        return None
    w = _label_w(n.label)
    lines = n.label.count("\n") + 1
    cx = n.x + n.w / 2
    return (cx - w / 2, n.y + n.h, cx + w / 2, n.y + n.h + lines * _NODE_LABEL_LINE_H)


def container_header_box(c: Container) -> Rect | None:
    """The title-text extent inside a container's header band. Deliberately the
    TEXT extent, not the full band width — edges may legitimately cross the
    band, just not the title."""
    if not c.label:
        return None
    longest = max((len(line) for line in c.label.splitlines()), default=0)
    w = min(longest * CHAR_W + LABEL_INSET, c.w)
    return (c.x + 2.0, c.y, c.x + 2.0 + w, c.y + HEADER)


def adornment_glyph_box(owner: Container | Node, corner: str) -> Rect:
    """Absolute rect of a corner adornment's glyph on its owner box."""
    gx = owner.x + ADORN_PAD if "left" in corner else owner.x + owner.w - ADORN_SIZE - ADORN_PAD
    gy = owner.y + ADORN_PAD if "top" in corner else owner.y + owner.h - ADORN_SIZE - ADORN_PAD
    return (gx, gy, gx + ADORN_SIZE, gy + ADORN_SIZE)


def adornment_boxes(owner: Container | Node) -> list[Rect]:
    """Glyph + label-text rects for every adornment on a box.

    Node adornments render their label to the SIDE of the glyph, pointing away
    from the owner (a below-glyph label lands straight on the owner's icon —
    the 'WAF on Front Door' defect). Container adornments keep the label below
    the glyph: it falls inside the container's header band, which is empty on
    that side. The emitter places labels with the same convention.
    """
    out: list[Rect] = []
    for ad in owner.adornments:
        gx1, gy1, gx2, gy2 = adornment_glyph_box(owner, ad.corner)
        out.append((gx1, gy1, gx2, gy2))
        if not ad.label:
            continue
        w = len(ad.label) * _ADORN_LABEL_CHAR_W + 4
        if isinstance(owner, Node):
            ly = gy1 + (ADORN_SIZE - _ADORN_LABEL_H) / 2
            if "left" in ad.corner:       # label points left, away from the icon
                out.append((gx1 - 2 - w, ly, gx1 - 2, ly + _ADORN_LABEL_H))
            else:                          # label points right
                out.append((gx2 + 2, ly, gx2 + 2 + w, ly + _ADORN_LABEL_H))
        else:
            cx = (gx1 + gx2) / 2
            out.append((cx - w / 2, gy2, cx + w / 2, gy2 + _ADORN_LABEL_H))
    return out


def edge_label_size(label: str) -> tuple[float, float]:
    lines = label.splitlines() or [""]
    longest = max(len(line) for line in lines)
    return (
        longest * EDGE_LABEL_CHAR_W + 2 * EDGE_LABEL_PAD,
        len(lines) * EDGE_LABEL_LINE_H + EDGE_LABEL_PAD,
    )


def collect_text_obstacles(diagram: Diagram) -> list[Rect]:
    """Every box a label must not land on: icons, node-label text zones,
    container title text, and adornment glyphs/labels."""
    obs: list[Rect] = []
    for n in diagram.nodes:
        obs.append((n.x, n.y, n.x + n.w, n.y + n.h))
        lb = node_label_box(n)
        if lb is not None:
            obs.append(lb)
        obs.extend(adornment_boxes(n))
    for c in diagram.containers:
        hb = container_header_box(c)
        if hb is not None:
            obs.append(hb)
        obs.extend(adornment_boxes(c))
    return obs


def _overlap_area(a: Rect, b: Rect) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0.0


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(
        abs(bx - ax) + abs(by - ay) if (ax == bx or ay == by)
        else ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        for (ax, ay), (bx, by) in zip(points, points[1:])
    )


def _seg_len(p0, p1) -> float:
    return ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5


def _candidates_for_segment(p0, p1, lw: float, lh: float):
    """Candidate label-center positions for one segment: fractions along it ×
    both perpendicular sides. Yields (anchor, center) pairs where `anchor` is
    the on-segment point (drives the draw.io relative-t) and `center` is the
    label-box center after the perpendicular offset."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = _seg_len(p0, p1)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    # Unit normal (perpendicular).
    nx, ny = -uy, ux
    # Offset distance: clear the line by half the label's extent along the
    # normal direction plus a small gap.
    off = abs(nx) * (lw / 2) + abs(ny) * (lh / 2) + _LINE_GAP
    for frac in (0.5, 0.35, 0.65, 0.2, 0.8):
        ax, ay = p0[0] + dx * frac, p0[1] + dy * frac
        for side in (1.0, -1.0):
            yield (ax, ay), (ax + nx * off * side, ay + ny * off * side)


def place_edge_labels(diagram: Diagram, routes) -> None:
    """Choose a collision-free position for every labeled edge's text and write
    it onto the RouteInfo. Mutates `routes` in place.

    Greedy in edge order: earlier labels become obstacles for later ones.
    When no candidate is fully clean the least-overlap one wins — still far
    better than the midpoint pile-up.
    """
    obstacles = collect_text_obstacles(diagram)
    placed: list[Rect] = []

    for e, r in zip(diagram.edges, routes):
        if r is None or not e.label:
            continue
        lw, lh = edge_label_size(e.label)
        points = r.points
        total = _polyline_length(points)
        if total < 1e-6:
            continue

        # Segments longest-first: a long run has the most room for text.
        seg_order = sorted(
            range(len(points) - 1),
            key=lambda k: _seg_len(points[k], points[k + 1]),
            reverse=True,
        )
        # Arc length up to the start of each segment (for the relative-t).
        arc_before: list[float] = [0.0]
        for k in range(len(points) - 1):
            arc_before.append(arc_before[-1] + _seg_len(points[k], points[k + 1]))

        best = None  # (score, anchor, center, arc_at_anchor)
        for k in seg_order:
            p0, p1 = points[k], points[k + 1]
            for anchor, center in _candidates_for_segment(p0, p1, lw, lh):
                box: Rect = (
                    center[0] - lw / 2, center[1] - lh / 2,
                    center[0] + lw / 2, center[1] + lh / 2,
                )
                score = sum(_overlap_area(box, o) for o in obstacles)
                score += sum(_overlap_area(box, p) for p in placed)
                if score == 0.0:
                    best = (0.0, anchor, center, arc_before[k] + _seg_len(p0, anchor))
                    break
                if best is None or score < best[0]:
                    best = (score, anchor, center, arc_before[k] + _seg_len(p0, anchor))
            if best is not None and best[0] == 0.0:
                break

        if best is None:
            continue
        _, anchor, center, arc = best
        box = (center[0] - lw / 2, center[1] - lh / 2,
               center[0] + lw / 2, center[1] + lh / 2)
        placed.append(box)
        # draw.io edge-label geometry: x ∈ [-1, 1] along the edge (0 = middle),
        # plus an absolute pixel offset point. The offset (center − anchor) is
        # sign-safe because it is applied unrotated.
        r.label_t = max(-1.0, min(1.0, 2.0 * (arc / total) - 1.0))
        r.label_offset = (center[0] - anchor[0], center[1] - anchor[1])
        r.label_box = box
