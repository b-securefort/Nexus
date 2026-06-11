"""Post-layout geometric checks (validation layer 3, advisory).

Operates on a Diagram whose boxes already have absolute geometry (i.e. after
layout_diagram). Self-contained — the algorithm mirrors
validate_drawio._check_edge_passes_through_icon (read-only reference) but works
on IR objects instead of parsed .drawio cells.

Step 1 of the connector-routing work: *measure* how many edges cross a
non-endpoint icon, so routing fixes are verifiable instead of eyeballed.
"""

from __future__ import annotations

from .schema import Container, Diagram, Node

# Shrink an icon's bbox before testing: the orthogonal router clips a few pixels
# of a corner without visibly crossing; only interior hits are worth flagging.
_INSET = 8.0


def _bbox(box: Container | Node) -> tuple[float, float, float, float]:
    return (box.x, box.y, box.x + box.w, box.y + box.h)


def _center(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _l_shape_paths(s, t):
    """The two orthogonal L-routes draw.io chooses between, source→target center."""
    sx, sy = _center(s)
    tx, ty = _center(t)
    return [
        [(sx, sy), (tx, sy), (tx, ty)],   # horizontal-then-vertical
        [(sx, sy), (sx, ty), (tx, ty)],   # vertical-then-horizontal
    ]


def _seg_hits_box(p0, p1, box) -> bool:
    """Axis-aligned segment vs rectangle overlap (our L-paths are axis-aligned)."""
    x1, y1, x2, y2 = box
    (ax, ay), (bx, by) = p0, p1
    if ay == by:  # horizontal
        if not (y1 <= ay <= y2):
            return False
        lo, hi = sorted((ax, bx))
        return lo <= x2 and hi >= x1
    if ax == bx:  # vertical
        if not (x1 <= ax <= x2):
            return False
        lo, hi = sorted((ay, by))
        return lo <= y2 and hi >= y1
    return False  # diagonal — not produced by _l_shape_paths


def _path_blocked(path, box) -> bool:
    return any(_seg_hits_box(path[i], path[i + 1], box) for i in range(len(path) - 1))


def _segs_from_points(points) -> list[tuple[str, float, float, float]]:
    """Axis-aligned segment tuples from an absolute polyline."""
    segs: list[tuple[str, float, float, float]] = []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        if abs(ay - by) <= _COLINEAR_EPS and abs(ax - bx) > _COLINEAR_EPS:
            segs.append(("h", ay, min(ax, bx), max(ax, bx)))
        elif abs(ax - bx) <= _COLINEAR_EPS and abs(ay - by) > _COLINEAR_EPS:
            segs.append(("v", ax, min(ay, by), max(ay, by)))
    return segs


def check_edge_crossings(diagram: Diagram, routes=None) -> list[str]:
    """Return one advisory string per edge whose route crosses a non-endpoint
    icon. With `routes` (per-edge polylines from routing.route_edges) the actual
    routed path is tested; without it, both center L-shapes are tested and an
    edge is flagged only when NO clean L-route exists (conservative proxy)."""
    out: list[str] = []
    nodes = diagram.nodes                       # only icons are obstacles, not containers
    boxes: dict[str, Container | Node] = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in nodes})

    for ei, e in enumerate(diagram.edges):
        src, tgt = boxes.get(e.source), boxes.get(e.target)
        if src is None or tgt is None:
            continue
        if routes is not None:
            r = routes[ei]
            if r is None:
                continue
            paths = [r.points]                  # the single routed path
        else:
            paths = _l_shape_paths(_bbox(src), _bbox(tgt))
        blockers_per_path: list[set[str]] = [set() for _ in paths]
        for n in nodes:
            if n.id in (e.source, e.target):
                continue
            bx = _bbox(n)
            inset = (bx[0] + _INSET, bx[1] + _INSET, bx[2] - _INSET, bx[3] - _INSET)
            if inset[0] >= inset[2] or inset[1] >= inset[3]:
                continue
            for i, p in enumerate(paths):
                if _path_blocked(p, inset):
                    blockers_per_path[i].add(n.id)
        if not all(blockers_per_path):
            continue  # at least one clean route exists → no forced crossing
        common = set.intersection(*blockers_per_path)
        blockers = sorted(common) if common else sorted(set().union(*blockers_per_path))
        label = (e.label[:22] + "…") if len(e.label) > 23 else e.label
        out.append(
            f"[edge-through-icon] edge {e.source}->{e.target}"
            f"{f' ({label!r})' if label else ''}: every orthogonal route crosses "
            f"non-endpoint icon(s) {', '.join(blockers[:3])}. The line will visibly "
            f"cross them — needs routing (gutter waypoints) or tighter placement."
        )
    return out


# An overlap shorter than this is just a shared junction stub, not "one arrow
# hidden behind another" — don't flag it.
_OVERLAP_MIN = 16.0
_COLINEAR_EPS = 1.5


def _route_segments(s, t) -> list[tuple[str, float, float, float]]:
    """Representative orthogonal route, as axis-aligned segments.

    Uses the horizontal-then-vertical L between centers — a fixed convention so
    the metric is a consistent yardstick (like the icon-crossing proxy, it does
    not model draw.io's exact border-to-border routing). Each segment is
    (orientation, fixed_coord, lo, hi): 'h' at y=fixed spanning x∈[lo,hi], or
    'v' at x=fixed spanning y∈[lo,hi]."""
    sx, sy = _center(s)
    tx, ty = _center(t)
    segs: list[tuple[str, float, float, float]] = []
    if abs(tx - sx) > _COLINEAR_EPS:
        segs.append(("h", sy, min(sx, tx), max(sx, tx)))
    if abs(ty - sy) > _COLINEAR_EPS:
        segs.append(("v", tx, min(sy, ty), max(sy, ty)))
    return segs


def check_edge_overlaps(diagram: Diagram, routes=None) -> list[str]:
    """Return one advisory per pair of edges that run colinear and overlap for
    more than _OVERLAP_MIN px — i.e. one arrow hides behind another (the 'C'
    case). Transversal crossings (the '+'/'×' case) are intentionally ignored.
    With `routes` (per-edge polylines) the actual routed segments are compared;
    without it, a center-to-center h-first proxy is used."""
    boxes: dict[str, Container | Node] = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})

    seg_routes: list[tuple[object, list, object]] = []
    for ei, e in enumerate(diagram.edges):
        src, tgt = boxes.get(e.source), boxes.get(e.target)
        if src is None or tgt is None:
            continue
        if routes is not None:
            r = routes[ei]
            if r is None:
                continue
            seg_routes.append((e, _segs_from_points(r.points), r))
        else:
            seg_routes.append((e, _route_segments(_bbox(src), _bbox(tgt)), None))

    out: list[str] = []
    for i in range(len(seg_routes)):
        ei, si, ri = seg_routes[i]
        for j in range(i + 1, len(seg_routes)):
            ej, sj, rj = seg_routes[j]
            # A fan bundle's trunk is exactly colinear BY DESIGN — two bundled
            # edges sharing an endpoint render as one line forking, not as one
            # arrow hiding another. Don't flag the intentional overlap.
            if (getattr(ri, "bundled", False) and getattr(rj, "bundled", False)
                    and (ei.source == ej.source or ei.target == ej.target)):
                continue
            if _segments_overlap(si, sj):
                out.append(
                    f"[edge-overlap] edges {ei.source}->{ei.target} and "
                    f"{ej.source}->{ej.target} run colinear and overlap (>"
                    f"{int(_OVERLAP_MIN)}px) — one arrow hides behind the other. "
                    f"Separate them with distinct exit/entry points or gutter lanes."
                )
    return out


def _segments_overlap(segs_a, segs_b) -> bool:
    for oa, fa, loa, hia in segs_a:
        for ob, fb, lob, hib in segs_b:
            if oa == ob and abs(fa - fb) <= _COLINEAR_EPS:
                overlap = min(hia, hib) - max(loa, lob)
                if overlap > _OVERLAP_MIN:
                    return True
    return False


# --- Placement advisory: mid-flow hops drawn far apart ----------------------
#
# The engine never infers position from edges (load-bearing rule), so stage
# PLACEMENT is authored. When the author parks a mid-flow component in a
# far-away stage ("APIM goes in the networking block at the end"), every hop
# through it becomes a canvas-crossing round trip that no router can save.
# This check measures it: nodes get a flow rank (BFS from the entry points
# over flow/private edges), and a rank-adjacent hop drawn further apart than
# _PLACEMENT_FAR is flagged, with a suggested stage order. Advisory only —
# the AUTHOR moves boxes, never the engine.

_PLACEMENT_FAR = 600.0
_FLOWLIKE_TYPES = {"flow", "private"}


def _flow_ranks(diagram: Diagram) -> dict[str, int]:
    """BFS depth from the flow entry points (in-degree-0 nodes), following
    flow/private edges. Telemetry/dns overlays don't define sequence."""
    from collections import deque

    edges = [(e.source, e.target) for e in diagram.edges
             if e.type in _FLOWLIKE_TYPES]
    targets = {t for _, t in edges}
    sources = {s for s, _ in edges}
    roots = sources - targets
    if not roots:
        return {}
    adj: dict[str, list[str]] = {}
    for s, t in edges:
        adj.setdefault(s, []).append(t)

    def _bfs(root: str) -> dict[str, int]:
        rank = {root: 0}
        q = deque([root])
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, ()):
                if nxt not in rank:
                    rank[nxt] = rank[cur] + 1
                    q.append(nxt)
        return rank

    # Rank only the PRIMARY flow (the root that reaches the most nodes).
    # Side stories with their own entry points (an async Logic Apps mailbox
    # loop next to the user-request path) would otherwise pollute both the
    # far-hop detection and the suggested stage order.
    return max((_bfs(r) for r in roots), key=len)


def _spine_stage(box_id: str, boxes: dict) -> str:
    """The outermost DRAWN container a box belongs to, looking through
    invisible bands — i.e. the stage the reader perceives it in."""
    cur = boxes.get(box_id)
    stage = box_id
    while cur is not None and cur.parent and cur.parent in boxes:
        parent = boxes[cur.parent]
        if isinstance(parent, Container) and parent.style != "band":
            stage = parent.id
        cur = parent
    return stage


def check_flow_placement(diagram: Diagram) -> list[str]:
    """Advisory: rank-adjacent hops drawn far apart, plus the traffic-ordered
    stage arrangement that would shorten them. Runs after layout."""
    boxes: dict = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})
    ranks = _flow_ranks(diagram)
    if not ranks:
        return []

    out: list[str] = []
    flagged = 0
    for e in diagram.edges:
        if e.type not in _FLOWLIKE_TYPES:
            continue
        s, t = boxes.get(e.source), boxes.get(e.target)
        if s is None or t is None:
            continue
        rs, rt = ranks.get(e.source), ranks.get(e.target)
        if rs is None or rt is None or abs(rt - rs) > 1:
            continue
        dist = abs((s.x + s.w / 2) - (t.x + t.w / 2)) + abs((s.y + s.h / 2) - (t.y + t.h / 2))
        if dist > _PLACEMENT_FAR:
            flagged += 1
            out.append(
                f"[far-hop] {e.source} (flow position {rs}) -> {e.target} "
                f"(position {rt}) are consecutive hops drawn {dist:.0f}px apart "
                f"— the stage holding '{_spine_stage(e.target, boxes)}' is far "
                f"from the stage holding '{_spine_stage(e.source, boxes)}'."
            )

    if flagged >= 2:
        # Suggest the traffic-ordered arrangement: stages sorted by the mean
        # rank of their flow-connected members.
        stage_ranks: dict[str, list[int]] = {}
        for n in diagram.nodes:
            r = ranks.get(n.id)
            if r is None:
                continue
            stage_ranks.setdefault(_spine_stage(n.id, boxes), []).append(r)
        ordered = sorted(stage_ranks, key=lambda sid: sum(stage_ranks[sid]) / len(stage_ranks[sid]))
        out.append(
            "[placement] Order the spine by traffic position, not category: "
            + " -> ".join(ordered)
            + ". A container hosting a mid-flow hop (e.g. an internal-APIM "
            "VNet) belongs BETWEEN the stages of its neighbors, never at the "
            "end; if one stage's members span very different flow positions, "
            "split it into two stages. Reorder via `edits` (upsert_container "
            "children / parent moves) and re-render."
        )
    return out

# --- E detector: box drawn on box --------------------------------------------
#
# The packer never overlaps boxes, so a box-on-box stack can only come from
# hand-set geometry or (historically) an align_to shift — conv #360 stacked
# the frontend onto the App Gateway and Postgres onto the backend, and the
# A–D scorecard scored the dominant defect ZERO because every detector
# covers lines and text, never box vs box. The layout guard now reverts
# overlapping align shifts; this detector is the scorecard's safety net so a
# regression (or a hand-geometry IR) can never look clean while icons stack.

def check_box_overlaps(diagram: Diagram) -> list[str]:
    from .layout import _ALIGN_TOL, _visual_rect

    boxes: dict = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})

    def lineage(b) -> set:
        out, cur = set(), b
        while cur.parent and cur.parent in boxes:
            out.add(cur.parent)
            cur = boxes[cur.parent]
        return out

    visible = [b for b in (*diagram.containers, *diagram.nodes)
               if not (isinstance(b, Container) and b.style == "band")]
    rects = {b.id: _visual_rect(b) for b in visible}
    ancestry = {b.id: lineage(b) for b in visible}

    out: list[str] = []
    for i, a in enumerate(visible):
        for b in visible[i + 1:]:
            if a.id in ancestry[b.id] or b.id in ancestry[a.id]:
                continue
            ra, rb = rects[a.id], rects[b.id]
            ox = min(ra[2], rb[2]) - max(ra[0], rb[0])
            oy = min(ra[3], rb[3]) - max(ra[1], rb[1])
            if ox > _ALIGN_TOL and oy > _ALIGN_TOL:
                hint = next((f" — drop or retarget '{x.id}'.align_to" for x in (a, b)
                             if getattr(x, "align_to", None)), " — restructure or spread the layout")
                out.append(
                    f"[box-overlap] {a.id} overlaps {b.id} ({ox:.0f}x{oy:.0f}px); "
                    f"boxes must never sit on each other{hint}."
                )
    return out


# --- Backward-hop advisory: the flow chain reverses reading direction -------
#
# Conv #359: "all private endpoints in one box" was authored BEFORE the app
# tier it serves, so the chain frontend → pe → backend → pe → db zigzagged
# right-left-right across the canvas. check_flow_placement never fired — the
# hops were stage-adjacent, far under _PLACEMENT_FAR — because it measures
# DISTANCE, not direction. This flags a rank-adjacent flow hop drawn AGAINST
# the diagram's reading axis when the target is a TRANSIT node (it continues
# the flow onward). A backward edge into a terminal side-service (auth call
# into Entra, an MI lookup) is a normal side-call and stays unflagged, as is
# small in-stage wobble (aws_complex draws a legitimate 98px ALB->ECS
# reversal inside one VPC; the conv #359 zigzags were 250px+). Advisory only
# — the AUTHOR moves boxes, never the engine.

_BACKWARD_MIN = 120.0


def check_backward_hop(diagram: Diagram) -> list[str]:
    boxes: dict = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})
    ranks = _flow_ranks(diagram)
    if not ranks:
        return []
    onward: set[str] = set()
    for e in diagram.edges:
        if e.type in _FLOWLIKE_TYPES:
            r1, r2 = ranks.get(e.source), ranks.get(e.target)
            if r1 is not None and r2 is not None and r2 > r1:
                onward.add(e.source)

    out: list[str] = []
    for e in diagram.edges:
        if e.type not in _FLOWLIKE_TYPES:
            continue
        rs, rt = ranks.get(e.source), ranks.get(e.target)
        if rs is None or rt is None or rt != rs + 1:
            continue
        if e.target not in onward:        # terminal side-call, not a hop
            continue
        s, t = boxes.get(e.source), boxes.get(e.target)
        if s is None or t is None:
            continue
        delta = ((t.x + t.w / 2) - (s.x + s.w / 2)) if diagram.direction == "LR" \
            else ((t.y + t.h / 2) - (s.y + s.h / 2))
        if delta < -_BACKWARD_MIN:
            stage_s = _spine_stage(e.source, boxes)
            stage_t = _spine_stage(e.target, boxes)
            if stage_t == stage_s:
                # Same perceived stage (e.g. both inside one VNet): name the
                # immediate boxes — the fix is reordering WITHIN that stage.
                stage_s = getattr(s, "parent", None) or e.source
                stage_t = getattr(t, "parent", None) or e.target
            out.append(
                f"[backward-hop] {e.source} -> {e.target} is hop {rs}->{rt} of "
                f"the flow but is drawn {-delta:.0f}px BACKWARD against the "
                f"{diagram.direction} reading direction — '{stage_t}' comes too "
                f"early on the spine. Move it (or the node) after '{stage_s}' "
                f"via `edits` and re-render."
            )
    return out


# --- Side-lane advisory: shared service buried in a flow stage --------------
#
# Human-diagrammer heuristic (2026-06-10): a node with many to/fro edges that
# is NOT itself a hop on the primary flow (DNS, identity, a shared firewall
# zone) gets pulled OUT of the flow and parked beside it, so its edge fan
# doesn't cross the main path. The engine never moves boxes (load-bearing
# no-edge-driven-placement rule), so — like check_flow_placement — this is an
# advisory that tells the author the structural fix: an invisible `band`
# beside the spine plus `align_to` its busiest counterpart.
# (Conv #355 burned ~6 iterations re-arranging a Private DNS Zones node that
# this advisory would have side-laned in one edit.)

_SIDE_LANE_MIN_DEGREE = 3


def check_side_lane(diagram: Diagram) -> list[str]:
    """Advisory: off-flow node with _SIDE_LANE_MIN_DEGREE+ edges spanning 2+
    other stages, buried in a stage that also hosts primary-flow nodes."""
    boxes: dict = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})
    ranks = _flow_ranks(diagram)

    degree: dict[str, int] = {}
    counterparties: dict[str, set[str]] = {}
    for e in diagram.edges:
        for me, other in ((e.source, e.target), (e.target, e.source)):
            degree[me] = degree.get(me, 0) + 1
            counterparties.setdefault(me, set()).add(other)

    # Which perceived stages host primary-flow nodes (rank ≠ None)?
    flow_stages = {_spine_stage(n.id, boxes) for n in diagram.nodes
                   if ranks.get(n.id) is not None}

    out: list[str] = []
    for n in diagram.nodes:
        if degree.get(n.id, 0) < _SIDE_LANE_MIN_DEGREE:
            continue
        if ranks.get(n.id) is not None:
            continue                      # on the primary flow — it IS a hop
        my_stage = _spine_stage(n.id, boxes)
        if my_stage not in flow_stages:
            continue                      # already a satellite zone of its own
        other_stages = {_spine_stage(c, boxes) for c in counterparties.get(n.id, ())
                        if c in boxes} - {my_stage}
        if len(other_stages) < 2:
            continue
        busiest = max(counterparties[n.id], key=lambda c: degree.get(c, 0), default=None)
        out.append(
            f"[side-lane] '{n.id}' has {degree[n.id]} connections across "
            f"{len(other_stages) + 1} stages but sits inside flow stage "
            f"'{my_stage}' — its edge fan will cross the main path. Pull it "
            f"into a side lane: add an invisible `band` container beside the "
            f"spine, move '{n.id}' there, and `align_to` its busiest "
            f"counterpart (e.g. '{busiest}'). One edit, not per-node nudges."
        )
    return out


#
# A and C only see icons and arrows, so a render where every defect is TEXT
# (edge labels on node captions, lines through container titles) still scores
# A=0/C=0. These two detectors close that blind spot; the label boxes come
# from labels.py — the same estimates routing avoids and the placer uses, so
# fixes and measurements share one geometry model.

# Shrink text boxes before line-crossing tests for the same reason as _INSET on
# icons: a line grazing the very edge of a caption isn't a readability defect.
# MUST be >= routing._STRAIGHT_INSET — the straight-first pass accepts a line
# that clears the box shrunk by that amount, so a smaller inset here would flag
# routes the router legitimately accepted (borderline grazes, not defects).
_TEXT_INSET = 6.0
# Label-on-label overlaps smaller than this area are antialiasing, not defects.
_LABEL_OVERLAP_MIN_AREA = 30.0


def _diag_seg_hits_rect(p0, p1, rect) -> bool:
    """Segment-vs-rect test that also handles diagonal segments (straight
    floating connectors). Reuses the routing module's Liang–Barsky clip."""
    from .routing import _seg_hits_rect
    return _seg_hits_rect(p0, p1, rect)


def check_line_over_labels(diagram: Diagram, routes) -> list[str]:
    """B — one advisory per edge whose routed polyline passes through a node's
    caption text or a container's title text. The edge's own endpoints are
    exempt (a bottom-face exit legitimately leaves through its own caption)."""
    from .labels import adornment_boxes, container_header_box, node_label_box

    out: list[str] = []
    if routes is None:
        return out
    node_labels = [(n.id, n.label, lb) for n in diagram.nodes
                   if (lb := node_label_box(n)) is not None]
    headers = [(c.id, c.label, hb) for c in diagram.containers
               if (hb := container_header_box(c)) is not None]
    adorns = [(b.id, f"{b.id} adornment", box)
              for b in (*diagram.nodes, *diagram.containers)
              for box in adornment_boxes(b)]

    for e, r in zip(diagram.edges, routes):
        if r is None:
            continue
        hits: list[str] = []
        for owner_id, owner_label, box in (*node_labels, *headers, *adorns):
            if owner_id in (e.source, e.target):
                continue
            inset = (box[0] + _TEXT_INSET, box[1] + _TEXT_INSET,
                     box[2] - _TEXT_INSET, box[3] - _TEXT_INSET)
            if inset[0] >= inset[2] or inset[1] >= inset[3]:
                continue
            if any(_diag_seg_hits_rect(r.points[i], r.points[i + 1], inset)
                   for i in range(len(r.points) - 1)):
                hits.append(f"{owner_id} ({owner_label[:24]!r})")
        if hits:
            out.append(
                f"[line-over-label] edge {e.source}->{e.target} runs through the "
                f"label text of {', '.join(hits[:3])} — reroute or spread the "
                f"arrangement so the line clears the caption."
            )
    return out


def check_label_collisions(diagram: Diagram, routes) -> list[str]:
    """D — one advisory per placed edge label whose box overlaps a node
    caption, a container title, an icon, or another edge label. Uses the
    `label_box` the placer wrote onto each RouteInfo; an edge with a label but
    no placement falls back to the polyline midpoint (so the metric measures
    the un-placed baseline too)."""
    from .labels import collect_text_obstacles, edge_label_size

    out: list[str] = []
    if routes is None:
        return out
    static = collect_text_obstacles(diagram)

    label_boxes: list[tuple[object, tuple]] = []
    for e, r in zip(diagram.edges, routes):
        if r is None or not e.label:
            continue
        box = getattr(r, "label_box", None)
        if box is None:
            mid = r.points[len(r.points) // 2] if len(r.points) % 2 else (
                (r.points[len(r.points) // 2 - 1][0] + r.points[len(r.points) // 2][0]) / 2,
                (r.points[len(r.points) // 2 - 1][1] + r.points[len(r.points) // 2][1]) / 2,
            )
            lw, lh = edge_label_size(e.label)
            box = (mid[0] - lw / 2, mid[1] - lh / 2, mid[0] + lw / 2, mid[1] + lh / 2)
        label_boxes.append((e, box))

    def _area(a, b) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        return w * h if (w > 0 and h > 0) else 0.0

    for i, (e, box) in enumerate(label_boxes):
        clashes: list[str] = []
        if any(_area(box, o) > _LABEL_OVERLAP_MIN_AREA for o in static):
            clashes.append("node/container text")
        for j, (e2, box2) in enumerate(label_boxes):
            if j != i and _area(box, box2) > _LABEL_OVERLAP_MIN_AREA:
                clashes.append(f"label of {e2.source}->{e2.target}")
        if clashes:
            label = (e.label[:22] + "…") if len(e.label) > 23 else e.label
            out.append(
                f"[label-collision] label {label!r} on {e.source}->{e.target} "
                f"overlaps {', '.join(clashes[:3])} — shorten the label, drop it "
                f"(the line type may already convey it), or spread the layout."
            )
    return out
