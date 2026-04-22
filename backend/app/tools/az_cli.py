"""
Azure CLI tool — runs az commands with user approval.
"""

import logging
import shutil
import subprocess
import sys
import threading
from typing import Generator

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login, clear_login_cache

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 8192


def _find_az() -> str:
    """Resolve the full path to az CLI. On Windows it's az.cmd."""
    path = shutil.which("az")
    if path:
        return path
    # Fallback for Windows
    if sys.platform == "win32":
        path = shutil.which("az.cmd")
        if path:
            return path
    return "az"


class AzCliTool(Tool):
    name = "az_cli"
    description = "Execute an Azure CLI command. Requires explicit user approval. The container's managed identity or pre-configured service principal is used for authentication."
    parameters_schema = {
        "type": "object",
        "properties": {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments to pass to the az CLI, e.g. ['group', 'list', '--output', 'table']",
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this command needs to be run",
            },
        },
        "required": ["args", "reason"],
    }
    requires_approval = True

    def execute(self, args: dict, user: User) -> str:
        # Pre-check Azure login state
        login_err = require_az_login()
        if login_err:
            return login_err

        az_args = args.get("args", [])
        if not isinstance(az_args, list):
            return "Error: args must be a list of strings"

        cmd = [_find_az()] + [str(a) for a in az_args]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"--- stdout ---\n{result.stdout}\n"
            if result.stderr:
                output += f"--- stderr ---\n{result.stderr}\n"

            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return output

        except subprocess.TimeoutExpired:
            return "Error: az CLI command timed out after 60 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found. Is it installed?"
        except Exception as e:
            logger.error("az CLI error: %s", str(e))
            return f"Error: {str(e)}"

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        # Pre-check Azure login state
        login_err = require_az_login()
        if login_err:
            yield login_err
            return login_err

        az_args = args.get("args", [])
        if not isinstance(az_args, list):
            yield "Error: args must be a list of strings"
            return "Error: args must be a list of strings"

        cmd = [_find_az()] + [str(a) for a in az_args]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            output_lines: list[str] = []
            stderr_lines: list[str] = []

            # Read stderr in background thread
            def _read_stderr():
                assert proc.stderr is not None
                for line in proc.stderr:
                    stderr_lines.append(line)

            t = threading.Thread(target=_read_stderr, daemon=True)
            t.start()

            # Stream stdout line by line
            assert proc.stdout is not None
            for line in proc.stdout:
                output_lines.append(line)
                yield line

            proc.wait(timeout=60)
            t.join(timeout=5)

            # Build full result
            full = f"Exit code: {proc.returncode}\n"
            if output_lines:
                full += f"--- stdout ---\n{''.join(output_lines)}\n"
            if stderr_lines:
                full += f"--- stderr ---\n{''.join(stderr_lines)}\n"
                # Yield stderr at the end
                yield f"--- stderr ---\n{''.join(stderr_lines)}"

            if len(full) > _MAX_OUTPUT_SIZE:
                full = full[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return full

        except subprocess.TimeoutExpired:
            proc.kill()
            err = "Error: az CLI command timed out after 60 seconds"
            yield err
            return err
        except FileNotFoundError:
            err = "Error: Azure CLI (az) not found. Is it installed?"
            yield err
            return err
        except Exception as e:
            logger.error("az CLI error: %s", str(e))
            err = f"Error: {str(e)}"
            yield err
            return err
