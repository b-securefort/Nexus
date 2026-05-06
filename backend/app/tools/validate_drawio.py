"""
Drawio diagram validator.

Detects layout/encoding violations that cause overlapping icons, stacked labels,
unrendered line breaks, and missing vendor icons. Used as a standalone tool
(`validate_drawio`) and called automatically by `generate_file` on every
`.drawio` write so the model can't skip validation.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")

# Layout standards — must match the rules inlined in the SKILL prompts.
_MIN_HORIZ_GAP = 80
_MIN_VERT_GAP = 60
_CONTAINER_PADDING = 40
# Vertices wider/taller than this are containers (zones, VNets, subnets, VPCs).
_CONTAINER_MIN_DIM = 300

# Service-name keywords for rule-based checks.
_OBSERVABILITY_KEYWORDS = (
    "monitor", "log analytics", "sentinel", "app insights",
    "application insights", "cloudwatch", "cloudtrail",
)
_VNET_KEYWORDS = ("vnet", "virtual network", "vpc", "subnet")


@dataclass
class _Cell:
    id: str
    parent: str
    is_vertex: bool
    is_edge: bool
    value: str
    style: str
    source: str
    target: str
    x: float
    y: float
    w: float
    h: float

    @property
    def is_container(self) -> bool:
        return self.is_vertex and (self.w >= _CONTAINER_MIN_DIM or self.h >= _CONTAINER_MIN_DIM)

    @property
    def is_resource_candidate(self) -> bool:
        # A non-container vertex that carries a label is treated as a resource.
        return self.is_vertex and not self.is_container and bool(self.value.strip())

    @property
    def has_vendor_icon(self) -> bool:
        return "image=img/lib/azure2/" in self.style or "shape=mxgraph.aws4." in self.style


def _parse(xml_text: str) -> dict[str, _Cell]:
    root = ET.fromstring(xml_text)
    cells: dict[str, _Cell] = {}
    for el in root.iter("mxCell"):
        cell_id = el.get("id", "")
        if not cell_id:
            continue
        geom = el.find("mxGeometry")
        x = float(geom.get("x", "0") or 0) if geom is not None else 0.0
        y = float(geom.get("y", "0") or 0) if geom is not None else 0.0
        w = float(geom.get("width", "0") or 0) if geom is not None else 0.0
        h = float(geom.get("height", "0") or 0) if geom is not None else 0.0
        cells[cell_id] = _Cell(
            id=cell_id,
            parent=el.get("parent", ""),
            is_vertex=el.get("vertex") == "1",
            is_edge=el.get("edge") == "1",
            value=el.get("value", ""),
            style=el.get("style", ""),
            source=el.get("source", ""),
            target=el.get("target", ""),
            x=x, y=y, w=w, h=h,
        )
    return cells


def _abs_pos(cell: _Cell, cells: dict[str, _Cell]) -> tuple[float, float]:
    """Walk parent chain, summing offsets from every vertex ancestor."""
    x, y = cell.x, cell.y
    parent_id = cell.parent
    seen = {cell.id}
    while parent_id and parent_id in cells and parent_id not in seen:
        parent = cells[parent_id]
        if not parent.is_vertex:
            break
        x += parent.x
        y += parent.y
        seen.add(parent_id)
        parent_id = parent.parent
    return x, y


def _bbox(cell: _Cell, cells: dict[str, _Cell]) -> tuple[float, float, float, float]:
    ax, ay = _abs_pos(cell, cells)
    return ax, ay, ax + cell.w, ay + cell.h


def _ancestors(cell: _Cell, cells: dict[str, _Cell]) -> list[_Cell]:
    out: list[_Cell] = []
    seen = {cell.id}
    parent_id = cell.parent
    while parent_id and parent_id in cells and parent_id not in seen:
        parent = cells[parent_id]
        out.append(parent)
        seen.add(parent_id)
        parent_id = parent.parent
    return out


def _label_preview(value: str, n: int = 40) -> str:
    v = value.replace("\n", " ").replace("\\n", " ")
    return v[:n] + ("..." if len(v) > n else "")


# --- Individual checks ---

def _check_literal_newlines(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    for c in cells.values():
        if "\\n" in c.value:
            out.append(
                f'[encoding] cell "{c.id}" has literal `\\n` in label '
                f'("{_label_preview(c.value)}"). '
                f"In XML attributes, use `&#10;` for line breaks — drawio does not unescape `\\n`."
            )
    return out


def _check_vendor_icons(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    for c in cells.values():
        if not c.is_resource_candidate or c.has_vendor_icon:
            continue
        out.append(
            f'[icon-style] cell "{c.id}" ("{_label_preview(c.value)}") '
            f"is a resource-sized vertex but uses a generic style. "
            f"Use shape=image;image=img/lib/azure2/<category>/<Icon>.svg "
            f"or shape=mxgraph.aws4.<service>."
        )
    return out


def _check_resources_parented_to_subnets(cells: dict[str, _Cell]) -> list[str]:
    """Check that resources inside VNets are parented to subnets, not canvas."""
    out: list[str] = []
    
    # Find all VNet and subnet containers
    vnet_ids = set()
    subnet_ids = set()
    for c in cells.values():
        v = c.value.lower()
        if c.is_container:
            if 'vnet' in v or 'virtual network' in v or 'vpc' in v:
                vnet_ids.add(c.id)
            elif 'subnet' in v or 'snet' in c.id.lower():
                subnet_ids.add(c.id)
    
    # Find resources that should be in subnets but have parent="1"
    for c in cells.values():
        if not c.is_resource_candidate:
            continue
        
        # Skip global services that should be on canvas
        v = c.value.lower()
        if any(kw in v for kw in ['internet', 'user', 'front door', 'cdn', 'monitor', 'log analytics', 'sentinel', 'cloudwatch', 'cloudtrail']):
            continue
        
        # If resource has parent="1" but there are subnets in the diagram, flag it
        if c.parent == "1" and subnet_ids:
            # Check if this resource is visually inside a VNet (by coordinates)
            for vnet_id in vnet_ids:
                vnet = cells.get(vnet_id)
                if vnet and vnet.x <= c.x <= vnet.x + vnet.w and vnet.y <= c.y <= vnet.y + vnet.h:
                    out.append(
                        f'[resource-parent] cell "{c.id}" ("{_label_preview(c.value)}") '
                        f'has parent="1" but appears to be inside VNet "{vnet_id}". '
                        f'Resources inside VNets must have parent="snet-xxx" (subnet ID), not parent="1". '
                        f'Otherwise icons float outside their network containers.'
                    )
                    break
    
    return out


def _rects_clear(a: tuple, b: tuple, gap_x: float, gap_y: float) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    horiz_clear = ax2 + gap_x <= bx1 or bx2 + gap_x <= ax1
    vert_clear = ay2 + gap_y <= by1 or by2 + gap_y <= ay1
    return horiz_clear or vert_clear


def _check_icon_overlap(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    icons = [c for c in cells.values() if c.is_resource_candidate]
    boxes = [(c, _bbox(c, cells)) for c in icons]
    for i, (a, abox) in enumerate(boxes):
        for b, bbox in boxes[i + 1:]:
            if not _rects_clear(abox, bbox, _MIN_HORIZ_GAP, _MIN_VERT_GAP):
                out.append(
                    f'[overlap] cells "{a.id}" ("{_label_preview(a.value, 25)}") '
                    f'and "{b.id}" ("{_label_preview(b.value, 25)}") '
                    f"overlap or are too close. Need ≥{_MIN_HORIZ_GAP}px horizontal "
                    f"or ≥{_MIN_VERT_GAP}px vertical gap between resource icons."
                )
    return out


def _check_containment(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    for c in cells.values():
        if not c.is_resource_candidate:
            continue
        container = next((a for a in _ancestors(c, cells) if a.is_container), None)
        if container is None:
            continue
        cx1, cy1, cx2, cy2 = _bbox(container, cells)
        ax1, ay1, ax2, ay2 = _bbox(c, cells)
        pad = _CONTAINER_PADDING
        if (
            ax1 < cx1 + pad or ay1 < cy1 + pad
            or ax2 > cx2 - pad or ay2 > cy2 - pad
        ):
            out.append(
                f'[containment] cell "{c.id}" ("{_label_preview(c.value, 25)}") '
                f'lies outside or too close to the edge of container '
                f'"{container.id}" ("{_label_preview(container.value, 25)}"). '
                f"Require ≥{pad}px padding from each container edge."
            )
    return out


def _check_observability_outside(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    for c in cells.values():
        if not c.is_resource_candidate:
            continue
        v = c.value.lower()
        if not any(k in v for k in _OBSERVABILITY_KEYWORDS):
            continue
        for a in _ancestors(c, cells):
            av = a.value.lower()
            if any(k in av for k in _VNET_KEYWORDS):
                out.append(
                    f'[observability-in-vnet] cell "{c.id}" '
                    f'("{_label_preview(c.value, 25)}") is inside network container '
                    f'"{a.id}" ("{_label_preview(a.value, 25)}"). '
                    f"Move observability resources outside any VNet/VPC into a "
                    f"separate Monitoring zone with a dashed telemetry edge."
                )
                break
    return out


def _check_duplicate_edge_labels(cells: dict[str, _Cell]) -> list[str]:
    out: list[str] = []
    by_source: dict[str, list[_Cell]] = {}
    for c in cells.values():
        if not c.is_edge or not c.source or not c.value.strip():
            continue
        by_source.setdefault(c.source, []).append(c)
    for src, edges in by_source.items():
        labels: dict[str, list[str]] = {}
        for e in edges:
            labels.setdefault(e.value, []).append(e.id)
        for label, ids in labels.items():
            if len(ids) > 1:
                out.append(
                    f'[duplicate-edge-labels] {len(ids)} edges leaving "{src}" '
                    f'share label "{_label_preview(label, 30)}" '
                    f'(edge ids: {", ".join(ids)}). '
                    f"Make each label unique by destination role."
                )
    return out


# --- Public entry points ---

def validate_drawio_file(path: Path) -> str:
    """Validate a .drawio file. Returns a human-readable report."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Validation skipped: could not read {path} ({e})"
    try:
        cells = _parse(text)
    except ET.ParseError as e:
        return f"Validation FAILED: XML parse error — {e}"

    violations: list[str] = []
    for check in (
        _check_literal_newlines,
        _check_vendor_icons,
        _check_resources_parented_to_subnets,
        _check_icon_overlap,
        _check_containment,
        _check_observability_outside,
        _check_duplicate_edge_labels,
    ):
        violations.extend(check(cells))

    counts = {
        "vertices": sum(1 for c in cells.values() if c.is_vertex),
        "edges": sum(1 for c in cells.values() if c.is_edge),
        "icons": sum(1 for c in cells.values() if c.is_resource_candidate),
        "containers": sum(1 for c in cells.values() if c.is_container),
    }
    summary = (
        f"{counts['vertices']} vertices ({counts['icons']} resources, "
        f"{counts['containers']} containers), {counts['edges']} edges"
    )

    if not violations:
        return f"Validation PASSED: {summary}. No layout violations."

    lines = [
        f"Validation FAILED: {len(violations)} violation(s) found.",
        f"Counts: {summary}.",
        "",
        "Violations to fix:",
    ]
    for i, v in enumerate(violations, 1):
        lines.append(f"  {i}. {v}")
    lines.append("")
    lines.append(
        "Fix each violation, re-write the file with overwrite=true, "
        "then re-run validate_drawio. Iterate until PASSED."
    )
    return "\n".join(lines)


class ValidateDrawioTool(Tool):
    name = "validate_drawio"
    description = (
        "Validate a .drawio file in output/ for layout violations: icon overlap, "
        "missing vendor icons (Azure2/AWS4), resources not parented to subnets (floating icons), "
        "observability inside VNets, container padding, duplicate edge labels, and literal `\\n` in labels. "
        "generate_file runs this automatically on .drawio writes — call it "
        "explicitly to re-validate after fixes. If violations are reported, fix "
        "them and re-write with overwrite=true. Iterate until PASSED."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Filename of the .drawio file in output/, e.g. 'try2.drawio'.",
            }
        },
        "required": ["filename"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        filename = args.get("filename", "").strip()
        if not filename:
            return "Error: filename is required"
        if not filename.endswith(".drawio"):
            return "Error: filename must end with .drawio"
        if ".." in filename or filename.startswith(("/", "\\")):
            return "Error: invalid filename"

        target = (_OUTPUT_DIR / filename).resolve()
        sandbox = _OUTPUT_DIR.resolve()
        try:
            target.relative_to(sandbox)
        except ValueError:
            return "Error: path escapes output/ sandbox"
        if not target.exists():
            return f"Error: {filename} not found in output/"

        return validate_drawio_file(target)
