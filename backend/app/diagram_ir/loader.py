"""Load a Diagram IR from the JSON authoring contract.

This is validation **layer 1 (schema)**: it turns a dict (parsed from the model's
JSON) into typed IR objects, raising IRSchemaError with a precise path on any
missing/extra/wrong-typed field. It does NOT check referential integrity or
geometry — that's validate.py (layers 2–3).
"""

from __future__ import annotations

from typing import Any

from .schema import Adornment, Container, Diagram, Edge, Node


class IRSchemaError(ValueError):
    """A field is missing, the wrong type, or unexpected."""


def _require(d: dict, key: str, typ: type, where: str) -> Any:
    if key not in d:
        raise IRSchemaError(f"{where}: missing required field '{key}'")
    val = d[key]
    if not isinstance(val, typ) or (typ is not bool and isinstance(val, bool)):
        raise IRSchemaError(
            f"{where}.{key}: expected {typ.__name__}, got {type(val).__name__}"
        )
    return val


def _opt(d: dict, key: str, typ: type, default, where: str) -> Any:
    if key not in d or d[key] is None:
        return default
    val = d[key]
    if not isinstance(val, typ) or (typ is not bool and isinstance(val, bool)):
        raise IRSchemaError(
            f"{where}.{key}: expected {typ.__name__}, got {type(val).__name__}"
        )
    return val


_NUM = (int, float)
_BOX_KEYS = {"id", "label", "icon", "style", "parent", "children", "adornments",
             "layout", "grid_cols", "align_to", "x", "y", "w", "h"}


def _adornment(d: dict, where: str) -> Adornment:
    if not isinstance(d, dict):
        raise IRSchemaError(f"{where}: adornment must be an object")
    return Adornment(
        icon=_require(d, "icon", str, where),
        corner=_opt(d, "corner", str, "top-right", where),
        label=_opt(d, "label", str, "", where),
    )


def _node(d: dict, where: str) -> Node:
    return Node(
        id=_require(d, "id", str, where),
        label=_opt(d, "label", str, "", where),
        icon=_require(d, "icon", str, where),
        parent=_opt(d, "parent", str, None, where),
        align_to=_opt(d, "align_to", str, None, where),
        adornments=[_adornment(a, f"{where}.adornments[{i}]")
                    for i, a in enumerate(_opt(d, "adornments", list, [], where))],
    )


def _container(d: dict, where: str) -> Container:
    # `style` is optional at the schema layer: a forgotten style used to hard-fail
    # the whole IR (the #1 cause of the model giving up and shipping a dumbed-down
    # diagram). We accept "" here and infer it from the parent in load_ir below.
    return Container(
        id=_require(d, "id", str, where),
        label=_opt(d, "label", str, "", where),
        style=_opt(d, "style", str, "", where),
        parent=_opt(d, "parent", str, None, where),
        children=list(_opt(d, "children", list, [], where)),
        adornments=[_adornment(a, f"{where}.adornments[{i}]")
                    for i, a in enumerate(_opt(d, "adornments", list, [], where))],
        layout=_opt(d, "layout", str, "", where),
        grid_cols=int(_opt(d, "grid_cols", _NUM, 0, where)),
        align_to=_opt(d, "align_to", str, None, where),
    )


def _edge(d: dict, where: str) -> Edge:
    return Edge(
        source=_require(d, "source", str, where),
        target=_require(d, "target", str, where),
        type=_opt(d, "type", str, "flow", where),
        label=_opt(d, "label", str, "", where),
    )


def load_ir(data: dict) -> Diagram:
    if not isinstance(data, dict):
        raise IRSchemaError("top level: expected an object")
    diagram = Diagram(
        title=_opt(data, "title", str, "", "diagram"),
        direction=_opt(data, "direction", str, "LR", "diagram"),
        containers=[_container(c, f"containers[{i}]")
                    for i, c in enumerate(_opt(data, "containers", list, [], "diagram"))],
        nodes=[_node(n, f"nodes[{i}]")
               for i, n in enumerate(_opt(data, "nodes", list, [], "diagram"))],
        edges=[_edge(e, f"edges[{i}]")
               for i, e in enumerate(_opt(data, "edges", list, [], "diagram"))],
    )
    _infer_missing_styles(diagram)
    return diagram


# Style inferred for a container that omitted `style`, keyed by its parent's
# style: a child of a network boundary is a subnet; everything else is a plain
# group. This keeps a forgotten token renderable instead of rejecting the IR.
_STYLE_FROM_PARENT = {"vnet": "subnet", "vpc": "subnet"}


def _infer_missing_styles(diagram: Diagram) -> None:
    by_id = {c.id: c for c in diagram.containers}

    def resolve(c: Container, seen: frozenset[str]) -> str:
        if c.style:
            return c.style
        parent = by_id.get(c.parent) if c.parent else None
        # Guard against a parent cycle (validate.py reports it as an error later).
        parent_style = (
            resolve(parent, seen | {c.id})
            if parent is not None and parent.id not in seen
            else ""
        )
        c.style = _STYLE_FROM_PARENT.get(parent_style, "group")
        return c.style

    for c in diagram.containers:
        resolve(c, frozenset())
