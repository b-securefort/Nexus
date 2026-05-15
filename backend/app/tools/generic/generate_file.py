"""
File generation tool — sandboxed file writes to an output directory.
No approval needed (writes only to output/ sandbox).
"""

import logging
import re
from pathlib import Path

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Sandbox directory relative to backend CWD
_OUTPUT_DIR = Path("output")

# Max file size: 1 MB
_MAX_FILE_SIZE = 1_048_576

# Allowed extensions (whitelist)
_ALLOWED_EXTENSIONS = {
    # IaC
    ".bicep", ".tf", ".json", ".yaml", ".yml",
    # Scripts
    ".ps1", ".sh", ".bash", ".py",
    # Config
    ".env", ".ini", ".toml", ".cfg", ".conf",
    # Docs
    ".md", ".txt", ".rst", ".csv",
    # Web
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx",
    # Data
    ".xml", ".sql",".drawio"
}

# Dangerous patterns in filenames
_DANGEROUS_PATTERNS = re.compile(r"\.\.|[<>:\"|?*\x00-\x1f]|^/|^\\")


class GenerateFileTool(Tool):
    name = "generate_file"
    description = (
        "Write generated content (Bicep, Terraform, scripts, configs, docs) to the "
        "output/ directory. Sandboxed — only writes to output/ so it's safe. "
        "Use this when the user asks to generate, create, or save a file. "
        "The file can be downloaded or reviewed by the user. "
        "If the file already exists, set overwrite=true to replace it."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Target filename with extension, optionally with subdirectories. "
                    "Examples: 'main.bicep', 'scripts/deploy.ps1', 'infra/modules/storage.tf'. "
                    "Path traversal (../) is blocked."
                ),
            },
            "content": {
                "type": "string",
                "description": "The file content to write.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "If true, overwrite an existing file. Default: false.",
            },
        },
        "required": ["filename", "content"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        if not isinstance(args, dict):
            return "Error: invalid arguments — expected an object with filename and content"
        # Accept common synonyms — smaller LLMs sometimes hallucinate
        # parameter keys. The schema documents `filename` / `content`,
        # but we forgive the most frequent variants rather than fail.
        filename = (
            args.get("filename")
            or args.get("file_name")
            or args.get("name")
            or args.get("path")
            or args.get("file_path")
            or ""
        )
        content = (
            args.get("content")
            or args.get("body")
            or args.get("text")
            or args.get("data")
            or args.get("file_content")
            or ""
        )
        overwrite = args.get("overwrite", False)

        if not filename:
            keys = ", ".join(sorted(args.keys())) or "(none)"
            return (
                "Error: filename is required. Pass it as the `filename` parameter. "
                f"Received keys: {keys}"
            )
        if not content:
            keys = ", ".join(sorted(args.keys())) or "(none)"
            return (
                "Error: content is required. Pass the file body as the `content` parameter. "
                f"Received keys: {keys}"
            )

        # Security: block path traversal and dangerous chars
        if _DANGEROUS_PATTERNS.search(filename):
            return "Error: Invalid filename — path traversal or special characters are not allowed."

        # Validate extension
        ext = Path(filename).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return (
                f"Error: File extension '{ext}' is not allowed. "
                f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            )

        # Size check (and reject content that isn't valid UTF-8 — lone
        # surrogates from the LLM tokenizer would crash write_text otherwise).
        try:
            encoded_len = len(content.encode("utf-8"))
        except UnicodeEncodeError as e:
            return (
                f"Error: content contains characters that cannot be encoded "
                f"as UTF-8 ({e.reason} at position {e.start}). Strip lone "
                "surrogates or non-text bytes before retrying."
            )
        if encoded_len > _MAX_FILE_SIZE:
            return f"Error: Content exceeds maximum file size of {_MAX_FILE_SIZE // 1024}KB"

        # Resolve target path within sandbox
        target = (_OUTPUT_DIR / filename).resolve()
        sandbox = _OUTPUT_DIR.resolve()

        # Double-check the resolved path is still within sandbox
        try:
            target.relative_to(sandbox)
        except ValueError:
            return "Error: Resolved path escapes the output/ sandbox."

        # Check existing file
        if target.exists() and not overwrite:
            return (
                f"Error: File '{filename}' already exists. "
                "Set overwrite=true to replace it, or choose a different name."
            )

        # Create directories and write
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            size = target.stat().st_size
            logger.info("Generated file: %s (%d bytes) by %s", filename, size, user.email)
            result = (
                f"File saved: output/{filename} ({size} bytes)\n"
                f"Full path: {target}"
            )
            if ext == ".drawio":
                from app.tools.validate_drawio import validate_drawio_file
                report = validate_drawio_file(target)
                result += f"\n\nAuto-validation:\n{report}"

                # Auto-render to PNG so the agent's vision feedback loop fires
                # on EVERY diagram generation, not only when the model thinks
                # to call render_drawio. The orchestrator detects the resulting
                # <stem>.png on disk and inlines it into the next model turn.
                # Best-effort: a render failure (drawio not installed, sidecar
                # unreachable) doesn't fail the write.
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
                            f"({size_kb} KB, via {mode}). The image is being "
                            "attached to your next turn for visual review."
                        )
                    elif render_err:
                        # Surface only when the cause might be actionable; the
                        # 'draw.io desktop is not installed' case is benign in
                        # dev environments and would just add noise.
                        if "not installed" not in render_err.lower():
                            result += f"\n\nAuto-render skipped: {render_err}"
            return result
        except OSError as e:
            return f"Error writing file: {e}"
