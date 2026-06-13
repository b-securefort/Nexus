"""
execute_script — run a script that already exists under output/scripts/.

Replaces the previous arbitrary-command `run_shell` tool. The model can no
longer pass an inline command string; it must point at a file the agent
itself (or a typed write-tool) deliberately produced. The shell is inferred
from the file extension (.ps1 → PowerShell, .sh → bash). Always
approval-gated — the script's author still has full host privileges once
the script runs.

This narrowing was driven by the conv 257 / 173 / 174 analysis: every
legitimate run_shell call referenced a script in output/scripts/; every
abuse passed an inline command. Removing the inline surface removes the
abuse class structurally.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Generator

from app.auth.models import User
from app.tools.base import (
    Tool,
    consume_stream,
    stream_subprocess,
)

logger = logging.getLogger(__name__)

# Sandbox: scripts must live under output/scripts/ (a subdirectory of the
# existing generate_file sandbox). Resolving inside this narrower root means
# the model cannot execute a JSON payload or .drawio file by mistake.
_OUTPUT_DIR = Path("output")
_SCRIPTS_DIR = _OUTPUT_DIR / "scripts"

# Working directory for the subprocess. Matches the previous run_shell tool's
# cwd so existing scripts that reference `.\output\...` still resolve. Keep
# this explicit — never inherit cwd from whichever request thread happens to
# be running.
_WORK_DIR = Path(__file__).resolve().parent.parent.parent

# Extensions we know how to run. Anything else gets rejected — generate_file
# accepts .py and others, but we deliberately don't execute Python or batch
# files here (Python should use a typed tool; .bat is a Windows footgun).
_SCRIPT_EXTENSIONS = {".ps1": "powershell", ".sh": "bash"}

# Max script output size before truncation
_MAX_OUTPUT_SIZE = 8192

# How much of a script body the risk reviewer is shown. Mirrors the az_rest body
# window (DESIGN.md §5 2026-06-12): real diagnostic scripts fit easily, and a
# body larger than this is both marked truncated to the reviewer AND floored to
# ⛔ by risk_floor — closing the append-after-truncation gap where a destructive
# tail past the old 4000-char cut was never seen by the review LLM.
_REVIEW_BODY_WINDOW = 16384


def _review_truncation_marker(shown_bytes: int, total_bytes: int) -> str:
    return (
        f"\n[script truncated: showing first {shown_bytes} of "
        f"{total_bytes} bytes — remainder NOT reviewed]"
    )

# Same regex as generate_file/read_file — defence-in-depth on raw path input.
_DANGEROUS_PATH_PATTERNS = re.compile(r"\.\.|[<>:\"|?*\x00-\x1f]|^/|^\\")


def _normalize_timeout(raw, default: int = 30, max_: int = 120) -> tuple[int | None, str | None]:
    """Coerce timeout_seconds to a positive int in [1, max_]."""
    if raw is None:
        return default, None
    try:
        t = int(raw)
    except (TypeError, ValueError):
        return None, f"Error: timeout_seconds must be a positive integer, got {raw!r}"
    if t <= 0:
        return None, f"Error: timeout_seconds must be greater than 0, got {t}"
    return min(t, max_), None


def _resolve_script(path: str) -> tuple[Path | None, str | None]:
    """Resolve the script path under output/scripts/ with the standard guards.

    Returns (resolved_path, None) on success or (None, error_message) on any
    guard failure.
    """
    if _DANGEROUS_PATH_PATTERNS.search(path):
        return None, "Error: path contains path traversal or special characters."

    # Accept either a path that already starts with 'scripts/' (so the
    # model can call execute_script({path: 'scripts/foo.ps1'}) consistent
    # with how it would call generate_file with the same filename) OR a
    # bare filename which we resolve under scripts/ for convenience.
    p = Path(path)
    if p.parts and p.parts[0] == "scripts":
        candidate = (_OUTPUT_DIR / p).resolve()
    else:
        candidate = (_SCRIPTS_DIR / p).resolve()
    sandbox = _SCRIPTS_DIR.resolve()
    try:
        candidate.relative_to(sandbox)
    except ValueError:
        return None, "Error: resolved path escapes the output/scripts/ sandbox."

    if not candidate.exists():
        return None, f"Error: script not found at output/scripts/{p.name} (resolved: {candidate})"
    if not candidate.is_file():
        return None, f"Error: not a regular file: {candidate}"

    ext = candidate.suffix.lower()
    if ext not in _SCRIPT_EXTENSIONS:
        return None, (
            f"Error: extension '{ext}' is not executable by execute_script. "
            f"Allowed: {', '.join(sorted(_SCRIPT_EXTENSIONS))}"
        )

    return candidate, None


def _build_cmd(script_path: Path) -> list[str]:
    ext = script_path.suffix.lower()
    if ext == ".ps1":
        # Prefer pwsh (PowerShell 7+, cross-platform) and fall back to
        # Windows PowerShell. -NoProfile and -NonInteractive keep the host's
        # profile and prompts out of the picture.
        ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [
            ps, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
        ]
    if ext == ".sh":
        bash = shutil.which("bash") or "bash"
        return [bash, str(script_path)]
    # _resolve_script already gated extensions; this path is unreachable.
    raise RuntimeError(f"unsupported script extension: {ext}")


def _shell_env() -> dict[str, str]:
    """Minimal env for the script subprocess. Matches the spirit of the §5
    2026-05-21 _run_az allowlist — strip everything that isn't necessary.
    """
    keys = (
        "PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP",
        "APPDATA", "LOCALAPPDATA", "PSModulePath", "ProgramFiles",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    )
    env = {k: v for k, v in os.environ.items() if k in keys}
    env["TERM"] = "dumb"
    return env


class ExecuteScriptTool(Tool):
    name = "execute_script"
    config_flag = "TOOL_SHELL_ENABLED"
    retry_eligible = True       # was orchestrator _COMMAND_TOOLS
    learning_eligible = True    # was orchestrator _LEARNING_ELIGIBLE_TOOLS
    result_limit = 12_000        # was orchestrator _TOOL_RESULT_LIMITS

    def retry_docs_query(self, func_args: dict, error_text: str) -> str | None:
        path = func_args.get("path", "")
        return f"{path[:80]} script error"

    def retry_alt_hint(self) -> str | None:
        return (
            "Don't retry the same script. Inspect it with `read_file`, fix it "
            "with `generate_file` (overwrite=true), and re-run."
        )

    # ── Risk-review hooks (DESIGN.md §5 2026-06-12) ──────────────────────────
    # Duck-typed, read by risk_review via the registry. render_for_review shows
    # the reviewer the resolved script body (replacing the old body[:4000] slice);
    # risk_floor escalates an over-window, unreviewable-length script to ⛔.

    def render_for_review(
        self, func_args: dict, max_bytes: int | None = _REVIEW_BODY_WINDOW
    ) -> tuple[str, bool]:
        """Render the script for display, inlining its resolved body up to
        `max_bytes` (None = uncapped) with a truncation marker when longer.
        Returns (rendered, truncated). The reviewer uses the 16 KB default; the
        human card passes 64 KB; the download endpoint passes None."""
        raw_path = func_args.get("path") or func_args.get("script") or func_args.get("file") or ""
        label = raw_path or "?"
        script_path, err = _resolve_script(raw_path)
        if err or script_path is None:
            return f"execute script {label} (body could not be read)", False
        try:
            data = script_path.read_bytes()
        except OSError as e:
            return f"execute script {label} (body read error: {e})", False
        if max_bytes is None or len(data) <= max_bytes:
            return f"execute script {label}:\n{data.decode('utf-8', errors='replace')}", False
        text = data[:max_bytes].decode("utf-8", errors="replace")
        text += _review_truncation_marker(max_bytes, len(data))
        return f"execute script {label}:\n{text}", True

    def risk_floor(self, func_args: dict) -> str | None:
        """Floor an over-window (unreviewable-length) script to the literal tier
        "destructive" — the reviewer LLM only sees the first `_REVIEW_BODY_WINDOW`
        bytes, so a longer body must escalate rather than pass on a partial view.
        Size is checked by `stat`, never re-read. Returns None otherwise, leaving
        the full-body shell-pattern floor (`_shell_floor` in risk_review) to
        classify. Stays equal to `risk_review.DESTRUCTIVE` — a test guards it."""
        raw_path = func_args.get("path") or func_args.get("script") or func_args.get("file") or ""
        script_path, err = _resolve_script(raw_path)
        if err or script_path is None:
            return None
        try:
            if script_path.stat().st_size > _REVIEW_BODY_WINDOW:
                return "destructive"
        except OSError:
            return None
        return None

    description = (
        "Execute a script that already exists under output/scripts/. "
        "Requires explicit user approval before execution. "
        "Path can be 'scripts/foo.ps1' or just 'foo.ps1' — both resolve under "
        "output/scripts/. Shell is inferred from the file extension: "
        ".ps1 → PowerShell, .sh → bash. Other extensions are rejected. "
        "Write the script with generate_file first; this tool does not accept "
        "inline command strings. Returns stdout/stderr and exit code."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Script path relative to output/scripts/ (or 'scripts/<name>'). "
                    "Path traversal is blocked."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this script needs to be run.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 120)",
                "default": 30,
            },
        },
        "required": ["path", "reason"],
    }
    requires_approval = True

    def execute(self, args: dict, user: User) -> str:
        # Single implementation: drain the streaming path (§5 2026-06-13) so
        # the kill-switch registration and watchdog deadline exist exactly once.
        return consume_stream(self.execute_streaming(args, user))

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        """Streamed variant — yields stdout lines as they arrive.

        Mirrors the previous run_shell streaming surface so the orchestrator
        SSE pipeline doesn't need changes.
        """
        if not isinstance(args, dict):
            err = "Error: invalid arguments — expected an object with path and reason"
            yield err
            return err

        raw_path = args.get("path") or args.get("script") or args.get("file") or ""
        if not isinstance(raw_path, str) or not raw_path:
            err = "Error: path is required (relative to output/scripts/)"
            yield err
            return err

        timeout, terr = _normalize_timeout(args.get("timeout_seconds"))
        if terr:
            yield terr
            return terr

        script_path, path_err = _resolve_script(raw_path)
        if path_err or script_path is None:
            err = path_err or "Error: unable to resolve script path"
            yield err
            return err

        cmd = _build_cmd(script_path)
        env = _shell_env()

        try:
            # The shared runner owns the lifecycle: killable process group,
            # kill-switch registration (Stop / disconnect kills the whole tree —
            # pwsh → az → python — see §5 2026-06-04), and the wall-clock
            # watchdog deadline (§5 2026-06-13).
            res = yield from stream_subprocess(
                cmd, env=env, cwd=str(_WORK_DIR), timeout=timeout,
            )
        except FileNotFoundError as e:
            err = f"Error: interpreter not found: {e}"
            yield err
            return err
        except Exception as e:
            logger.error("execute_script streaming error for %s: %s", script_path, e)
            err = f"Error: {e}"
            yield err
            return err

        # returncode!=0 guard: a clean exit that races the watchdog is a success.
        if res.timed_out and res.returncode != 0:
            err = f"Error: script timed out after {timeout} seconds"
            yield err
            return err

        full = f"Exit code: {res.returncode}\n"
        if res.stdout:
            full += f"--- stdout ---\n{res.stdout}\n"
        if res.stderr:
            full += f"--- stderr ---\n{res.stderr}\n"
            yield f"--- stderr ---\n{res.stderr}"

        if len(full) > _MAX_OUTPUT_SIZE:
            full = full[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
        # Surface non-zero exit as an error for retry/learning (is_error).
        if res.returncode != 0:
            return f"Error: script exited with code {res.returncode}.\n{full}"
        return full
