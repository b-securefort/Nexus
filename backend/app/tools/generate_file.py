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
        filename = args.get("filename", "")
        content = args.get("content", "")
        overwrite = args.get("overwrite", False)

        if not filename:
            return "Error: filename is required"
        if not content:
            return "Error: content is required"

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

        # Size check
        if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
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
            return result
        except OSError as e:
            return f"Error writing file: {e}"
