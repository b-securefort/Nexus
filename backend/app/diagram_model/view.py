"""Views: one SemanticGraph, many diagrams.

A View is a curation decision — what this particular picture shows — and
`project()` turns it into the IR authoring contract (the same dict the
generate tool accepts). The L0 overview and every L1 drill-down are Views
over one graph, so they cannot drift from each other: change the graph and
every projection changes with it.

Projection is deterministic and total: same graph + same view ⇒ identical IR
dict, and every relation either appears, re-targets to a collapsed/visible
ancestor, or is provably out of frame — never silently lost to ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import SemanticGraph

# Container style token by provider-type suffix; anything else groups plainly.
_CONTAINER_STYLE = {
    "virtualnetworks": "vnet",
    "subnets": "subnet",
    "resourcegroups": "resource_group",
    "subscriptions": "zone",
}
# A leaf with no icon still renders — as a generic box, never a crash.
_FALLBACK_ICON = "shape/process"


def _container_style(rtype: str) -> str:
    return _CONTAINER_STYLE.get(rtype.rsplit("/", 1)[-1].lower(), "group")


@dataclass
class View:
    title: str = ""                 # "" = graph title
    direction: str = "LR"
    # Roots to show (with ancestors for containment + all descendants).
    # Empty = whole graph.
    include: list[str] = field(default_factory=list)
    # Subtrees to drop entirely (id + descendants + their relations).
    exclude: list[str] = field(default_factory=list)
    # Containers to render as ONE node ("label (n)"); relations into the
    # subtree re-target to the collapsed node.
    collapse: list[str] = field(default_factory=list)


def _visible_ids(graph: SemanticGraph, view: View) -> set[str]:
    all_ids = {r.id for r in graph.resources}
    if view.include:
        visible: set[str] = set()
        by_id = graph.by_id()
        for rid in view.include:
            if rid not in all_ids:
                raise ValueError(f"view.include: unknown resource '{rid}'")
            visible.add(rid)
            visible |= graph.descendants_of(rid)
            cur = by_id[rid].parent           # ancestors keep containment intact
            while cur is not None:
                visible.add(cur)
                cur = by_id[cur].parent
    else:
        visible = set(all_ids)
    for rid in view.exclude:
        if rid not in all_ids:
            raise ValueError(f"view.exclude: unknown resource '{rid}'")
        visible.discard(rid)
        visible -= graph.descendants_of(rid)
    return visible


def project(graph: SemanticGraph, view: View | None = None) -> dict:
    """Project the graph through the view into an IR authoring-contract dict."""
    view = view or View()
    visible = _visible_ids(graph, view)

    # Collapsed containers stay visible themselves; their subtrees fold into
    # them. `rep` maps every folded id to the collapsed ancestor that now
    # stands for it (outermost collapse wins when they nest).
    rep: dict[str, str] = {}
    folded: set[str] = set()
    for rid in view.collapse:
        if rid not in {r.id for r in graph.resources}:
            raise ValueError(f"view.collapse: unknown resource '{rid}'")
        if rid not in visible or rid in folded:
            continue
        subtree = graph.descendants_of(rid) & visible
        for sub in subtree:
            rep.setdefault(sub, rid)
        folded |= subtree

    shown = visible - folded
    has_shown_children = {
        r.parent for r in graph.resources if r.id in shown and r.parent in shown
    }

    containers, nodes = [], []
    for r in graph.resources:               # graph order ⇒ deterministic output
        if r.id not in shown:
            continue
        parent = r.parent if (r.parent in shown) else None
        if r.id in has_shown_children:
            containers.append({
                "id": r.id, "label": r.label, "style": _container_style(r.rtype),
                "parent": parent,
                "children": [c.id for c in graph.children_of(r.id) if c.id in shown],
            })
        else:
            n_folded = len(graph.descendants_of(r.id) & visible)
            label = f"{r.label} ({n_folded})" if r.id in view.collapse and n_folded else r.label
            nodes.append({
                "id": r.id, "label": label,
                "icon": r.icon or _FALLBACK_ICON, "parent": parent,
            })

    edges, seen = [], set()
    for rel in graph.relations:
        src = rep.get(rel.source, rel.source)
        tgt = rep.get(rel.target, rel.target)
        if src not in shown or tgt not in shown or src == tgt:
            continue                        # out of frame, or internal to a collapse
        key = (src, tgt, rel.type)
        if key in seen:
            continue                        # many folded relations ⇒ one edge
        seen.add(key)
        edges.append({"source": src, "target": tgt, "type": rel.type,
                      "label": rel.label if (src, tgt) == (rel.source, rel.target) else ""})

    return {
        "title": view.title or graph.title,
        "direction": view.direction,
        "containers": containers,
        "nodes": nodes,
        "edges": edges,
    }
