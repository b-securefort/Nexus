"""
Azure REST API tool — direct ARM/management API calls via az rest.
GET requests are read-only (no approval). Mutations require approval.
"""

import json
import logging
import re
from pathlib import Path

from app.auth.models import User
from bundles.azure._az_base import AzureToolBase, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)

# HTTP methods that are read-only
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Sandbox for body_file resolution — same boundary as generate_file/read_file.
_OUTPUT_DIR = Path("output")

# Path-input guard for body_file (mirrors generate_file/read_file).
_DANGEROUS_PATH_PATTERNS = re.compile(r"\.\.|[<>:\"|?*\x00-\x1f]|^/|^\\")

# Maximum body payload size accepted from body_file (bytes). Larger payloads
# are almost always model error rather than a legitimate ARM request shape.
_MAX_BODY_BYTES = 1_048_576

# How much of a request body the risk reviewer is shown. Real ARM payloads are
# a few KB, so this displays essentially every legitimate mutation in full; a
# body larger than this is the rare, already-suspicious case and is both marked
# truncated to the reviewer AND floored to ⛔ by `risk_floor` so the unseen
# remainder can never pass silently at ⚠ (DESIGN.md §5 2026-06-12).
_REVIEW_BODY_WINDOW = 16384


def _truncation_marker(shown_bytes: int, total_bytes: int) -> str:
    return (
        f"\n[payload truncated: showing first {shown_bytes} of "
        f"{total_bytes} bytes — remainder NOT reviewed]"
    )


class AzRestApiTool(AzureToolBase):
    name = "az_rest_api"
    config_flag = "TOOL_AZ_REST_ENABLED"
    learning_eligible = True    # was orchestrator _LEARNING_ELIGIBLE_TOOLS
    rate_limit_calls = 10
    description = (
        "Call any Azure Resource Manager REST API directly using 'az rest'. "
        "Use this as a last resort when az_resource_graph and az_cli don't support the operation. "
        "GET requests do not require approval; PUT/POST/PATCH/DELETE require approval. "
        "Provide the full API URL or a relative path starting with /subscriptions/. "
        "For mutation payloads larger than ~3 KB or containing HTML/embedded quotes, "
        "write the body to output/ with generate_file first and pass it via `body_file` "
        "instead of `body` — this avoids escaping issues in large JSON payloads."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "PUT", "POST", "PATCH", "DELETE"],
                "description": "HTTP method. GET is read-only, others require approval.",
            },
            "url": {
                "type": "string",
                "description": (
                    "Azure REST API URL. Can be:\n"
                    "- Full: https://management.azure.com/subscriptions/{sub}/...\n"
                    "- Relative: /subscriptions/{sub}/resourceGroups/{rg}/...\n"
                    "Include api-version as a query parameter."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "JSON request body for PUT/POST/PATCH, passed inline. Must be valid JSON. "
                    "Use this only for small payloads — escape round-tripping can corrupt "
                    "bodies larger than ~3 KB. For larger payloads, prefer `body_file`."
                ),
            },
            "body_file": {
                "type": "string",
                "description": (
                    "Path under output/ to a file whose contents are the request body "
                    "(e.g. 'logicapp-patch.json'). The tool reads the file from disk "
                    "and forwards it to `az rest --body @<resolved_path>`, avoiding the "
                    "string-escape round-trips that mangle large inline bodies. "
                    "Write the file with generate_file first. Mutually exclusive with `body`."
                ),
            },
        },
        "required": ["method", "url"],
    }

    @property
    def requires_approval(self) -> bool:  # type: ignore[override]
        # Approval is dynamic per HTTP method, not static: the orchestrator's
        # _tool_needs_approval() duck-types `_needs_approval(method)` below and
        # gates mutations there (GET/HEAD are free, PUT/POST/PATCH/DELETE need
        # approval). This static attribute is the fallback for callers that
        # don't special-case the dynamic path; it must stay False so a bare
        # read isn't gated. execute() does NOT re-check — keep the two in sync.
        return False

    def _needs_approval(self, method: str) -> bool:
        return method.upper() not in _SAFE_METHODS

    @staticmethod
    def _resolve_body_file(body_file: str) -> tuple[Path | None, str | None]:
        """Resolve a body_file argument to an absolute path under output/.

        Returns (resolved_path, None) on success or (None, error_message) on
        any guard failure. Same defence-in-depth as generate_file/read_file:
        regex on the raw input then Path.resolve().relative_to(sandbox).
        """
        if not isinstance(body_file, str) or not body_file:
            return None, "Error: body_file must be a non-empty string path under output/"
        if _DANGEROUS_PATH_PATTERNS.search(body_file):
            return None, "Error: body_file contains path traversal or special characters."
        target = (_OUTPUT_DIR / body_file).resolve()
        sandbox = _OUTPUT_DIR.resolve()
        try:
            target.relative_to(sandbox)
        except ValueError:
            return None, "Error: body_file resolves outside the output/ sandbox."
        if not target.exists():
            return None, f"Error: body_file not found: output/{body_file}"
        if not target.is_file():
            return None, f"Error: body_file is not a regular file: output/{body_file}"
        try:
            size = target.stat().st_size
        except OSError as e:
            return None, f"Error: cannot stat body_file: {e}"
        if size > _MAX_BODY_BYTES:
            return None, (
                f"Error: body_file too large ({size} bytes, max {_MAX_BODY_BYTES}). "
                "Split the request or use a deployment template instead."
            )
        return target, None

    # ── Risk-review hooks (DESIGN.md §5 2026-06-12) ──────────────────────────
    # Duck-typed, read by risk_review via the registry — core never imports this
    # bundle. render_for_review lets the reviewer judge the actual payload (not a
    # filename); risk_floor escalates an oversized, unreviewable mutation body.

    def render_for_review(
        self, func_args: dict, max_bytes: int | None = _REVIEW_BODY_WINDOW
    ) -> tuple[str, bool]:
        """Resolve the command for display, inlining the real request body
        (reading `body_file` from the output/ sandbox) so a mutation is judged on
        its payload, not a pointer. Content past `max_bytes` is cut with a
        truncation marker; `max_bytes=None` means uncapped (download path).
        Returns (rendered, truncated). The reviewer uses the 16 KB default; the
        human card passes 64 KB; the download endpoint passes None."""
        method = str(func_args.get("method", "GET")).upper()
        url = func_args.get("url", "")
        body_text, truncated = self._body_for_review(func_args, max_bytes)
        return f"{method} {url}\n{body_text}".strip(), truncated

    def risk_floor(self, func_args: dict) -> str | None:
        """Floor a mutation whose body exceeds the reviewer's window to the
        literal tier "destructive" — an unreviewable payload must force a careful
        read rather than passing at ⚠. Size-checked (inline `len` / file `stat`),
        never re-read. Returns None for reads and in-window bodies, leaving the
        method-based floor in risk_review to classify. Stays equal to
        `risk_review.DESTRUCTIVE` — a test guards the drift."""
        method = str(func_args.get("method", "GET")).upper()
        if method in _SAFE_METHODS:
            return None
        inline = func_args.get("body")
        if isinstance(inline, str) and len(inline) > _REVIEW_BODY_WINDOW:
            return "destructive"
        body_file = func_args.get("body_file")
        if isinstance(body_file, str) and body_file:
            target, err = self._resolve_body_file(body_file)
            if err is None and target is not None:
                try:
                    if target.stat().st_size > _REVIEW_BODY_WINDOW:
                        return "destructive"
                except OSError:
                    return None
        return None

    def _body_for_review(self, func_args: dict, max_bytes: int | None) -> tuple[str, bool]:
        """Return (display_text, truncated) for the request body, reading at most
        `max_bytes` (None = uncapped). `truncated` is True when the full body
        exceeds `max_bytes`. Inline `body` wins over `body_file` (mirrors the
        mutual-exclusion check in execute)."""
        inline = func_args.get("body")
        if isinstance(inline, str) and inline:
            if max_bytes is None or len(inline) <= max_bytes:
                return inline, False
            return inline[:max_bytes] + _truncation_marker(max_bytes, len(inline)), True
        body_file = func_args.get("body_file")
        if isinstance(body_file, str) and body_file:
            target, err = self._resolve_body_file(body_file)
            if err or target is None:
                return f"(body_file unreadable: {err})", False
            try:
                data = target.read_bytes()
            except OSError as e:
                return f"(body_file read error: {e})", False
            if max_bytes is None or len(data) <= max_bytes:
                return data.decode("utf-8", errors="replace"), False
            text = data[:max_bytes].decode("utf-8", errors="replace")
            return text + _truncation_marker(max_bytes, len(data)), True
        return "", False

    def execute(self, args: dict, user: User) -> str:
        login_err = require_az_login()
        if login_err:
            return login_err

        method = args.get("method", "GET").upper()
        url = args.get("url", "")
        body = args.get("body", "")
        body_file_arg = args.get("body_file", "")

        if not url:
            return "Error: url is required"

        # Validate URL doesn't point outside Azure management
        if url.startswith("http") and "management.azure.com" not in url and "graph.microsoft.com" not in url:
            return (
                "Error: URL must be an Azure management API URL "
                "(management.azure.com or graph.microsoft.com) or a relative path."
            )

        # body and body_file are mutually exclusive — using both would be ambiguous
        # about which payload wins.
        if body and body_file_arg:
            return (
                "Error: pass either `body` (inline JSON) or `body_file` (path under "
                "output/), not both. body_file is preferred for payloads larger than "
                "a few KB."
            )

        body_file_path: Path | None = None
        if body_file_arg:
            body_file_path, body_file_err = self._resolve_body_file(body_file_arg)
            if body_file_err:
                return body_file_err
            if method in _SAFE_METHODS:
                return (
                    f"Error: body_file is not allowed for {method} requests "
                    "(read methods don't accept a body)."
                )
            # Sanity-check the file is parseable JSON BEFORE invoking az rest, so
            # we surface a clear error instead of az's generic 'Invalid JSON body'.
            try:
                with open(body_file_path, "r", encoding="utf-8") as fh:
                    json.load(fh)
            except json.JSONDecodeError as e:
                return f"Error: body_file is not valid JSON: {e}"
            except OSError as e:
                return f"Error reading body_file: {e}"

        # Validate inline body is valid JSON if provided
        if body:
            try:
                json.loads(body)
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON body: {e}"

        cmd = [_find_az(), "rest", "--method", method, "--url", url, "--output", "json"]

        if body and method not in _SAFE_METHODS:
            cmd.extend(["--body", body])
        elif body_file_path is not None:
            # az rest accepts `--body @<path>` to load the request body from disk.
            # We pass the resolved absolute path so az doesn't re-resolve against
            # an unexpected cwd.
            cmd.extend(["--body", f"@{body_file_path}"])

        result_str = self._run_az(cmd, label=f"{method} {url}", timeout=60)
        return result_str if result_str else f"{method} {url} — success (no response body)"
