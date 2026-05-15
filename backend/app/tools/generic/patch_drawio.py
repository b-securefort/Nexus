"""
Surgical updates to a single cell's geometry in a .drawio file.

Used to apply a validator-suggested-fix coordinate (e.g. "set x to 244") without
the model having to rewrite the entire diagram. Auto-runs validate + render on
the patched file so the agent gets the same feedback loop as generate_file.
"""

import logging
import re
from pathlib import Path

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")

# The four geometry attributes the model is allowed to patch. Anything else
# (id, parent, style, value) requires generate_file - patching those would
# silently corrupt the diagram, so we don't expose them here.
_PATCHABLE = ("x", "y", "width", "height")


def _format_number(val: float | int) -> str:
    """Render numbers without a trailing .0 when they're whole. Drawio writes
    integer coordinates by convention; sticking to that keeps diffs minimal."""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)


def _patch_geometry(
    xml_text: str, cell_id: str, updates: dict[str, float]
) -> tuple[str | None, str | None]:
    """Replace x/y/width/height attributes on the named cell's <mxGeometry>.

    Returns (new_xml, None) on success or (None, error) on failure. Operates
    on the raw text via regex so the rest of the file - whitespace, attribute
    order, comments - is preserved byte-for-byte. ElementTree round-tripping
    would reformat unrelated cells and bloat the diff.
    """
    # Match <mxCell ... id="<cell_id>" ...> ... </mxCell>. id can appear in
    # any position among the attributes; `[^>]*\bid=` handles both.
    cell_re = re.compile(
        rf'(<mxCell\b[^>]*\bid="{re.escape(cell_id)}"[^>]*>)(.*?)(</mxCell>)',
        re.DOTALL,
    )
    match = cell_re.search(xml_text)
    if match is None:
        return None, f"cell '{cell_id}' not found in file"

    cell_open, cell_body, cell_close = match.groups()

    # Find the first <mxGeometry ...> inside the cell body. This is the cell's
    # own geometry - any nested mxGeometry would belong to a child cell, but
    # mxCells aren't nested inside other mxCells in drawio's schema.
    geom_re = re.compile(r"<mxGeometry\b[^>]*?(/?)>")
    geom_match = geom_re.search(cell_body)
    if geom_match is None:
        return None, f"cell '{cell_id}' has no <mxGeometry> to patch"
    geom_str = geom_match.group(0)

    new_geom = geom_str
    for attr, val in updates.items():
        formatted = _format_number(val)
        attr_re = re.compile(rf'\b{attr}="[^"]*"')
        if attr_re.search(new_geom):
            new_geom = attr_re.sub(f'{attr}="{formatted}"', new_geom, count=1)
        else:
            # Insert the attribute before the closing `>` (or `/>`).
            new_geom = re.sub(
                r"(\s*/?>)\s*$",
                f' {attr}="{formatted}"\\1',
                new_geom,
                count=1,
            )

    new_body = cell_body.replace(geom_str, new_geom, 1)
    return (
        xml_text[: match.start()]
        + cell_open
        + new_body
        + cell_close
        + xml_text[match.end() :],
        None,
    )


class PatchDrawioCellTool(Tool):
    name = "patch_drawio_cell"
    description = (
        "Update only the geometry (x, y, width, height) of one cell in an existing "
        ".drawio file. Use this to apply a validator suggested-fix coordinate "
        "without rewriting the whole diagram - cheaper, less error-prone, and "
        "won't accidentally regress unrelated parts. The cell_id and target "
        "values come straight from the [overlap] / [containment] violation. "
        "Auto-runs validate_drawio and renders a fresh PNG."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Existing .drawio file in output/, e.g. 'arch.drawio'.",
            },
            "cell_id": {
                "type": "string",
                "description": (
                    "ID of the cell to update, exactly as named in the validator's "
                    "violation message (e.g. 'hub-pip', 'snet-app')."
                ),
            },
            "x": {"type": "number", "description": "New x (relative to parent), if changing."},
            "y": {"type": "number", "description": "New y (relative to parent), if changing."},
            "width": {"type": "number", "description": "New width, if changing."},
            "height": {"type": "number", "description": "New height, if changing."},
        },
        "required": ["filename", "cell_id"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        filename = (args.get("filename") or args.get("file_name") or "").strip()
        cell_id = (args.get("cell_id") or "").strip()

        updates: dict[str, float] = {}
        for attr in _PATCHABLE:
            if attr in args and args[attr] is not None:
                try:
                    updates[attr] = float(args[attr])
                except (TypeError, ValueError):
                    return f"Error: {attr} must be a number, got {args[attr]!r}."

        if not filename:
            return "Error: filename is required."
        if not filename.endswith(".drawio"):
            return "Error: filename must end with .drawio."
        if ".." in filename or filename.startswith(("/", "\\")):
            return "Error: invalid filename - path traversal not allowed."
        if not cell_id:
            return "Error: cell_id is required."
        if not updates:
            return (
                "Error: provide at least one of x, y, width, height to patch. "
                "Pull the target value from the validator's suggested-fix line."
            )

        target = (_OUTPUT_DIR / filename).resolve()
        sandbox = _OUTPUT_DIR.resolve()
        try:
            target.relative_to(sandbox)
        except ValueError:
            return "Error: path escapes output/ sandbox."
        if not target.exists():
            return f"Error: {filename} not found in output/."

        try:
            xml = target.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error reading {filename}: {e}"

        new_xml, err = _patch_geometry(xml, cell_id, updates)
        if err is not None or new_xml is None:
            return f"Error: {err}"

        try:
            target.write_text(new_xml, encoding="utf-8")
        except OSError as e:
            return f"Error writing {filename}: {e}"

        change_summary = ", ".join(
            f"{a}={_format_number(v)}" for a, v in updates.items()
        )
        logger.info(
            "Patched %s cell '%s': %s by %s",
            filename, cell_id, change_summary, user.email,
        )
        result = f"Patched output/{filename} cell '{cell_id}': {change_summary}"

        # Auto-validate so the model immediately sees whether the patch fixed
        # the violation. Same contract as generate_file.
        from app.tools.validate_drawio import validate_drawio_file
        report = validate_drawio_file(target)
        result += f"\n\nAuto-validation:\n{report}"

        # Auto-render so the orchestrator's image-injection picks up the new
        # PNG on the next turn. Skip if validation failed at the parse layer
        # (renderer would also fail).
        if "Validation FAILED: XML parse error" not in report:
            try:
                from app.tools.render_drawio import render_drawio_to_disk
                out_path, mode, render_err = render_drawio_to_disk(filename, "png")
            except Exception as e:  # noqa: BLE001
                out_path, mode, render_err = None, None, str(e)
            if out_path is not None:
                size_kb = out_path.stat().st_size // 1024
                result += (
                    f"\n\nAuto-render: output/{out_path.name} "
                    f"({size_kb} KB, via {mode}). The image is being attached "
                    "to your next turn for visual review."
                )
            elif render_err and "not installed" not in render_err.lower():
                result += f"\n\nAuto-render skipped: {render_err}"

        return result
