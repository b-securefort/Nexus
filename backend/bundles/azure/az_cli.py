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

# Subcommand sequences that hand an arbitrary command string to remote compute —
# the same arbitrary-code-execution surface the 2026-05-22 run_shell retirement
# removed from the Nexus host, here reaching the user's own Azure resources.
# These are NOT blocked: the target is the user's own resource and the command
# runs as their own ARM identity, so it grants no privilege they lack. Instead
# they floor to ⛔ destructive in the risk reviewer (via `risk_floor` below) so
# the approval card forces a careful read rather than a routine ⚠ click.
# See DESIGN.md §5 2026-06-12. The list is deliberately a finite floor: the
# review LLM still escalates any remote-exec verb we did not enumerate here.
_REMOTE_EXEC_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("vm", "run-command", "invoke"),
    ("vm", "run-command", "create"),
    ("vm", "run-command", "update"),
    ("vmss", "run-command", "invoke"),
    ("vmss", "run-command", "create"),
    ("vmss", "run-command", "update"),
    ("aks", "command", "invoke"),
    ("ssh", "vm"),
    ("ssh", "arc"),
    ("container", "exec"),
    ("containerapp", "exec"),
    ("webapp", "ssh"),
    ("webapp", "create-remote-connection"),
    ("acr", "run"),
    ("acr", "build"),
    ("acr", "task", "run"),
)


def _matches_prefix_sequence(
    az_args: list[str], prefixes: tuple[tuple[str, ...], ...]
) -> tuple[str, ...] | None:
    """Return the first prefix that appears as a contiguous run of tokens
    anywhere in ``az_args``, or None.

    Scans the *entire* args list rather than only the head, so global flags
    (``--debug``, ``--verbose``, ``--only-show-errors``, ``--output json``,
    ``--subscription <id>``, etc.) cannot be used as a prefix to slip a matched
    subcommand past the scan. Matching the action verb as part of the sequence
    (e.g. ``run-command invoke``) means read forms like ``run-command list`` do
    not match.
    """
    lowered = [str(a).lower() for a in az_args]
    for prefix in prefixes:
        n = len(prefix)
        for i in range(len(lowered) - n + 1):
            if tuple(lowered[i:i + n]) == prefix:
                return prefix
    return None


def _is_blocked(az_args: list[str]) -> str | None:
    """Return an error string if the args contain a blocked subcommand sequence
    as a contiguous run of tokens, else None."""
    prefix = _matches_prefix_sequence(az_args, _BLOCKED_PREFIXES)
    if prefix is None:
        return None
    joined = " ".join(prefix)
    return (
        f"Error: 'az {joined}' is blocked for safety. "
        "These operations can wipe credentials or remove access. "
        "If this is genuinely required, the operator must run it manually."
    )


class AzCliTool(Tool):
    name = "az_cli"
    config_flag = "TOOL_AZ_CLI_ENABLED"
    retry_eligible = True       # was orchestrator _COMMAND_TOOLS
    learning_eligible = True    # was orchestrator _LEARNING_ELIGIBLE_TOOLS
    result_limit = 12_000        # was orchestrator _TOOL_RESULT_LIMITS

    def retry_docs_query(self, func_args: dict, error_text: str) -> str | None:
        args = func_args.get("args", [])
        return f"az {' '.join(args[:3])} syntax parameters" if args else None

    def retry_alt_hint(self) -> str | None:
        return (
            "For read queries, try `az_resource_graph` (KQL) — faster and needs "
            "no approval. For ARM operations not exposed by az_cli, use "
            "`az_rest_api` (with `body_file` for large payloads). Tip: "
            "`az <command> --help` shows the correct syntax."
        )

    def risk_floor(self, func_args: dict) -> str | None:
        """Tool-owned risk floor read by `risk_review.deterministic_floor`.

        Duck-typed via the registry so core needs no `bundles` import — the
        Azure bundle owns the facts about which az commands are dangerous
        (DESIGN.md §5 2026-06-12). Returns the literal tier "destructive" when
        the args invoke a remote-exec command that hands an arbitrary command
        string to compute (run-command / exec / ssh / acr run); None otherwise,
        leaving the generic token-based floor to classify. The returned string
        must stay equal to `risk_review.DESTRUCTIVE` — a test guards the drift.
        """
        az_args = func_args.get("args") or []
        if not isinstance(az_args, list):
            return None
        if _matches_prefix_sequence(az_args, _REMOTE_EXEC_PREFIXES) is not None:
            return "destructive"
        return None

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
