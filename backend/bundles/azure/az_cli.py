"""
Azure CLI tool — runs az commands with user approval.
"""

import logging
import subprocess
import sys
import threading
from typing import Generator

import os

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool, check_shell_injection, get_arm_token
from bundles.azure._az_base import _find_az
from bundles.azure.az_login_check import require_az_login, clear_login_cache

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 8192

# Subcommand prefixes that are blocked even with approval. These are operations
# that wipe credentials, create identities, or remove access — any of which
# could lock the team out or be abused if approval UX is bypassed.
_BLOCKED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("account", "clear"),
    ("ad", "app", "create"),
    ("ad", "app", "delete"),
    ("ad", "sp", "create"),
    ("ad", "sp", "delete"),
    ("role", "assignment", "delete"),
    ("role", "definition", "delete"),
)


def _is_blocked(az_args: list[str]) -> str | None:
    """Return an error string if the args contain a blocked subcommand sequence
    as a contiguous run of tokens.

    Scans the *entire* args list rather than only the head, so global flags
    (``--debug``, ``--verbose``, ``--only-show-errors``, ``--output json``,
    ``--subscription <id>``, etc.) cannot be used as a prefix to slip a
    destructive subcommand past the blocklist.
    """
    lowered = [str(a).lower() for a in az_args]
    for prefix in _BLOCKED_PREFIXES:
        n = len(prefix)
        for i in range(len(lowered) - n + 1):
            if tuple(lowered[i:i + n]) == prefix:
                joined = " ".join(prefix)
                return (
                    f"Error: 'az {joined}' is blocked for safety. "
                    "These operations can wipe credentials or remove access. "
                    "If this is genuinely required, the operator must run it manually."
                )
    return None


class AzCliTool(Tool):
    name = "az_cli"
    config_flag = "TOOL_AZ_CLI_ENABLED"
    retry_eligible = True       # was orchestrator _COMMAND_TOOLS
    learning_eligible = True    # was orchestrator _LEARNING_ELIGIBLE_TOOLS
    result_limit = 4_000        # was orchestrator _TOOL_RESULT_LIMITS
    description = (
        "Execute an Azure CLI command. Requires explicit user approval. "
        "Commands run as the authenticated user's own Azure identity — the same permissions "
        "they have in the Azure portal. If subscription context is needed, use "
        "['account', 'list'] first to discover available subscriptions, then pass "
        "'--subscription <id>' in subsequent commands."
    )
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

        # Block destructive operations even with approval
        blocked = _is_blocked(az_args)
        if blocked:
            return blocked

        # Defence-in-depth: block shell metacharacters in individual args
        for i, arg in enumerate(az_args):
            injection_err = check_shell_injection(str(arg), f"args[{i}]")
            if injection_err:
                return injection_err

        az_path = _find_az()
        if not az_path:
            return "Error: Azure CLI is not installed on this server. Circuit breaker is open. Tool disabled."

        cmd = [az_path] + [str(a) for a in az_args]

        env = os.environ.copy()
        arm_token = get_arm_token()
        if arm_token:
            env["AZURE_ACCESS_TOKEN"] = arm_token

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                shell=(sys.platform == "win32"),
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

            # A non-zero exit is a real failure. Prefix "Error:" so the
            # orchestrator's failure detection (is_error) engages — otherwise a
            # failed command (bad syntax, exit 2, auth error) reads as success,
            # so multi-strategy retry never fires and the success-after-failure
            # learning path never captures the fix. The full exit/stderr detail
            # is preserved after the prefix for the model to read.
            if result.returncode != 0:
                return f"Error: az CLI exited with code {result.returncode}.\n{output}"
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

        # Block destructive operations even with approval
        blocked = _is_blocked(az_args)
        if blocked:
            yield blocked
            return blocked

        # Defence-in-depth: block shell metacharacters in individual args
        for i, arg in enumerate(az_args):
            injection_err = check_shell_injection(str(arg), f"args[{i}]")
            if injection_err:
                yield injection_err
                return injection_err

        az_path = _find_az()
        if not az_path:
            err = "Error: Azure CLI is not installed on this server. Circuit breaker is open. Tool disabled."
            yield err
            return err

        cmd = [az_path] + [str(a) for a in az_args]

        env = os.environ.copy()
        arm_token = get_arm_token()
        if arm_token:
            env["AZURE_ACCESS_TOKEN"] = arm_token

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=(sys.platform == "win32"),
                env=env,
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

            # See execute(): non-zero exit must surface as an error so retry +
            # learning capture engage. Streamed chunks already reached the UI;
            # only the returned value (used for is_error) gets the prefix.
            if proc.returncode != 0:
                return f"Error: az CLI exited with code {proc.returncode}.\n{full}"
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
