"""The structural Diagram IR.

Geometry (x/y/w/h, in absolute canvas coordinates) is optional on every box: in
the render-first walking skeleton it is hand-supplied; the future layout engine
will fill it from structure alone. The emitter converts absolute coordinates to
draw.io's parent-relative form at write time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


@dataclass
class Adornment:
    """A fixed-corner glyph/badge on a container or node (NSG on a subnet, VNet
    glyph on a VNet, WAF on a gateway). NOT placed on the grid by the layouter
    and excluded from overlap checks — it is a label *on* the box, not a node."""
    icon: str                      # icon ref, e.g. "azure/network_security_groups"
    corner: Corner = "top-right"
    label: str = ""


@dataclass
class Node:
    """A leaf resource: an icon + label. `icon` is a catalog ref like
    "azure/app_services", "aws/ec2", or "shape/cloud" for a built-in shape."""
    id: str
    label: str
    icon: str
    parent: Optional[str] = None   # container id, or None = top-level (canvas)
    adornments: list[Adornment] = field(default_factory=list)
    # Geometry (absolute). Hand-set in the skeleton; engine-computed later.
    x: float = 0.0
    y: float = 0.0
    w: float = 56.0
    h: float = 56.0


@dataclass
class Container:
    """A box that visually groups children: a VNet, subnet, resource group,
    monitoring zone, or a plain group. `style` is a visual token resolved by the
    catalog — it carries appearance, never placement logic. `children` lists the
    ids of nodes/containers drawn inside it."""
    id: str
    label: str
    style: str                     # token: vnet | vpc | subnet | resource_group | zone | monitoring | group | band
    parent: Optional[str] = None
    children: list[str] = field(default_factory=list)
    # Per-container arrangement hint consumed by the layout engine. "" = default
    # (row when Diagram.direction is LR, column when TB). grid_cols only applies
    # to layout="grid" (0 = auto ≈ sqrt(n)).
    layout: Literal["", "row", "column", "grid"] = ""
    grid_cols: int = 0
    adornments: list[Adornment] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    w: float = 200.0
    h: float = 120.0


@dataclass
class Edge:
    """A connector. `type` is a semantic token (flow | private | dns | telemetry
    | replication) the catalog maps to a style string; the engine never reasons
    about what the semantic means."""
    source: str
    target: str
    type: str = "flow"
    label: str = ""


@dataclass
class Diagram:
    title: str = ""
    direction: Literal["LR", "TB"] = "LR"
    containers: list[Container] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
