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
import subprocess
import sys
import threading
from pathlib import Path
from typing import Generator

from app.auth.models import User
from app.tools.base import (
    SUBPROCESS_FLAGS,
    Tool,
    get_conversation_id,
    register_process,
    unregister_process,
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
    result_limit = 4_000        # was orchestrator _TOOL_RESULT_LIMITS

    def retry_docs_query(self, func_args: dict, error_text: str) -> str | None:
        path = func_args.get("path", "")
        return f"{path[:80]} script error"

    def retry_alt_hint(self) -> str | None:
        return (
            "Don't retry the same script. Inspect it with `read_file`, fix it "
            "with `generate_file` (overwrite=true), and re-run."
        )

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
        if not isinstance(args, dict):
            return "Error: invalid arguments — expected an object with path and reason"

        raw_path = args.get("path") or args.get("script") or args.get("file") or ""
        if not isinstance(raw_path, str) or not raw_path:
            return "Error: path is required (relative to output/scripts/)"

        timeout, err = _normalize_timeout(args.get("timeout_seconds"))
        if err:
            return err

        script_path, path_err = _resolve_script(raw_path)
        if path_err or script_path is None:
            return path_err or "Error: unable to resolve script path"

        cmd = _build_cmd(script_path)
        env = _shell_env()

        try:
            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(_WORK_DIR),
                env=env,
                **SUBPROCESS_FLAGS,
            )
            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"--- stdout ---\n{result.stdout}\n"
            if result.stderr:
                output += f"--- stderr ---\n{result.stderr}\n"
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            # Non-zero exit is a real failure — prefix "Error:" so the
            # orchestrator detects it (is_error), retries, and can learn the fix.
            if result.returncode != 0:
                return f"Error: script exited with code {result.returncode}.\n{output}"
            return output
        except subprocess.TimeoutExpired:
            return f"Error: script timed out after {timeout} seconds"
        except FileNotFoundError as e:
            return f"Error: interpreter not found: {e}"
        except Exception as e:
            logger.error("execute_script error for %s: %s", script_path, e)
            return f"Error: {e}"

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

        # Launch in its own process group so the Stop / disconnect path can kill
        # the whole tree (pwsh → az → python). On POSIX `start_new_session=True`
        # gives a killable group for os.killpg; on Windows taskkill /T walks the
        # PID tree, so no extra flag is needed. See DESIGN.md §5 2026-06-04.
        popen_kwargs = dict(SUBPROCESS_FLAGS)
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        conv_id = get_conversation_id()
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(_WORK_DIR),
                env=env,
                **popen_kwargs,
            )
            # Register for the kill switch. If the turn is stopped, the
            # orchestrator kills this tree; the read loop below then hits EOF and
            # this thread unwinds.
            register_process(conv_id, proc)
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            def _read_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_lines.append(line)

            t = threading.Thread(target=_read_stderr, daemon=True)
            t.start()

            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_lines.append(line)
                yield line

            proc.wait(timeout=timeout)
            t.join(timeout=5)

            full = f"Exit code: {proc.returncode}\n"
            if stdout_lines:
                full += f"--- stdout ---\n{''.join(stdout_lines)}\n"
            if stderr_lines:
                full += f"--- stderr ---\n{''.join(stderr_lines)}\n"
                yield f"--- stderr ---\n{''.join(stderr_lines)}"

            if len(full) > _MAX_OUTPUT_SIZE:
                full = full[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            # See execute(): surface non-zero exit as an error for retry/learning.
            if proc.returncode != 0:
                return f"Error: script exited with code {proc.returncode}.\n{full}"
            return full
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            err = f"Error: script timed out after {timeout} seconds"
            yield err
            return err
        except Exception as e:
            logger.error("execute_script streaming error for %s: %s", script_path, e)
            err = f"Error: {e}"
            yield err
            return err
        finally:
            if proc is not None:
                unregister_process(conv_id, proc)
