"""
Azure CLI tool — runs az commands with user approval.
"""

import hashlib
import logging
import re
from typing import Generator, Iterator

from app.auth.models import User
from app.tools.base import (
    Tool,
    check_shell_injection,
    consume_stream,
    stream_subprocess,
)
from bundles.azure._az_base import _az_env, _find_az, resolve_output_file
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


# Privilege-escalation grants — create/update a role assignment or definition.
# Floored ⛔ (NOT blocked): like _REMOTE_EXEC_PREFIXES this acts as the user's own
# ARM identity, and Azure RBAC already requires Microsoft.Authorization/.../write
# to make the grant, so it confers nothing the user can't already confer (§5
# 2026-06-13). The lock-out-shaped *deletes* stay in _BLOCKED_PREFIXES — different
# charter (could remove the team's access), unchanged here.
_PRIVESC_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("role", "assignment", "create"),
    ("role", "assignment", "update"),
    ("role", "definition", "create"),
    ("role", "definition", "update"),
)

# Credential-reads — the command's *output* returns live secret material. These
# use read verbs (show / list / list-keys / show-connection-string) so they would
# otherwise hit the read-verb SAFE shortcut; instead they floor ⚠ AND trigger
# whole-body output redaction before the result is persisted/replayed (§5
# 2026-06-13). Finite floor: the review LLM still escalates anything not listed.
_CREDENTIAL_READ_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("keyvault", "secret", "show"),
    ("keyvault", "secret", "list"),         # list with --query can leak values
    ("keyvault", "key", "show"),
    ("keyvault", "certificate", "show"),
    ("storage", "account", "keys", "list"),
    ("storage", "account", "show-connection-string"),
    ("cosmosdb", "keys", "list"),
    ("cosmosdb", "list-keys"),
    ("cosmosdb", "list-connection-strings"),
    ("redis", "list-keys"),
    ("servicebus", "namespace", "authorization-rule", "keys", "list"),
    ("eventhubs", "namespace", "authorization-rule", "keys", "list"),
    ("relay", "namespace", "authorization-rule", "keys", "list"),
    ("acr", "credential", "show"),
    ("appconfig", "credential", "list"),
    ("iot", "hub", "connection-string", "show"),
    ("functionapp", "keys", "list"),
    ("webapp", "deployment", "list-publishing-credentials"),
)

# az subcommand tokens that imply irreversible data/identity loss. Matched as
# standalone tokens anywhere in the args list (global flags can't shield them).
# Moved here from risk_review (core) so the bundle owns all az risk facts
# (§5 2026-06-13).
_DESTRUCTIVE_TOKENS = frozenset({
    "delete", "purge", "remove", "destroy", "revoke",
})

# az read-only leaf verbs — genuinely safe even though az_cli is always
# approval-gated. Checked AFTER the credential-read set so a read verb that
# returns a secret (`secret show`) does not slip through as SAFE.
_READ_VERBS = frozenset({
    "list", "show", "get", "check", "exists", "wait", "version", "list-keys",
})

# Flags whose following token is a secret value, masked in any rendered command
# shown to the review LLM / approval card / download / stored tool_calls (§5
# 2026-06-13). Execution is unaffected — rendering is display-only.
_SENSITIVE_FLAGS = frozenset({
    "--value", "--password", "--admin-password", "--secret", "--client-secret",
    "--account-key", "--connection-string", "--sas-token", "--certificate-password",
    "--ssh-key-value", "--secrets",
})

# Value shapes that are secrets regardless of the preceding flag.
_SECRET_VALUE_PATTERN = re.compile(
    r"(AccountKey=|SharedAccessKey=|sig=)[^;&\s\"']+",
    re.IGNORECASE,
)

_MASK = "***"

_REDACTED_OUTPUT = (
    "[redacted: this command returns credential material. The value was shown to "
    "you live but is withheld from saved history. Re-run the command to fetch it "
    "again if needed.]"
)


def _mask_args(az_args: list[str]) -> list[str]:
    """Return a copy of args with secret values masked for display.

    Masks the token following a sensitive flag (``--value SECRET`` →
    ``--value ***``), the inline form (``--value=SECRET``), and any
    connection-string / SAS shape anywhere in a token. Display-only — never used
    to build the executed command (§5 2026-06-13)."""
    out: list[str] = []
    mask_next = False
    for arg in az_args:
        s = str(arg)
        if mask_next:
            out.append(_MASK)
            mask_next = False
            continue
        low = s.lower()
        if low in _SENSITIVE_FLAGS:
            out.append(s)
            mask_next = True
            continue
        if "=" in s and low.split("=", 1)[0] in _SENSITIVE_FLAGS:
            out.append(f"{s.split('=', 1)[0]}={_MASK}")
            continue
        out.append(_SECRET_VALUE_PATTERN.sub(lambda m: m.group(1) + _MASK, s))
    return out


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


# az's `@file` convention loads an argument value from disk at execution time
# (`--scripts @output/x.ps1`). Any such path must resolve inside the output/
# sandbox — otherwise az would read an arbitrary server file (e.g. backend/.env)
# and forward it to remote compute. We resolve every @-token against the sandbox,
# hard-reject escapes, and rewrite the survivor to its absolute path so az and the
# risk reviewer read the same bytes (DESIGN.md §5 2026-06-15).
_MAX_AT_FILE_BYTES = 1_048_576

# `--query`/`-q` values are carved out: JMESPath uses a leading `@` for the
# current node (`--query "@.name"`), which is not a file reference.
_QUERY_FLAGS = frozenset({"--query", "-q"})

# Render budget that separates the judge LLM (small/finite token budget) from the
# human approval card / download (large or unbounded). The judge and persisted
# history get the @file *pointer*; only the human sees resolved @file *content*,
# so file-borne secrets reach neither the review LLM nor the DB (#16 surface
# split, §5 2026-06-15). A test guards this against the risk_review windows.
_JUDGE_RENDER_MAX = 32768


def _split_at_token(token: str) -> tuple[str, str] | None:
    """If `token` carries an `@file` reference, return ``(prefix, relpath)`` where
    `prefix` is everything up to and including the `@` — so rewriting the token to
    its resolved path is ``prefix + abspath``. Handles the standalone form
    ``@path`` (prefix ``@``) and the inline-flag form ``--flag=@path`` (prefix
    ``--flag=@``). A bare ``@`` (empty remainder) is JMESPath current-node, not a
    file, so returns None."""
    if token.startswith("@"):
        rel = token[1:]
        return ("@", rel) if rel else None
    if token.startswith("-") and "=" in token:
        flag, _, val = token.partition("=")
        if val.startswith("@"):
            rel = val[1:]
            return (f"{flag}=@", rel) if rel else None
    return None


def _iter_at_files(az_args: list[str]) -> Iterator[tuple[int, str, str]]:
    """Yield ``(index, prefix, relpath)`` for each `@file` arg, skipping
    ``--query``/``-q`` values (JMESPath owns a leading ``@``). Detection only —
    no sandbox resolution. Shared by the execute-path rewrite, the fingerprint,
    and the human-card content render so all three agree on what an @file is."""
    prev_is_query = False
    for i, token in enumerate(az_args):
        s = str(token)
        flag = s.partition("=")[0].lower()
        is_query_value = prev_is_query or (flag in _QUERY_FLAGS and "=" in s)
        prev_is_query = s.lower() in _QUERY_FLAGS
        if is_query_value:
            continue
        split = _split_at_token(s)
        if split is None:
            continue
        prefix, rel = split
        # Accept both the natural az cwd-relative form (`@output/x.ps1`, since the
        # backend process cwd is backend/) and the output-relative form
        # (`@x.ps1`, matching body_file): strip a single leading `output/` so both
        # resolve to the same sandbox file instead of `output/output/x.ps1`.
        if rel.replace("\\", "/").startswith("output/"):
            rel = rel[len("output/"):]
        yield i, prefix, rel


def _resolve_and_rewrite_at_files(
    az_args: list[str],
) -> tuple[list[str] | None, list, str | None]:
    """Resolve every `@file` against the output/ sandbox and rewrite it to an
    absolute path. Returns ``(rewritten_args, resolved_paths, None)`` on success
    or ``(None, [], error)`` if any `@file` escapes the sandbox or is unreadable."""
    out = [str(a) for a in az_args]
    resolved: list = []
    for i, prefix, rel in _iter_at_files(az_args):
        target, err = resolve_output_file(
            rel, max_bytes=_MAX_AT_FILE_BYTES, label="@file argument",
        )
        if err or target is None:
            return None, [], err
        out[i] = f"{prefix}{target}"
        resolved.append(target)
    return out, resolved, None


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
        Azure bundle owns the full az risk classification (DESIGN.md §5
        2026-06-13, finishing the decoupling §5 2026-06-12 began). Returns any
        tier or None; core defers entirely (`_tool_risk_floor(...) or CAUTION`).
        The returned strings must stay equal to `risk_review.{SAFE,CAUTION,
        DESTRUCTIVE}` — a test guards the drift.

        Order is load-bearing: remote-exec / privilege-escalation ⛔ win over
        everything; credential-reads ⚠ are checked BEFORE read verbs (a read
        verb like `secret show` returns a secret, so it must not slip to SAFE);
        destructive tokens ⛔; read verbs ✓; anything else ⚠ (default for an
        approval-gated mutation, e.g. create/update/stop/deallocate — the review
        LLM escalates the dangerous ones the floor can't see in flag values).
        """
        az_args = func_args.get("args") or []
        if not isinstance(az_args, list):
            return None

        if _matches_prefix_sequence(az_args, _REMOTE_EXEC_PREFIXES) is not None:
            return "destructive"
        if _matches_prefix_sequence(az_args, _PRIVESC_PREFIXES) is not None:
            return "destructive"
        if _matches_prefix_sequence(az_args, _CREDENTIAL_READ_PREFIXES) is not None:
            return "caution"

        tokens = [str(t).lower() for t in az_args]
        if any(t in _DESTRUCTIVE_TOKENS for t in tokens):
            return "destructive"
        non_flag = [t for t in tokens if not t.startswith("-")]
        if non_flag and any(v in _READ_VERBS for v in non_flag):
            return "safe"
        return "caution"

    def review_fingerprint(self, func_args: dict) -> str | None:
        """sha256 over the resolved bytes of every `@file` arg — closes the
        approve→execute TOCTOU for az_cli (hardening #20, §5 2026-06-15). The
        orchestrator captures this at approval time and re-checks immediately
        before execution, aborting if a referenced file changed in between.

        Returns None when the command has no `@file` (plain az_cli needs no
        re-check) or when a referenced file can't be resolved (the execute-path
        hard-reject is the guard there). Order-independent (paths sorted) so a
        multi-`@file` command fingerprints stably; bytes are length-delimited so
        concatenation is unambiguous. Never raises."""
        az_args = func_args.get("args")
        if not isinstance(az_args, list):
            return None
        paths: list = []
        for _, _, rel in _iter_at_files(az_args):
            target, err = resolve_output_file(
                rel, max_bytes=_MAX_AT_FILE_BYTES, label="@file argument",
            )
            if err or target is None:
                return None
            paths.append(target)
        if not paths:
            return None
        h = hashlib.sha256()
        for target in sorted(paths, key=str):
            try:
                h.update(target.read_bytes())
                h.update(b"\x00")
            except OSError:
                return None
        return h.hexdigest()

    def _render_at_file_content(
        self, az_args: list[str], max_bytes: int | None
    ) -> tuple[list[str], bool]:
        """Return ``(sections, truncated)`` inlining each `@file`'s resolved
        content for the human approval card. Unresolvable files render as a
        marker (the execute path is the real guard, so this never errors out)."""
        sections: list[str] = []
        truncated = False
        remaining = max_bytes
        for _, _, rel in _iter_at_files(az_args):
            target, err = resolve_output_file(
                rel, max_bytes=_MAX_AT_FILE_BYTES, label="@file argument",
            )
            if err or target is None:
                sections.append(f"--- @{rel} (unreadable) ---")
                continue
            try:
                data = target.read_bytes()
            except OSError as e:
                sections.append(f"--- @{rel} (read error: {e}) ---")
                continue
            body = data.decode("utf-8", errors="replace")
            if remaining is not None and len(body) > remaining:
                body = body[:remaining] + f"\n[truncated: {len(data)} bytes total]"
                truncated = True
                remaining = 0
            elif remaining is not None:
                remaining -= len(body)
            sections.append(f"--- @{rel} ({len(data)} bytes) ---\n{body}")
        return sections, truncated

    def render_for_review(
        self, func_args: dict, max_bytes: int | None = None
    ) -> tuple[str, bool]:
        """Render the command for the reviewer / card / download with secret
        args masked (§5 2026-06-13). Display-only — execution reads `func_args`.

        Surface split for `@file` args (§5 2026-06-15): the judge LLM and
        persisted history (small/finite `max_bytes`) get the `@path` *pointer*
        so file-borne secrets never reach the review LLM or the DB; the human
        approval card and download (large/unbounded `max_bytes`) get the resolved
        `@file` *content* inlined, because the human is the gate and must see
        what runs."""
        az_args = func_args.get("args")
        if not isinstance(az_args, list):
            return "az (no args)", False
        base = "az " + " ".join(_mask_args(az_args))
        show_content = max_bytes is None or max_bytes > _JUDGE_RENDER_MAX
        if not show_content:
            return base, False
        sections, truncated = self._render_at_file_content(az_args, max_bytes)
        if not sections:
            return base, False
        return base + "\n\n" + "\n\n".join(sections), truncated

    def mask_args(self, func_args: dict) -> dict:
        """Return func_args with secret arg values masked, for persistence of
        the assistant's stored tool_calls (§5 2026-06-13). Core calls this via a
        duck-typed hook before writing tool_calls_json and on history rebuild."""
        az_args = func_args.get("args")
        if not isinstance(az_args, list):
            return func_args
        return {**func_args, "args": _mask_args(az_args)}

    def redact_output(self, func_args: dict, output: str) -> str:
        """Replace a credential-read's output with a marker before it is saved
        or replayed (§5 2026-06-13). The live SSE stream and the current turn's
        in-memory history keep the real value; only the persisted copy is
        redacted. Non-credential commands pass through unchanged."""
        az_args = func_args.get("args") or []
        if not isinstance(az_args, list):
            return output
        if _matches_prefix_sequence(az_args, _CREDENTIAL_READ_PREFIXES) is not None:
            return _REDACTED_OUTPUT
        return output

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

        # Resolve any `@file` args against the output/ sandbox and rewrite them to
        # absolute paths so az and the risk reviewer read the same bytes; an @file
        # that escapes the sandbox or is unreadable is a hard reject (§5
        # 2026-06-15). Runs after check_shell_injection (which validated the raw,
        # model-supplied tokens) — the rewritten absolute paths are system-built.
        rewritten, _resolved, at_err = _resolve_and_rewrite_at_files(az_args)
        if at_err:
            yield at_err
            return at_err
        az_args = rewritten

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
