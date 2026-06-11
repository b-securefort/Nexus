"""Deterministic box-layout engine: structure → geometry.

Recursive flexbox. Two passes:
  1. measure(box)  — bottom-up: a node's footprint is its icon plus label space;
     a container's footprint is its children arranged per its `layout` hint, plus
     padding and a header band for the label.
  2. place(box, x, y) — top-down: assign absolute coordinates, centering each
     child on the cross axis of its parent's arrangement.

The engine is concept-free: it knows rows/columns/grids and box sizes, nothing
about VNets or PaaS. Placement *intent* comes from the IR's containment + layout
hints (authored by the prompt/style layer). Adornments are ignored here — the
emitter positions them at box corners.
"""

from __future__ import annotations

import math

from .schema import Container, Diagram, Node
from .textmetrics import container_label_width, node_label_width

PAD = 16          # inner padding inside a container
GAP = 30          # gap between siblings
HEADER = 26       # top band reserved for a container's label
MARGIN = 30       # canvas margin around the whole diagram
ICON_W = 56
ICON_H = 56
LABEL_H = 22      # space below an icon for its label
LABEL_MAX = 170   # cap on label-driven footprint width
LABEL_INSET = 12  # left+right text margin for a container's header label
ADORN_CLEAR = 30  # glyph+gap a top-corner adornment steals from the header band


def _label_w(label: str) -> float:
    return max(ICON_W, min(node_label_width(label), LABEL_MAX))


def _container_label_min_w(box: Container) -> float:
    """Min content width so a container never clips its own header label. The
    label is uncapped (unlike node labels): a wide subnet name must fit. A
    top-corner adornment shares the header band, so reserve the glyph width."""
    if not box.label:
        return 0.0
    inset = LABEL_INSET
    if any("top" in a.corner for a in box.adornments):
        inset += ADORN_CLEAR
    return container_label_width(box.label, box.style) + inset


def _default_layout(box: Container, direction: str) -> str:
    if box.layout:
        return box.layout
    return "row" if direction == "LR" else "column"


def _header_for(box: Container) -> float:
    return HEADER if box.label else PAD


def _arrange(sizes: list[tuple[float, float]], layout: str, grid_cols: int):
    """Given child footprint sizes, return (positions, content_w, content_h).

    positions are (x, y) of each child's top-left, relative to the content origin
    (i.e. before adding the parent's PAD/HEADER offset). Children are centered on
    the cross axis so a short icon under a wide sibling still looks aligned.
    """
    if not sizes:
        return [], 0.0, 0.0

    if layout == "grid":
        n = len(sizes)
        cols = grid_cols or max(1, round(math.sqrt(n)))
        rows = math.ceil(n / cols)
        col_w = max(w for w, _ in sizes)
        row_h = max(h for _, h in sizes)
        pos = []
        for i, (w, h) in enumerate(sizes):
            r, c = divmod(i, cols)
            cx = c * (col_w + GAP) + (col_w - w) / 2
            cy = r * (row_h + GAP) + (row_h - h) / 2
            pos.append((cx, cy))
        content_w = cols * col_w + (cols - 1) * GAP
        content_h = rows * row_h + (rows - 1) * GAP
        return pos, content_w, content_h

    if layout == "column":
        content_w = max(w for w, _ in sizes)
        content_h = sum(h for _, h in sizes) + GAP * (len(sizes) - 1)
        pos, cursor = [], 0.0
        for w, h in sizes:
            pos.append(((content_w - w) / 2, cursor))
            cursor += h + GAP
        return pos, content_w, content_h

    # default: row
    content_h = max(h for _, h in sizes)
    content_w = sum(w for w, _ in sizes) + GAP * (len(sizes) - 1)
    pos, cursor = [], 0.0
    for w, h in sizes:
        pos.append((cursor, (content_h - h) / 2))
        cursor += w + GAP
    return pos, content_w, content_h


def layout_diagram(diagram: Diagram) -> Diagram:
    boxes: dict[str, Container | Node] = {}
    for c in diagram.containers:
        boxes[c.id] = c
    for n in diagram.nodes:
        boxes[n.id] = n

    footprint: dict[str, tuple[float, float]] = {}
    content_dims: dict[str, tuple[float, float]] = {}   # effective inner content w/h per container

    def measure(box: Container | Node) -> tuple[float, float]:
        if isinstance(box, Node):
            fp = (_label_w(box.label), ICON_H + LABEL_H)
        else:
            sizes = [measure(boxes[cid]) for cid in box.children]
            _, cw, ch = _arrange(sizes, _default_layout(box, diagram.direction), box.grid_cols)
            cw = max(cw, _container_label_min_w(box))   # never clip the container's own label
            content_dims[box.id] = (cw, ch)
            fp = (cw + 2 * PAD, ch + _header_for(box) + PAD)
            box.w, box.h = fp           # container cell == its footprint
        footprint[box.id] = fp
        return fp

    def place(box: Container | Node, x: float, y: float) -> None:
        """x, y = top-left of this box's *footprint* slot (absolute)."""
        if isinstance(box, Node):
            fw, _ = footprint[box.id]
            box.x = x + (fw - ICON_W) / 2     # center icon under its label-width slot
            box.y = y
            box.w, box.h = ICON_W, ICON_H
            return
        box.x, box.y = x, y
        sizes = [footprint[cid] for cid in box.children]
        positions, cw0, _ = _arrange(sizes, _default_layout(box, diagram.direction), box.grid_cols)
        eff_cw, _ = content_dims[box.id]
        shift = (eff_cw - cw0) / 2      # center the children block when a label widened the box
        ox, oy = x + PAD + shift, y + _header_for(box)
        for cid, (rx, ry) in zip(box.children, positions):
            place(boxes[cid], ox + rx, oy + ry)

    # Implicit root: arrange all top-level boxes (parent is None).
    top = [b for b in (diagram.containers + diagram.nodes) if not b.parent]
    # Preserve author order: containers then nodes is wrong; use declared order.
    top = _ordered_top(diagram)
    top_sizes = [measure(b) for b in top]
    positions, _, _ = _arrange(top_sizes, "row" if diagram.direction == "LR" else "column", 0)
    for b, (rx, ry) in zip(top, positions):
        place(b, MARGIN + rx, MARGIN + ry)

    _apply_alignments(diagram, boxes)
    return diagram


def _translate(box: Container | Node, boxes: dict, dx: float, dy: float) -> None:
    box.x += dx
    box.y += dy
    if isinstance(box, Container):
        for cid in box.children:
            _translate(boxes[cid], boxes, dx, dy)


def _apply_alignments(diagram: Diagram, boxes: dict) -> None:
    """Post-placement pass for `align_to` hints: shift a satellite so its center
    sits over the target's center on the axis perpendicular to the main flow
    (X for LR, Y for TB). The whole subtree moves together; bands draw nothing,
    so a satellite leaving its layout band's bounds is invisible. Clamped to the
    canvas margin so a target near the edge can't push the satellite off-canvas.

    A shift that would land the satellite ON another box is REVERTED: the
    packer never overlaps boxes, so align_to was the only way to draw one icon
    on another (conv #360 chained four align_to hints and stacked the frontend
    onto the App Gateway and Postgres onto the backend). An align that can't
    be honored cleanly is dropped, not half-applied."""
    perp_x = diagram.direction == "LR"
    aligned: list[Container | Node] = []
    for b in diagram.containers + diagram.nodes:
        tgt = boxes.get(b.align_to) if b.align_to else None
        if tgt is None or tgt is b:
            continue
        # align_to is for a satellite in ANOTHER band pointing at the main-flow
        # element it serves. Aligning a same-parent sibling just stacks the two on
        # the cross axis (they already share it) — that collapses any chain through
        # them into one colinear line (the "parallel lines to the same place" look)
        # and overlaps their labels. Ignore it; validate.py warns the author.
        if b.parent is not None and b.parent == tgt.parent:
            continue
        if perp_x:
            delta = (tgt.x + tgt.w / 2) - (b.x + b.w / 2)
            delta = max(delta, MARGIN - b.x)        # don't cross the left margin
            _translate(b, boxes, delta, 0)
            if _align_collides(b, diagram, boxes):
                _translate(b, boxes, -delta, 0)
                continue
        else:
            delta = (tgt.y + tgt.h / 2) - (b.y + b.h / 2)
            delta = max(delta, MARGIN - b.y)        # don't cross the top margin
            _translate(b, boxes, 0, delta)
            if _align_collides(b, diagram, boxes):
                _translate(b, boxes, 0, -delta)
                continue
        aligned.append(b)
    _spread_aligned(aligned, boxes, perp_x)


# A graze under this many px on either axis isn't a visible stack.
_ALIGN_TOL = 6.0


def _visual_rect(b: Container | Node) -> tuple[float, float, float, float]:
    """The box plus, for nodes, the caption strip below the icon — captions are
    what visibly collide first when two icons approach each other."""
    if isinstance(b, Node) and b.label:
        lw = _label_w(b.label)
        x1 = min(b.x, b.x + (b.w - lw) / 2)
        x2 = max(b.x + b.w, b.x + (b.w + lw) / 2)
        return (x1, b.y, x2, b.y + b.h + LABEL_H)
    return (b.x, b.y, b.x + b.w, b.y + b.h)


def _align_collides(b: Container | Node, diagram: Diagram, boxes: dict) -> bool:
    """Would `b` (post-shift) visibly overlap any box outside its own subtree
    and ancestry? Invisible bands aren't obstacles; their children are."""
    skip = {b.id}
    if isinstance(b, Container):
        frontier = list(b.children)
        while frontier:
            cid = frontier.pop()
            skip.add(cid)
            kid = boxes.get(cid)
            if isinstance(kid, Container):
                frontier.extend(kid.children)
    cur = b
    while cur.parent and cur.parent in boxes:
        skip.add(cur.parent)
        cur = boxes[cur.parent]

    r = _visual_rect(b)
    for o in (*diagram.containers, *diagram.nodes):
        if o.id in skip or (isinstance(o, Container) and o.style == "band"):
            continue
        ro = _visual_rect(o)
        ox = min(r[2], ro[2]) - max(r[0], ro[0])
        oy = min(r[3], ro[3]) - max(r[1], ro[1])
        if ox > _ALIGN_TOL and oy > _ALIGN_TOL:
            return True
    return False


def _spread_aligned(aligned: list, boxes: dict, perp_x: bool) -> None:
    """When two satellites target nearby elements they land on top of each other.
    Keep each as close to its aligned position as possible but enforce a GAP:
    sweep siblings (same parent) in order and push later ones along the band."""
    by_parent: dict = {}
    for b in aligned:
        by_parent.setdefault(b.parent, []).append(b)
    for sibs in by_parent.values():
        if len(sibs) < 2:
            continue
        if perp_x:
            sibs.sort(key=lambda b: b.x)
            for prev, cur in zip(sibs, sibs[1:]):
                overlap = (prev.x + prev.w + GAP) - cur.x
                if overlap > 0:
                    _translate(cur, boxes, overlap, 0)
        else:
            sibs.sort(key=lambda b: b.y)
            for prev, cur in zip(sibs, sibs[1:]):
                overlap = (prev.y + prev.h + GAP) - cur.y
                if overlap > 0:
                    _translate(cur, boxes, 0, overlap)


def _ordered_top(diagram: Diagram) -> list[Container | Node]:
    """Top-level boxes in author order (nodes and containers interleaved as listed)."""
    out: list[Container | Node] = []
    for c in diagram.containers:
        if not c.parent:
            out.append(c)
    for n in diagram.nodes:
        if not n.parent:
            out.append(n)
    return out
