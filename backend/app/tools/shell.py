"""
Shell tool — runs commands with user approval.
"""

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Generator

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Max output size in bytes
_MAX_OUTPUT_SIZE = 8192


class RunShellTool(Tool):
    name = "run_shell"
    description = "Execute a shell command. Requires explicit user approval before execution. Returns stdout, stderr, and exit code."
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
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 120)",
                "default": 30,
            },
        },
        "required": ["command", "reason"],
    }
    requires_approval = True

    def execute(self, args: dict, user: User) -> str:
        command = args.get("command", "")
        timeout = min(args.get("timeout_seconds", 30), 120)

        # Create per-conversation working directory
        work_dir = Path(tempfile.gettempdir()) / "team-architect" / "shell"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Minimal safe environment
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(work_dir),
            "TERM": "dumb",
        }

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(work_dir),
                env=env,
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
        timeout = min(args.get("timeout_seconds", 30), 120)

        work_dir = Path(tempfile.gettempdir()) / "team-architect" / "shell"
        work_dir.mkdir(parents=True, exist_ok=True)

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(work_dir),
            "TERM": "dumb",
        }

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(work_dir),
                env=env,
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
