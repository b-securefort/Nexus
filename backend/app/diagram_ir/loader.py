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
             "layout", "grid_cols", "x", "y", "w", "h"}


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
        adornments=[_adornment(a, f"{where}.adornments[{i}]")
                    for i, a in enumerate(_opt(d, "adornments", list, [], where))],
    )


def _container(d: dict, where: str) -> Container:
    return Container(
        id=_require(d, "id", str, where),
        label=_opt(d, "label", str, "", where),
        style=_require(d, "style", str, where),
        parent=_opt(d, "parent", str, None, where),
        children=list(_opt(d, "children", list, [], where)),
        adornments=[_adornment(a, f"{where}.adornments[{i}]")
                    for i, a in enumerate(_opt(d, "adornments", list, [], where))],
        layout=_opt(d, "layout", str, "", where),
        grid_cols=int(_opt(d, "grid_cols", _NUM, 0, where)),
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
    return Diagram(
        title=_opt(data, "title", str, "", "diagram"),
        direction=_opt(data, "direction", str, "LR", "diagram"),
        containers=[_container(c, f"containers[{i}]")
                    for i, c in enumerate(_opt(data, "containers", list, [], "diagram"))],
        nodes=[_node(n, f"nodes[{i}]")
               for i, n in enumerate(_opt(data, "nodes", list, [], "diagram"))],
        edges=[_edge(e, f"edges[{i}]")
               for i, e in enumerate(_opt(data, "edges", list, [], "diagram"))],
    )
