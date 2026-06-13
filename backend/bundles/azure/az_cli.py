"""
Azure CLI tool — runs az commands with user approval.
"""

import logging
from typing import Generator

from app.auth.models import User
from app.tools.base import (
    Tool,
    check_shell_injection,
    consume_stream,
    stream_subprocess,
)
from bundles.azure._az_base import _az_env, _find_az
from bundles.azure.az_login_check import require_az_login, clear_login_cache

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 8192

# Wall-clock deadline for one az invocation, enforced by the stream_subprocess
# watchdog (§5 2026-06-13). Fixed for now — configurability is backlog #11.
_TIMEOUT_SECONDS = 60

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
        # Single implementation: drain the streaming path (§5 2026-06-13).
        # A separate subprocess.run copy here is where a dead 60s timeout hid
        # while the orchestrator only ever dispatched execute_streaming.
        return consume_stream(self.execute_streaming(args, user))

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
        # (primary defence is shell=False inside stream_subprocess)
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

        try:
            # shell=False + env allowlist + kill-switch registration + watchdog
            # deadline, all owned by the shared runner (§5 2026-06-13).
            res = yield from stream_subprocess(
                cmd, env=_az_env(), timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            err = "Error: Azure CLI (az) not found. Is it installed?"
            yield err
            return err
        except Exception as e:
            logger.error("az CLI error: %s", str(e))
            err = f"Error: {str(e)}"
            yield err
            return err

        # returncode!=0 guard: if the process finished cleanly in the same
        # instant the watchdog fired, believe the clean exit over the timer.
        if res.timed_out and res.returncode != 0:
            err = f"Error: az CLI command timed out after {_TIMEOUT_SECONDS} seconds"
            yield err
            return err

        # Build full result
        full = f"Exit code: {res.returncode}\n"
        if res.stdout:
            full += f"--- stdout ---\n{res.stdout}\n"
        if res.stderr:
            full += f"--- stderr ---\n{res.stderr}\n"
            # Yield stderr at the end
            yield f"--- stderr ---\n{res.stderr}"

        if len(full) > _MAX_OUTPUT_SIZE:
            full = full[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

        # A non-zero exit is a real failure. Prefix "Error:" so the
        # orchestrator's failure detection (is_error) engages — otherwise a
        # failed command (bad syntax, exit 2, auth error) reads as success,
        # so multi-strategy retry never fires and the success-after-failure
        # learning path never captures the fix. Streamed chunks already reached
        # the UI; only the returned value (used for is_error) gets the prefix.
        if res.returncode != 0:
            return f"Error: az CLI exited with code {res.returncode}.\n{full}"
        return full
