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

    seg_routes: list[tuple[object, list]] = []
    for ei, e in enumerate(diagram.edges):
        src, tgt = boxes.get(e.source), boxes.get(e.target)
        if src is None or tgt is None:
            continue
        if routes is not None:
            r = routes[ei]
            if r is None:
                continue
            seg_routes.append((e, _segs_from_points(r.points)))
        else:
            seg_routes.append((e, _route_segments(_bbox(src), _bbox(tgt))))

    out: list[str] = []
    for i in range(len(seg_routes)):
        ei, si = seg_routes[i]
        for j in range(i + 1, len(seg_routes)):
            ej, sj = seg_routes[j]
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
