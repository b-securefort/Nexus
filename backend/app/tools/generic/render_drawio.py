"""
Render a .drawio file to an image.

Two modes:

1. **HTTP sidecar** (production / containerized) — when settings.DRAWIO_EXPORT_URL
   is set, POST the diagram XML to a `jgraph/drawio-image-export2` container.
   This is the recommended path for App Service Linux, Container Apps, AKS.

2. **Local CLI** (development) — fall back to invoking the locally installed
   draw.io desktop executable. Used when no sidecar URL is configured.

Either way, the agent gets a PNG (or other format) it can read back to verify
visual quality of the diagram it just generated.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from app.auth.models import User
from app.config import get_settings
from app.tools.base import SUBPROCESS_FLAGS, Tool

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")
_CLI_TIMEOUT_S = 45

# Standard install paths probed before falling back to PATH lookup.
_WIN_CANDIDATE_PATHS = (
    r"C:\Program Files\draw.io\draw.io.exe",
    r"C:\Program Files (x86)\draw.io\draw.io.exe",
)


def _find_drawio_executable() -> str | None:
    """Locate the draw.io desktop executable. Returns None if not found."""
    if sys.platform == "win32":
        for candidate in _WIN_CANDIDATE_PATHS:
            if Path(candidate).is_file():
                return candidate
        # WindowsApps (Microsoft Store) install — name has version + arch suffix
        win_apps = Path(r"C:\Program Files\WindowsApps")
        if win_apps.exists():
            try:
                for entry in win_apps.glob("draw.io*"):
                    exe = entry / "app" / "draw.io.exe"
                    if exe.is_file():
                        return str(exe)
            except OSError:
                pass  # WindowsApps often denies enumeration; not a hard error
    return shutil.which("drawio") or shutil.which("draw.io")


def _render_via_sidecar(
    xml: str, fmt: str, out_path: Path, sidecar_url: str, timeout: int,
) -> str | None:
    """POST XML to a drawio-image-export2 sidecar. Returns error message on failure, None on success."""
    url = sidecar_url.rstrip("/")
    # The export server's contract: POST form data with `xml` and `format` fields.
    # See https://github.com/jgraph/drawio-image-export2 for the full API.
    try:
        resp = httpx.post(
            url,
            data={"xml": xml, "format": fmt, "bg": "#FFFFFF", "scale": "1"},
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return (
            f"draw.io export sidecar at {url} timed out after {timeout}s. "
            "Check the sidecar's health and resource limits."
        )
    except httpx.HTTPError as e:
        return (
            f"Failed to reach draw.io export sidecar at {url}: {e}. "
            "Verify DRAWIO_EXPORT_URL is correct and the sidecar is running."
        )

    if resp.status_code != 200 or not resp.content:
        body = resp.text[:300] if resp.text else "(empty body)"
        return (
            f"draw.io export sidecar returned HTTP {resp.status_code}. "
            f"Response: {body}"
        )

    out_path.write_bytes(resp.content)
    return None


def _render_via_cli(
    target: Path, fmt: str, out_path: Path,
) -> str | None:
    """Invoke locally installed draw.io desktop. Returns error message on failure, None on success."""
    drawio_exe = _find_drawio_executable()
    if drawio_exe is None:
        return (
            "draw.io desktop is not installed or could not be located, and no "
            "DRAWIO_EXPORT_URL sidecar is configured. To enable rendering: "
            "(1) install draw.io desktop locally for development "
            "(https://drawio.com/), OR (2) configure a drawio-image-export2 "
            "sidecar and set DRAWIO_EXPORT_URL."
        )

    cmd = [
        drawio_exe,
        "--export",
        "--format", fmt,
        "--output", str(out_path),
        str(target),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
            **SUBPROCESS_FLAGS,
        )
    except subprocess.TimeoutExpired:
        return (
            f"draw.io CLI export timed out after {_CLI_TIMEOUT_S}s. "
            "Check that draw.io desktop opens normally; it may be hanging on a dialog."
        )
    except OSError as e:
        return f"Error invoking draw.io: {e}"

    if result.returncode != 0 or not out_path.exists():
        stderr = (result.stderr or "").strip()[:400]
        stdout = (result.stdout or "").strip()[:200]
        return (
            f"draw.io CLI export failed (exit {result.returncode}). "
            f"stdout: {stdout} stderr: {stderr}"
        )
    return None


def render_drawio_to_disk(
    filename: str, fmt: str = "png"
) -> tuple[Path | None, str | None, str | None]:
    """Render a .drawio file in output/ to <stem>.<fmt> next to the source.

    Returns (output_path, mode, error). On success, output_path and mode are
    set and error is None. On failure, output_path is None and error explains
    why; mode is None. Used by both the explicit `render_drawio` tool and the
    `generate_file` auto-render so a single rendering pipeline serves both
    call sites.
    """
    if not filename or not filename.endswith(".drawio"):
        return None, None, "filename must end with .drawio"
    if ".." in filename or filename.startswith(("/", "\\")):
        return None, None, "invalid filename - path traversal not allowed"
    if fmt not in {"png", "svg", "pdf", "jpg"}:
        return None, None, f"unsupported format '{fmt}'. Use png, svg, pdf, or jpg"

    target = (_OUTPUT_DIR / filename).resolve()
    sandbox = _OUTPUT_DIR.resolve()
    try:
        target.relative_to(sandbox)
    except ValueError:
        return None, None, "path escapes output/ sandbox"
    if not target.exists():
        return None, None, f"{filename} not found in output/"

    out_path = target.with_suffix(f".{fmt}")
    settings = get_settings()

    if settings.DRAWIO_EXPORT_URL:
        try:
            xml = target.read_text(encoding="utf-8")
        except OSError as e:
            return None, None, f"could not read source file: {e}"
        err = _render_via_sidecar(
            xml=xml,
            fmt=fmt,
            out_path=out_path,
            sidecar_url=settings.DRAWIO_EXPORT_URL,
            timeout=settings.DRAWIO_EXPORT_TIMEOUT_SECONDS,
        )
        mode = "sidecar"
    else:
        err = _render_via_cli(target=target, fmt=fmt, out_path=out_path)
        mode = "local CLI"

    if err is not None:
        return None, None, err
    if not out_path.exists():
        return None, None, f"rendering completed but output file {out_path.name} not found"
    return out_path, mode, None


class RenderDrawioTool(Tool):
    name = "render_drawio"
    config_flag = "TOOL_RENDER_DRAWIO_ENABLED"
    is_diagram_tool = True      # was orchestrator _DRAWIO_TOOLS
    attaches_render = True   # fresh PNG on success - orchestrator attaches + vision-reviews
    description = (
        "Render a .drawio file in output/ to an image (PNG by default) so you can "
        "visually review the rendered diagram. generate_file already auto-renders "
        ".drawio writes, so call this only when you want to re-render an existing "
        "file (e.g. after manual XML edits) or pick a non-PNG format. "
        "When format is png/jpg, the rendered image is automatically attached to the "
        "next model turn for vision-based review. "
        "Uses a drawio-image-export2 sidecar when DRAWIO_EXPORT_URL is configured "
        "(production), otherwise falls back to a local draw.io desktop install (dev)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Filename of the .drawio file in output/ (e.g. 'arch.drawio'). "
                    "The output uses the same basename with the chosen extension."
                ),
            },
            "format": {
                "type": "string",
                "description": "Export format. One of: png, svg, pdf, jpg. Default: png.",
                "enum": ["png", "svg", "pdf", "jpg"],
            },
        },
        "required": ["filename"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        filename = (args.get("filename") or args.get("file_name") or "").strip()
        fmt = (args.get("format") or "png").strip().lower()

        if not filename:
            return "Error: filename is required."

        out_path, mode, err = render_drawio_to_disk(filename, fmt)
        if err is not None or out_path is None:
            return f"Error: {err}"

        size_kb = out_path.stat().st_size // 1024
        logger.info(
            "Rendered %s -> %s (%d KB) via %s for %s",
            filename, out_path.name, size_kb, mode, user.email,
        )
        vision_note = (
            "The rendered image is being attached to the next turn for visual review. "
            "Inspect it and look for:\n"
            if fmt in ("png", "jpg")
            else "Visually review the image for issues the structural validator cannot detect:\n"
        )
        return (
            f"Rendered: output/{out_path.name} ({size_kb} KB, via {mode})\n"
            f"Full path: {out_path}\n\n"
            f"{vision_note}"
            "  - Edge labels overlapping icons or other labels\n"
            "  - Numbered badges sitting on top of edge labels\n"
            "  - Long edges routed through busy areas\n"
            "  - Ambiguous bidirectional arrows\n"
            "  - Auxiliary zones (monitoring, identity, DNS) far from connected resources\n\n"
            "If you find issues, edit the source .drawio with overwrite=true and re-render."
        )
