"""
Shell tool — runs commands with user approval.
"""

import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Generator

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool

logger = logging.getLogger(__name__)

# Max output size in bytes
_MAX_OUTPUT_SIZE = 8192


# Working directory = backend project root (where output/ lives)
_WORK_DIR = Path(__file__).resolve().parent.parent.parent


# B3: pattern that reliably causes "Cannot run a document in the middle of a
# pipeline" in PowerShell — piping the az executable output directly instead
# of capturing it in a variable first.
# Matches: az <any args> | <something>  (on a single logical line)
_PS_AZ_PIPE_RE = re.compile(r'\baz(?:\.cmd)?\b[^|\n]+\|', re.IGNORECASE)


def _normalize_timeout(raw, default: int = 30, max_: int = 120) -> tuple[int | None, str | None]:
    """Coerce timeout_seconds to a positive int in [1, max_].

    Returns (timeout, None) on success, (None, error_string) on failure.
    Strings, negatives, zero, and non-numeric junk all return a clean error
    so the caller never propagates a TypeError or ValueError from subprocess.
    """
    if raw is None:
        return default, None
    try:
        t = int(raw)
    except (TypeError, ValueError):
        return None, f"Error: timeout_seconds must be a positive integer, got {raw!r}"
    if t <= 0:
        return None, f"Error: timeout_seconds must be greater than 0, got {t}"
    return min(t, max_), None


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Execute a shell or PowerShell command. Requires explicit user approval before execution. "
        "Returns stdout, stderr, and exit code. "
        "IMPORTANT: To run PowerShell cmdlets or .ps1 scripts, you MUST set shell='powershell'. "
        "The default shell is cmd (Windows) / bash (Linux) which cannot run PowerShell syntax. "
        "The working directory is the backend project root, so files in output/ are accessible "
        "(e.g. .\\output\\scripts\\my-script.ps1)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this command needs to be run",
            },
            "shell": {
                "type": "string",
                "enum": ["default", "powershell"],
                "description": "Which shell to use. 'default' uses cmd (Windows) or bash (Linux/macOS). 'powershell' uses PowerShell.",
                "default": "default",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 120)",
                "default": 30,
            },
        },
        "required": ["command", "reason"],
    }
    requires_approval = True

    def _build_cmd(self, command: str, shell_type: str):
        """Return (cmd, shell_flag) for subprocess."""
        if shell_type == "powershell":
            # Use pwsh if available, else powershell.exe
            import shutil
            ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            return [ps, "-NoProfile", "-NonInteractive", "-Command", command], False
        return command, True

    def execute(self, args: dict, user: User) -> str:
        command = args.get("command", "")
        shell_type = args.get("shell", "default")
        timeout, err = _normalize_timeout(args.get("timeout_seconds"))
        if err:
            return err

        # B3: pre-flight check for the PowerShell "pipe az directly" anti-pattern
        if shell_type == "powershell" and _PS_AZ_PIPE_RE.search(command):
            return (
                "Error: The command pipes the 'az' executable directly into a pipeline, "
                "which fails in PowerShell with:\n"
                "  'Cannot run a document in the middle of a pipeline'\n\n"
                "Fix: capture the az output in a variable first, then pipe it:\n"
                "  # Wrong:  az graph query -q $q -o json | ConvertFrom-Json\n"
                "  # Right:  $json = az graph query -q $q -o json\n"
                "  #         $json | ConvertFrom-Json\n\n"
                "Please rewrite the command using the pattern above and retry."
            )

        work_dir = _WORK_DIR

        # Build environment
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(work_dir)),
            "TERM": "dumb",
        }
        # PowerShell needs extra env vars on Windows
        if shell_type == "powershell":
            for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
                        "SystemRoot", "ProgramFiles", "PSModulePath"):
                val = os.environ.get(key)
                if val:
                    env[key] = val

        cmd, use_shell = self._build_cmd(command, shell_type)

        try:
            result = subprocess.run(
                cmd,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(work_dir),
                env=env,
                **SUBPROCESS_FLAGS,
            )

            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"--- stdout ---\n{result.stdout}\n"
            if result.stderr:
                output += f"--- stderr ---\n{result.stderr}\n"

            # Truncate
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return output

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as e:
            logger.error("Shell execution error: %s", str(e))
            return f"Error: {str(e)}"

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        command = args.get("command", "")
        shell_type = args.get("shell", "default")
        timeout, err = _normalize_timeout(args.get("timeout_seconds"))
        if err:
            yield err
            return err

        work_dir = _WORK_DIR

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(work_dir)),
            "TERM": "dumb",
        }
        if shell_type == "powershell":
            for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
                        "SystemRoot", "ProgramFiles", "PSModulePath"):
                val = os.environ.get(key)
                if val:
                    env[key] = val

        cmd, use_shell = self._build_cmd(command, shell_type)

        try:
            proc = subprocess.Popen(
                cmd,
                shell=use_shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(work_dir),
                env=env,
                **SUBPROCESS_FLAGS,
            )

            output_lines: list[str] = []
            stderr_lines: list[str] = []

            def _read_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_lines.append(line)

            t = threading.Thread(target=_read_stderr, daemon=True)
            t.start()

            assert proc.stdout is not None
            for line in proc.stdout:
                output_lines.append(line)
                yield line

            proc.wait(timeout=timeout)
            t.join(timeout=5)

            full = f"Exit code: {proc.returncode}\n"
            if output_lines:
                full += f"--- stdout ---\n{''.join(output_lines)}\n"
            if stderr_lines:
                full += f"--- stderr ---\n{''.join(stderr_lines)}\n"
                yield f"--- stderr ---\n{''.join(stderr_lines)}"

            if len(full) > _MAX_OUTPUT_SIZE:
                full = full[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return full

        except subprocess.TimeoutExpired:
            proc.kill()
            err = f"Error: Command timed out after {timeout} seconds"
            yield err
            return err
        except Exception as e:
            logger.error("Shell execution error: %s", str(e))
            err = f"Error: {str(e)}"
            yield err
            return err
