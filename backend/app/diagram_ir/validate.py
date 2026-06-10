"""Validate a Diagram IR — the two-tier check from Q9.

HARD (a broken IR is rejected before any layout/render):
  - layer 1 token legality: style / layout / edge-type / adornment-corner / icon-ref
  - layer 2 referential integrity: unique ids; parent exists and is a container;
    parent↔children agree; no parent cycles; edge endpoints exist
ADVISORY (warnings only — never block):
  - empty container, isolated node, duplicate edge between the same pair

Geometry (layer 3) is NOT here — it only exists after layout and reuses the
existing validate_drawio geometric checks on the emitted result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import CONTAINER_STYLES, EDGE_STYLES, icon_known, suggest_icons
from .schema import Container, Diagram, Node

_LEGAL_LAYOUT = {"", "row", "column", "grid"}
_LEGAL_CORNER = {"top-left", "top-right", "bottom-left", "bottom-right"}


def _icon_hint(ref: str) -> str:
    close = suggest_icons(ref)
    if close:
        return f" — did you mean: {', '.join(close)}?"
    return " — use a 'shape/*' builtin as a fallback and tell the user the icon is missing"


@dataclass
class IRValidation:
    errors: list[str] = field(default_factory=list)   # hard — block render
    warnings: list[str] = field(default_factory=list)  # advisory — render anyway

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        if self.ok and not self.warnings:
            return "IR validation PASSED."
        head = "IR validation PASSED" if self.ok else "IR validation FAILED"
        lines = [f"{head}: {len(self.errors)} error(s), {len(self.warnings)} warning(s)."]
        for e in self.errors:
            lines.append(f"  [error] {e}")
        for w in self.warnings:
            lines.append(f"  [warn]  {w}")
        return "\n".join(lines)


def validate_ir(diagram: Diagram) -> IRValidation:
    v = IRValidation()
    containers = {c.id: c for c in diagram.containers}
    nodes = {n.id: n for n in diagram.nodes}

    # --- unique ids across all boxes ---
    seen: dict[str, int] = {}
    for b in (*diagram.containers, *diagram.nodes):
        seen[b.id] = seen.get(b.id, 0) + 1
    for bid, count in seen.items():
        if count > 1:
            v.errors.append(f"duplicate id '{bid}' ({count} boxes share it)")
    boxes: dict[str, Container | Node] = {**containers, **nodes}

    # --- layer 1: token legality ---
    if diagram.direction not in ("LR", "TB"):
        v.errors.append(f"diagram.direction '{diagram.direction}' invalid (LR|TB)")
    for c in diagram.containers:
        if c.style not in CONTAINER_STYLES:
            v.errors.append(f"container '{c.id}': unknown style '{c.style}'")
        if c.layout not in _LEGAL_LAYOUT:
            v.errors.append(f"container '{c.id}': unknown layout '{c.layout}'")
        if c.grid_cols < 0:
            v.errors.append(f"container '{c.id}': grid_cols must be >= 0")
    for b in (*diagram.containers, *diagram.nodes):
        for ad in b.adornments:
            if ad.corner not in _LEGAL_CORNER:
                v.errors.append(f"'{b.id}': adornment corner '{ad.corner}' invalid")
            if not icon_known(ad.icon):
                v.errors.append(
                    f"'{b.id}': adornment icon '{ad.icon}' not in catalog"
                    f"{_icon_hint(ad.icon)}"
                )
    for n in diagram.nodes:
        if not icon_known(n.icon):
            v.errors.append(
                f"node '{n.id}': icon '{n.icon}' not in catalog{_icon_hint(n.icon)}"
            )
    for e in diagram.edges:
        if e.type not in EDGE_STYLES:
            v.errors.append(f"edge {e.source}->{e.target}: unknown type '{e.type}'")

    # --- layer 2: referential integrity ---
    for b in (*diagram.containers, *diagram.nodes):
        if b.parent is not None:
            if b.parent not in boxes:
                v.errors.append(f"'{b.id}': parent '{b.parent}' does not exist")
            elif b.parent not in containers:
                v.errors.append(f"'{b.id}': parent '{b.parent}' is a node, not a container")
            elif b.id not in containers[b.parent].children:
                v.errors.append(
                    f"'{b.id}': parent is '{b.parent}' but that container's "
                    f"children does not list '{b.id}' (parent/children disagree)"
                )
    for c in diagram.containers:
        for k in c.children:
            if k not in boxes:
                v.errors.append(f"container '{c.id}': child '{k}' does not exist")
            elif boxes[k].parent != c.id:
                v.errors.append(
                    f"container '{c.id}': lists child '{k}' but '{k}'.parent is "
                    f"'{boxes[k].parent}' (parent/children disagree)"
                )
    for e in diagram.edges:
        if e.source not in boxes:
            v.errors.append(f"edge: source '{e.source}' does not exist")
        if e.target not in boxes:
            v.errors.append(f"edge: target '{e.target}' does not exist")

    _check_cycles(diagram, boxes, v)

    # --- advisory (layer 3-lite, structural) ---
    edged = {e.source for e in diagram.edges} | {e.target for e in diagram.edges}
    for c in diagram.containers:
        if not c.children:
            v.warnings.append(f"container '{c.id}' has no children (renders empty)")
    for n in diagram.nodes:
        if n.id not in edged:
            v.warnings.append(f"node '{n.id}' has no edges (isolated)")
    pair_seen: set[tuple[str, str]] = set()
    for e in diagram.edges:
        key = (e.source, e.target)
        if key in pair_seen:
            v.warnings.append(f"duplicate edge {e.source}->{e.target}")
        pair_seen.add(key)

    # align_to is a cosmetic hint — a broken ref just means "no alignment", so
    # warn rather than block (unlike parent/edge refs, which are hard errors).
    for b in (*diagram.containers, *diagram.nodes):
        if b.align_to is not None:
            if b.align_to not in boxes:
                v.warnings.append(f"'{b.id}': align_to '{b.align_to}' does not exist (ignored)")
            elif b.align_to == b.id:
                v.warnings.append(f"'{b.id}': align_to points at itself (ignored)")
            elif b.parent is not None and boxes[b.align_to].parent == b.parent:
                v.warnings.append(
                    f"'{b.id}': align_to '{b.align_to}' is a sibling in the same band "
                    f"(ignored — it would stack them and collapse edges onto one line; "
                    f"align_to is for a satellite in a DIFFERENT band)"
                )

    return v


def _check_cycles(diagram: Diagram, boxes: dict, v: IRValidation) -> None:
    for b in (*diagram.containers, *diagram.nodes):
        seen, cur = set(), b.parent
        while cur and cur in boxes:
            if cur == b.id or cur in seen:
                v.errors.append(f"'{b.id}': parent chain forms a cycle")
                break
            seen.add(cur)
            cur = boxes[cur].parent
