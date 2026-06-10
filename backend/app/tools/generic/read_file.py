"""
read_file — read a file previously written into the output/ sandbox.

Symmetric with generate_file. Same sandbox boundary (output/), same path-traversal
defence (regex + Path.resolve().relative_to(sandbox)). No approval needed since
the only content this tool can see is what the agent itself put there via
generate_file — or what a typed write-tool deliberately produced (drawio render,
python_diagram, etc.).
"""

import logging
import re
from pathlib import Path

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")

# Max bytes returned to the model. Anything larger is truncated with a
# trailing marker so the model can decide whether to fetch in chunks via
# offset/limit if we add that later.
_DEFAULT_MAX_BYTES = 65_536
_HARD_MAX_BYTES = 1_048_576

# Same regex as generate_file — defence-in-depth on path inputs before the
# Path.resolve() check. Catches `..`, absolute paths, NUL bytes, and Windows
# reserved characters.
_DANGEROUS_PATTERNS = re.compile(r"\.\.|[<>:\"|?*\x00-\x1f]|^/|^\\")


class ReadFileTool(Tool):
    name = "read_file"
    result_limit = 24_000        # was orchestrator _TOOL_RESULT_LIMITS
    description = (
        "Read a file previously written to the output/ sandbox by generate_file "
        "or a diagram/render tool. Path must be relative to output/ "
        "(e.g. 'logicapp-patch.json', 'scripts/list-resources.ps1'). "
        "Returns the file content as text, truncated at max_bytes if larger."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to output/. Examples: 'logicapp-patch.json', "
                    "'scripts/list-resources.ps1'. Path traversal (../) is blocked."
                ),
            },
            "max_bytes": {
                "type": "integer",
                "description": (
                    f"Maximum bytes to return. Default {_DEFAULT_MAX_BYTES}, "
                    f"hard cap {_HARD_MAX_BYTES}."
                ),
            },
        },
        "required": ["path"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        if not isinstance(args, dict):
            return "Error: invalid arguments — expected an object with path"

        path = (args.get("path") or args.get("file_path") or args.get("filename") or "")
        if not isinstance(path, str) or not path:
            return "Error: path is required (relative to output/, e.g. 'logicapp-patch.json')"

        raw_max = args.get("max_bytes", _DEFAULT_MAX_BYTES)
        try:
            max_bytes = int(raw_max)
        except (TypeError, ValueError):
            return f"Error: max_bytes must be an integer, got {raw_max!r}"
        if max_bytes <= 0:
            return "Error: max_bytes must be > 0"
        max_bytes = min(max_bytes, _HARD_MAX_BYTES)

        if _DANGEROUS_PATTERNS.search(path):
            return "Error: Invalid path — path traversal or special characters are not allowed."

        target = (_OUTPUT_DIR / path).resolve()
        sandbox = _OUTPUT_DIR.resolve()
        try:
            target.relative_to(sandbox)
        except ValueError:
            return "Error: Resolved path escapes the output/ sandbox."

        if not target.exists():
            return f"Error: File not found: output/{path}"
        if not target.is_file():
            return f"Error: Not a regular file: output/{path}"

        try:
            data = target.read_bytes()
        except OSError as e:
            return f"Error reading file: {e}"

        total = len(data)
        truncated = total > max_bytes
        chunk = data[:max_bytes]

        # Decode as UTF-8 with replacement so binary contamination doesn't crash
        # the tool — the model will see the � marker and can decide to skip.
        text = chunk.decode("utf-8", errors="replace")

        header = f"File: output/{path} ({total} bytes"
        if truncated:
            header += f", showing first {max_bytes})"
        else:
            header += ")"
        return f"{header}\n{text}"
