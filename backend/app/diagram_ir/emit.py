"""Emit a Diagram IR to draw.io XML.

Walking-skeleton scope: geometry must already be present on the IR (hand-set).
This proves the IR → .drawio → Azure2-icon pipeline before any layout engine
exists. The only computation here is absolute → parent-relative coordinate
conversion (draw.io child geometry is relative to the immediate parent).
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .catalog import container_style, edge_style, icon_style
from .labels import ADORN_PAD as _ADORN_PAD, ADORN_SIZE as _ADORN_SIZE
from .schema import Adornment, Container, Diagram, Node


def _esc(s: str) -> str:
    return escape(s or "", {'"': "&quot;"})


def emit_drawio(diagram: Diagram, routes=None) -> str:
    # Index every box by id so we can resolve a child's parent origin.
    boxes: dict[str, Container | Node] = {}
    for c in diagram.containers:
        boxes[c.id] = c
    for n in diagram.nodes:
        boxes[n.id] = n

    def parent_origin(box: Container | Node) -> tuple[float, float]:
        p = box.parent
        if p and p in boxes:
            par = boxes[p]
            return par.x, par.y
        return 0.0, 0.0

    cells: list[str] = []

    def emit_box(box: Container | Node, style: str, value: str) -> None:
        ox, oy = parent_origin(box)
        rx, ry = box.x - ox, box.y - oy
        parent_id = box.parent if (box.parent and box.parent in boxes) else "1"
        cells.append(
            f'<mxCell id="{_esc(box.id)}" value="{_esc(value)}" style="{style}" '
            f'vertex="1" parent="{_esc(parent_id)}">'
            f'<mxGeometry x="{rx:g}" y="{ry:g}" width="{box.w:g}" height="{box.h:g}" as="geometry"/>'
            f"</mxCell>"
        )
        for ad in box.adornments:
            _emit_adornment(box, ad)

    def _emit_adornment(owner: Container | Node, ad: Adornment) -> None:
        # Positioned relative to the OWNER box (parent = owner.id).
        if "left" in ad.corner:
            ax = _ADORN_PAD
        else:
            ax = owner.w - _ADORN_SIZE - _ADORN_PAD
        if "top" in ad.corner:
            ay = _ADORN_PAD
        else:
            ay = owner.h - _ADORN_SIZE - _ADORN_PAD
        style = icon_style(ad.icon)
        # On a NODE the default below-glyph label lands straight on the owner's
        # icon ('WAF' over Front Door). Push it to the side, pointing away from
        # the icon; later duplicate style keys win in draw.io, so appending
        # overrides the icon style's verticalLabelPosition=bottom. Containers
        # keep the below-glyph label — it falls in the empty header band.
        # labels.adornment_boxes mirrors this convention for the detectors.
        if isinstance(owner, Node) and ad.label:
            if "left" in ad.corner:
                style += "labelPosition=left;align=right;verticalLabelPosition=middle;verticalAlign=middle;fontSize=10;"
            else:
                style += "labelPosition=right;align=left;verticalLabelPosition=middle;verticalAlign=middle;fontSize=10;"
        cells.append(
            f'<mxCell id="{_esc(owner.id)}__adorn_{_esc(ad.corner)}" value="{_esc(ad.label)}" '
            f'style="{style}" vertex="1" parent="{_esc(owner.id)}">'
            f'<mxGeometry x="{ax:g}" y="{ay:g}" width="{_ADORN_SIZE}" height="{_ADORN_SIZE}" as="geometry"/>'
            f"</mxCell>"
        )

    # Containers before their children so parents exist first. Sort by nesting
    # depth (root containers first) — draw.io tolerates any order, but this keeps
    # the XML readable.
    def depth(box: Container | Node) -> int:
        d, p, seen = 0, box.parent, set()
        while p and p in boxes and p not in seen:
            seen.add(p)
            d += 1
            p = boxes[p].parent
        return d

    for c in sorted(diagram.containers, key=depth):
        emit_box(c, container_style(c.style), c.label)
    for n in diagram.nodes:
        emit_box(n, icon_style(n.icon), n.label)

    for i, e in enumerate(diagram.edges):
        style = edge_style(e.type)
        geometry = '<mxGeometry relative="1" as="geometry"/>'
        if routes is not None and routes[i] is not None:
            r = routes[i]
            if r.straight:
                # Straight, drag-to-edit connector: drop the orthogonal router and
                # (when floating) the fixed connection points, so draw.io draws one
                # direct border-to-border line with no waypoints to wrangle.
                style = style.replace("edgeStyle=orthogonalEdgeStyle;", "")
                if not r.floating:
                    style += (
                        f"exitX={r.exitX:g};exitY={r.exitY:g};exitDx=0;exitDy=0;"
                        f"entryX={r.entryX:g};entryY={r.entryY:g};entryDx=0;entryDy=0;"
                    )
            else:
                style += (
                    f"exitX={r.exitX:g};exitY={r.exitY:g};exitDx=0;exitDy=0;"
                    f"entryX={r.entryX:g};entryY={r.entryY:g};entryDx=0;entryDy=0;"
                )
                if r.waypoints:
                    pts = "".join(f'<mxPoint x="{x:g}" y="{y:g}"/>' for x, y in r.waypoints)
                    geometry = (
                        '<mxGeometry relative="1" as="geometry">'
                        f'<Array as="points">{pts}</Array>'
                        "</mxGeometry>"
                    )
        # Deterministic label placement (labels.place_edge_labels): when the
        # route carries a computed position, the label moves to a child
        # edgeLabel cell at that exact spot (with a white background so the
        # line doesn't strike through the text) and the edge's own value stays
        # empty — otherwise draw.io would render its default midpoint label on
        # top of everything, which is exactly the defect being fixed.
        label_t = getattr(routes[i], "label_t", None) if (routes is not None and routes[i] is not None) else None
        edge_value = "" if (e.label and label_t is not None) else e.label
        label_cell = ""
        if e.label and label_t is not None:
            ox, oy = routes[i].label_offset or (0.0, 0.0)
            label_cell = (
                f'<mxCell id="edge{i}_label" value="{_esc(e.label)}" '
                f'style="edgeLabel;html=1;align=center;verticalAlign=middle;'
                f'resizable=0;points=[];fontSize=10;'
                f'labelBackgroundColor=#FFFFFF;" '
                f'vertex="1" connectable="0" parent="edge{i}">'
                f'<mxGeometry x="{label_t:g}" relative="1" as="geometry">'
                f'<mxPoint x="{ox:g}" y="{oy:g}" as="offset"/>'
                f"</mxGeometry></mxCell>"
            )
        cells.append(
            f'<mxCell id="edge{i}" value="{_esc(edge_value)}" style="{style}" '
            f'edge="1" parent="1" source="{_esc(e.source)}" target="{_esc(e.target)}">'
            f'{geometry}'
            f"</mxCell>"
            + label_cell
        )

    title_cell = ""
    if diagram.title:
        title_cell = (
            f'<mxCell id="title" value="{_esc(diagram.title)}" '
            f'style="text;html=1;strokeColor=none;fillColor=none;align=left;'
            f'verticalAlign=middle;fontStyle=1;fontSize=16;fontColor=#1A1A1A;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="20" y="8" width="500" height="24" as="geometry"/></mxCell>'
        )

    body = title_cell + "".join(cells)
    return (
        '<mxfile host="app.diagrams.net">'
        '<diagram id="d1" name="Page-1">'
        '<mxGraphModel dx="1400" dy="900" grid="0" pageWidth="1100" pageHeight="700" '
        'background="#FFFFFF" math="0" shadow="0">'
        "<root>"
        '<mxCell id="0"/>'
        '<mxCell id="1" parent="0"/>'
        f"{body}"
        "</root>"
        "</mxGraphModel>"
        "</diagram>"
        "</mxfile>"
    )
