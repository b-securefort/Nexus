"""Connector routing — step 2: per-edge exit/entry connection points.

By default draw.io connects edges at box centers, so every edge sharing a box
face leaves/enters on the same line and they hide behind each other (the 'C'
case). Here we pick a face per edge from the relative geometry, then *spread*
edges that share a (box, face): distinct connection points along the face,
ordered by the cross-axis position of the other endpoint so they don't cross at
the box. Output feeds both the emitter (normalized exitX/Y, entryX/Y) and the
detectors (an absolute polyline), so improvements are measurable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import Container, Diagram, Node


@dataclass
class RouteInfo:
    exitX: float
    exitY: float
    entryX: float
    entryY: float
    points: list[tuple[float, float]]   # absolute polyline [exit, ..., entry]
    waypoints: list[tuple[float, float]] = None  # interior bend points for the emitter
    # straight = a direct source→target line with no bends; floating = let draw.io
    # pick the border connection points (no fixed exit/entry) so the line stays a
    # clean, drag-to-edit straight segment. Set together by the straight-first pass.
    straight: bool = False
    floating: bool = False

    def __post_init__(self):
        if self.waypoints is None:
            # default (step-2): the single interior bend, if any
            self.waypoints = self.points[1:-1]


def _center(b):
    return (b.x + b.w / 2, b.y + b.h / 2)


def route_edges(diagram: Diagram, spread: bool = True) -> list[RouteInfo | None]:
    boxes: dict[str, Container | Node] = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})

    # Pass 1 — choose a face per endpoint from dominant axis.
    faces: list[tuple | None] = []
    for e in diagram.edges:
        s, t = boxes.get(e.source), boxes.get(e.target)
        if s is None or t is None:
            faces.append(None)
            continue
        scx, scy = _center(s)
        tcx, tcy = _center(t)
        dx, dy = tcx - scx, tcy - scy
        if abs(dx) >= abs(dy):
            exit_face = "R" if dx >= 0 else "L"
            entry_face = "L" if dx >= 0 else "R"
        else:
            exit_face = "B" if dy >= 0 else "T"
            entry_face = "T" if dy >= 0 else "B"
        faces.append((exit_face, entry_face))

    # Pass 2 — group every connection that lands on a (box, face), counting
    # exits AND entries together (3b): otherwise an edge entering a face and one
    # leaving the SAME face are spread independently and can land on the same
    # point. Spread by the cross-axis position of the other endpoint so the
    # ordering doesn't cross right at the box.
    face_slots: dict[tuple, list[tuple[int, str]]] = {}
    for i, (e, f) in enumerate(zip(diagram.edges, faces)):
        if f is None:
            continue
        face_slots.setdefault((e.source, f[0]), []).append((i, "exit"))
        face_slots.setdefault((e.target, f[1]), []).append((i, "entry"))

    def other_cross(idx: int, role: str, face: str) -> float:
        e = diagram.edges[idx]
        other = boxes[e.target if role == "exit" else e.source]
        ocx, ocy = _center(other)
        return ocy if face in ("R", "L") else ocx

    exit_t: dict[int, float] = {}
    entry_t: dict[int, float] = {}
    for (box_id, face), slots in face_slots.items():
        n = len(slots)
        if not spread or n == 1:
            for idx, role in slots:
                (exit_t if role == "exit" else entry_t)[idx] = 0.5
            continue
        ordered = sorted(slots, key=lambda s: other_cross(s[0], s[1], face))
        for rank, (idx, role) in enumerate(ordered):
            (exit_t if role == "exit" else entry_t)[idx] = (rank + 1) / (n + 1)

    # Pass 3 — assemble normalized points + absolute polyline.
    out: list[RouteInfo | None] = []
    for i, (e, f) in enumerate(zip(diagram.edges, faces)):
        if f is None:
            out.append(None)
            continue
        s, t = boxes[e.source], boxes[e.target]
        ef, nf = f
        et, nt = exit_t[i], entry_t[i]
        ex, ey = _face_point(ef, et)
        nx, ny = _face_point(nf, nt)
        exit_abs = (s.x + ex * s.w, s.y + ey * s.h)
        entry_abs = (t.x + nx * t.w, t.y + ny * t.h)
        if ef in ("R", "L"):          # leave horizontally → horizontal-first L
            bend = (entry_abs[0], exit_abs[1])
        else:                          # leave vertically → vertical-first L
            bend = (exit_abs[0], entry_abs[1])
        out.append(RouteInfo(ex, ey, nx, ny, [exit_abs, bend, entry_abs]))
    return out


def _face_point(face: str, t: float) -> tuple[float, float]:
    """Normalized (x, y) on a box face; t is the position along the face."""
    if face == "R":
        return (1.0, t)
    if face == "L":
        return (0.0, t)
    if face == "B":
        return (t, 1.0)
    return (t, 0.0)  # "T"


# --- Step 3: deterministic gutter router (Hanan-grid A*) -------------------
#
# Route each edge orthogonally through the icon-free space, emitting explicit
# waypoints so the result is deterministic (not draw.io's auto-router). Only
# leaf-node icons are obstacles; container backgrounds are free to cross. The
# edge's own source/target are kept as *exact* (un-inflated) obstacles so the
# line can start/end on their border but never cuts back across them.

import heapq

_CLEARANCE = 10.0   # inflate other icons by this so lines keep their distance
_TURN_PENALTY = 14.0
_EPS = 0.5


def _bbox(b):
    return (b.x, b.y, b.x + b.w, b.y + b.h)


# Straight-first routing: a direct source→target line is preferred (no waypoints,
# trivially editable in draw.io). We only fall back to the orthogonal gutter route
# when that straight line would visibly cut through another icon. An icon is
# shrunk by this inset before the test so a line merely grazing a corner is fine.
_STRAIGHT_INSET = 6.0


def _seg_hits_rect(p0, p1, rect) -> bool:
    """True if segment p0→p1 passes through the interior of axis-aligned `rect`
    (x1, y1, x2, y2). Liang–Barsky clip; handles diagonal segments (the L-shape
    test in geometry.py only handles axis-aligned ones)."""
    x1, y1, x2, y2 = rect
    if x1 >= x2 or y1 >= y2:
        return False
    (ax, ay), (bx, by) = p0, p1
    dx, dy = bx - ax, by - ay
    p = (-dx, dx, -dy, dy)
    q = (ax - x1, x2 - ax, ay - y1, y2 - ay)
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-9:
            if qi < 0:
                return False            # parallel to this edge and outside the slab
        else:
            r = qi / pi
            if pi < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t1 - t0 > 1e-6               # parametric: a real (non-grazing) overlap


def _straight_clear(p0, p1, node_boxes, src, tgt) -> bool:
    for nid, bx in node_boxes.items():
        if nid in (src, tgt):
            continue
        shrunk = (bx[0] + _STRAIGHT_INSET, bx[1] + _STRAIGHT_INSET,
                  bx[2] - _STRAIGHT_INSET, bx[3] - _STRAIGHT_INSET)
        if _seg_hits_rect(p0, p1, shrunk):
            return False
    return True


def _seg_hits_interior(p0, p1, box) -> bool:
    x1, y1, x2, y2 = box
    (ax, ay), (bx, by) = p0, p1
    if abs(ay - by) <= _EPS:                       # horizontal
        if not (y1 + _EPS < ay < y2 - _EPS):
            return False
        lo, hi = sorted((ax, bx))
        return min(hi, x2) - max(lo, x1) > _EPS
    if abs(ax - bx) <= _EPS:                        # vertical
        if not (x1 + _EPS < ax < x2 - _EPS):
            return False
        lo, hi = sorted((ay, by))
        return min(hi, y2) - max(lo, y1) > _EPS
    return False


def _seg_free(p0, p1, obstacles) -> bool:
    return not any(_seg_hits_interior(p0, p1, o) for o in obstacles)


def _coord_lines(vals: set[float]) -> list[float]:
    s = sorted(vals)
    out: list[float] = []
    for i, v in enumerate(s):
        out.append(v)
        if i + 1 < len(s):
            out.append((v + s[i + 1]) / 2)   # channel centre-line between obstacles
    return out


def _astar(start, goal, obstacles, xs, ys):
    xi = {x: i for i, x in enumerate(xs)}
    yi = {y: i for i, y in enumerate(ys)}
    si, sj = xi[start[0]], yi[start[1]]
    gi, gj = xi[goal[0]], yi[goal[1]]

    def h(i, j):
        return abs(xs[i] - goal[0]) + abs(ys[j] - goal[1])

    # state: (i, j, dir) dir 0=none 1=horizontal 2=vertical
    start_state = (si, sj, 0)
    g = {start_state: 0.0}
    pq = [(h(si, sj), 0.0, start_state)]
    came: dict = {}
    while pq:
        _, gc, (i, j, d) = heapq.heappop(pq)
        if (i, j) == (gi, gj):
            # reconstruct
            pts = [(xs[i], ys[j])]
            cur = (i, j, d)
            while cur in came:
                cur = came[cur]
                pts.append((xs[cur[0]], ys[cur[1]]))
            return list(reversed(pts))
        if gc > g.get((i, j, d), float("inf")):
            continue
        for di, dj, nd in ((1, 0, 1), (-1, 0, 1), (0, 1, 2), (0, -1, 2)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < len(xs) and 0 <= nj < len(ys)):
                continue
            p0, p1 = (xs[i], ys[j]), (xs[ni], ys[nj])
            if not _seg_free(p0, p1, obstacles):
                continue
            seg_len = abs(xs[ni] - xs[i]) + abs(ys[nj] - ys[j])
            turn = _TURN_PENALTY if (d != 0 and d != nd) else 0.0
            ng = gc + seg_len + turn
            ns = (ni, nj, nd)
            if ng < g.get(ns, float("inf")):
                g[ns] = ng
                came[ns] = (i, j, d)
                heapq.heappush(pq, (ng + h(ni, nj), ng, ns))
    return None


def _simplify(pts):
    """Drop colinear interior points."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for k in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[k]
        cx, cy = pts[k + 1]
        # keep b only if direction changes
        if not ((abs(ax - bx) <= _EPS and abs(bx - cx) <= _EPS) or
                (abs(ay - by) <= _EPS and abs(by - cy) <= _EPS)):
            out.append(pts[k])
    out.append(pts[-1])
    return out


def route_edges_gutter(diagram: Diagram) -> list[RouteInfo | None]:
    """Route every edge with the grid A*; falls back to the step-2 L-route when
    no obstacle-free path is found. Keeps step-2 exit/entry points as endpoints."""
    base = route_edges(diagram, spread=True)
    boxes: dict = {c.id: c for c in diagram.containers}
    boxes.update({n.id: n for n in diagram.nodes})
    node_boxes = {n.id: _bbox(n) for n in diagram.nodes}

    out: list[RouteInfo | None] = []
    for e, info in zip(diagram.edges, base):
        if info is None:
            out.append(None)
            continue
        # Straight-first: a direct center→center line that clears every other icon
        # becomes a clean, waypoint-free, floating connector. Only when it would cut
        # through an icon do we fall back to the orthogonal gutter route below.
        s, t = boxes[e.source], boxes[e.target]
        sc, tc = _center(s), _center(t)
        if _straight_clear(sc, tc, node_boxes, e.source, e.target):
            out.append(RouteInfo(info.exitX, info.exitY, info.entryX, info.entryY,
                                 points=[sc, tc], waypoints=[],
                                 straight=True, floating=True))
            continue
        start, goal = info.points[0], info.points[-1]
        # obstacles: other icons inflated; src/tgt exact (border-touch allowed)
        obs = []
        for nid, bx in node_boxes.items():
            if nid in (e.source, e.target):
                obs.append(bx)
            else:
                obs.append((bx[0] - _CLEARANCE, bx[1] - _CLEARANCE,
                            bx[2] + _CLEARANCE, bx[3] + _CLEARANCE))
        xs = _coord_lines({start[0], goal[0]} | {o[0] for o in obs} | {o[2] for o in obs})
        ys = _coord_lines({start[1], goal[1]} | {o[1] for o in obs} | {o[3] for o in obs})
        path = _astar(start, goal, obs, xs, ys)
        if path is None:
            out.append(info)        # fallback: step-2 L-route
            continue
        path = _simplify([start, *path[1:-1], goal])
        out.append(RouteInfo(info.exitX, info.exitY, info.entryX, info.entryY,
                             points=path, waypoints=path[1:-1]))
    _separate_lanes(out, diagram, node_boxes)
    return out


_LANE_GAP = 9.0
_OVERLAP_MIN = 16.0


def _offset_segment(points, k, axis, newc):
    """Move segment k (between points k, k+1) to perpendicular coord `newc`,
    inserting stubs when an endpoint is the fixed start/end so connection points
    don't move. axis 'h' moves y; 'v' moves x."""
    ci = 1 if axis == "h" else 0
    n = len(points)
    left_fixed, right_fixed = (k == 0), (k + 1 == n - 1)
    new: list[list[float]] = []
    for i, p in enumerate(points):
        q = list(p)
        if i == k:
            if left_fixed:
                new.append(list(p)); s = list(p); s[ci] = newc; new.append(s)
            else:
                q[ci] = newc; new.append(q)
        elif i == k + 1:
            if right_fixed:
                s = list(p); s[ci] = newc; new.append(s); new.append(list(p))
            else:
                q[ci] = newc; new.append(q)
        else:
            new.append(q)
    return [tuple(p) for p in new]


def _route_obstacles(e, node_boxes):
    obs = []
    for nid, bx in node_boxes.items():
        if nid in (e.source, e.target):
            obs.append(bx)
        else:
            obs.append((bx[0] - _CLEARANCE, bx[1] - _CLEARANCE,
                        bx[2] + _CLEARANCE, bx[3] + _CLEARANCE))
    return obs


def _separate_lanes(routes, diagram, node_boxes) -> None:
    """Offset edges that share a channel onto parallel lanes (the 'C' fix).
    Each candidate offset is reverted if it would route the line through an icon,
    so A (icon-crossing) can never regress."""
    segs: list[tuple] = []   # (ridx, k, orient, coord, lo, hi)
    for ridx, r in enumerate(routes):
        if r is None or r.straight:      # never bend a straight line back into lanes
            continue
        for k in range(len(r.points) - 1):
            (ax, ay), (bx, by) = r.points[k], r.points[k + 1]
            if abs(ay - by) <= _EPS and abs(ax - bx) > _EPS:
                segs.append((ridx, k, "h", ay, min(ax, bx), max(ax, bx)))
            elif abs(ax - bx) <= _EPS and abs(ay - by) > _EPS:
                segs.append((ridx, k, "v", ax, min(ay, by), max(ay, by)))

    # cluster colinear overlapping segments from DIFFERENT routes
    used = [False] * len(segs)
    for i in range(len(segs)):
        if used[i]:
            continue
        ri, ki, oi, ci, loi, hii = segs[i]
        cluster = [i]
        for j in range(i + 1, len(segs)):
            if used[j]:
                continue
            rj, kj, oj, cj, loj, hij = segs[j]
            if oj == oi and abs(cj - ci) <= _EPS and rj != ri \
               and min(hii, hij) - max(loi, loj) > _OVERLAP_MIN:
                cluster.append(j); used[j] = True
        if len(cluster) < 2:
            continue
        used[i] = True
        # one segment per distinct route, ordered by route index for stability
        by_route: dict = {}
        for idx in cluster:
            by_route.setdefault(segs[idx][0], idx)
        members = list(by_route.values())
        m = len(members)
        for rank, sidx in enumerate(members):
            ridx, k, orient, coord, _, _ = segs[sidx]
            newc = coord + (rank - (m - 1) / 2) * _LANE_GAP
            if abs(newc - coord) < _EPS:
                continue
            cand = _simplify(_offset_segment(routes[ridx].points, k, orient, newc))
            obs = _route_obstacles(diagram.edges[ridx], node_boxes)
            if all(_seg_free(cand[s], cand[s + 1], obs) for s in range(len(cand) - 1)):
                routes[ridx].points = cand
                routes[ridx].waypoints = cand[1:-1]
