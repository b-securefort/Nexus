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

PAD = 16          # inner padding inside a container
GAP = 30          # gap between siblings
HEADER = 26       # top band reserved for a container's label
MARGIN = 30       # canvas margin around the whole diagram
ICON_W = 56
ICON_H = 56
LABEL_H = 22      # space below an icon for its label
CHAR_W = 6.2      # rough per-char width for label-width estimation
LABEL_MAX = 170   # cap on label-driven footprint width


def _label_w(label: str) -> float:
    longest = max((len(line) for line in label.splitlines()), default=0)
    return max(ICON_W, min(longest * CHAR_W, LABEL_MAX))


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

    def measure(box: Container | Node) -> tuple[float, float]:
        if isinstance(box, Node):
            fp = (_label_w(box.label), ICON_H + LABEL_H)
        else:
            sizes = [measure(boxes[cid]) for cid in box.children]
            _, cw, ch = _arrange(sizes, _default_layout(box, diagram.direction), box.grid_cols)
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
        positions, _, _ = _arrange(sizes, _default_layout(box, diagram.direction), box.grid_cols)
        ox, oy = x + PAD, y + _header_for(box)
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
    return diagram


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
