"""
Tool base class and registry.
"""

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Generator

from prometheus_client import Counter

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)

# Per-request ARM token for user-identity Azure calls.
# Set by the orchestrator at the start of each chat turn; read by AzureToolBase._run_az()
# so every az subprocess authenticates as the current user rather than the server identity.
_current_arm_token: ContextVar[str | None] = ContextVar("arm_token", default=None)


def set_arm_token(token: str | None) -> None:
    """Store the user's ARM token for the current request context. Called by the orchestrator."""
    _current_arm_token.set(token)


def get_arm_token() -> str | None:
    return _current_arm_token.get()


# Per-request active skill slug. Set by the orchestrator at the start of each
# chat turn; read by tools that need to enforce skill-scoped behaviour the LLM
# keeps ignoring in the system prompt. Today the only use is generate_file
# refusing .drawio writes when the conversation's skill is the Engineer
# (`chat-with-kb`) — see §5 2026-05-19: Engineer hands diagrams off to Architect.
_current_skill_name: ContextVar[str | None] = ContextVar("skill_name", default=None)


def set_skill_name(name: str | None) -> None:
    """Store the active skill slug for the current request context."""
    _current_skill_name.set(name)


def get_skill_name() -> str | None:
    return _current_skill_name.get()


# ── Killable subprocess registry (DESIGN.md §5 2026-06-04) ────────────────────
#
# `execute_script` can run a long, multi-step script (e.g. a loop deleting
# resources). When the user hits Stop / disconnects, the orchestrator's
# interrupt cleanup kills the script's whole process tree so the remaining
# iterations never run. The tool runs in an executor thread; the kill is
# triggered from the async path — so we keep a thread-safe per-conversation
# registry of live Popen handles, keyed by a ContextVar that propagates into
# the worker thread (same mechanism as the ARM token / skill slug).
#
# NB: this only stops *future* work. Whatever the current iteration already
# dispatched to Azure completes server-side — a local kill can't recall it.
_current_conversation_id: ContextVar[int | None] = ContextVar("conversation_id", default=None)


def set_conversation_id(conversation_id: int | None) -> None:
    """Store the active conversation id for the current request context so a
    tool running in the executor thread can register its subprocess for kill."""
    _current_conversation_id.set(conversation_id)


def get_conversation_id() -> int | None:
    return _current_conversation_id.get()


# Per-request user identity, set alongside the conversation id at the top of a
# chat turn. Lets a completions call deep in the stack (aux summaries, judge,
# rerank) attribute its token usage to the right user for the spend ledger
# (DESIGN.md §5 2026-06-14) without threading user_oid through every signature.
_current_user_oid: ContextVar[str | None] = ContextVar("user_oid", default=None)


def set_user_oid(user_oid: str | None) -> None:
    """Store the active user's oid for the current request context so a
    completions call anywhere in the turn can attribute its usage to them."""
    _current_user_oid.set(user_oid)


def get_user_oid() -> str | None:
    return _current_user_oid.get()


_process_registry_lock = threading.Lock()
_process_registry: dict[int, set[subprocess.Popen]] = {}


def register_process(conversation_id: int | None, proc: subprocess.Popen) -> None:
    """Track a running subprocess so Stop / disconnect can kill it."""
    if conversation_id is None:
        return
    with _process_registry_lock:
        _process_registry.setdefault(conversation_id, set()).add(proc)


def unregister_process(conversation_id: int | None, proc: subprocess.Popen) -> None:
    """Drop a subprocess from the registry once it has finished."""
    if conversation_id is None:
        return
    with _process_registry_lock:
        procs = _process_registry.get(conversation_id)
        if procs:
            procs.discard(proc)
            if not procs:
                _process_registry.pop(conversation_id, None)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate a process AND its children, cross-platform.

    Windows: ``taskkill /T`` walks the child tree by PID parentage. POSIX: the
    process was launched with ``start_new_session=True`` so the children share a
    process group we can signal with ``killpg`` (SIGTERM, then SIGKILL).
    """
    if proc.poll() is not None:
        return  # already exited
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                **SUBPROCESS_FLAGS,
            )
        else:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                return
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except Exception:
        logger.exception("Failed to kill process tree pid=%s", getattr(proc, "pid", "?"))


def kill_conversation_processes(conversation_id: int | None) -> int:
    """Kill every tracked subprocess for a conversation; return the count.

    Called from the orchestrator's interrupt cleanup (Stop / client disconnect).
    Idempotent — a no-op when nothing is registered.
    """
    if conversation_id is None:
        return 0
    with _process_registry_lock:
        procs = list(_process_registry.get(conversation_id, ()))
    for proc in procs:
        _kill_process_tree(proc)
        unregister_process(conversation_id, proc)
    if procs:
        logger.info(
            "Killed %d tracked process(es) for conv=%s", len(procs), conversation_id
        )
    return len(procs)


# Suppress the black console window that subprocess spawns on Windows.
# Spread this into every subprocess.run() / subprocess.Popen() call:
#   subprocess.run([...], **SUBPROCESS_FLAGS)
SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


# ── Streaming subprocess runner with wall-clock watchdog (§5 2026-06-13) ──────
#
# `for line in proc.stdout` blocks in a C-level pipe read that cannot be
# interrupted portably (no select() on Windows pipes), so a `proc.wait(timeout)`
# placed *after* the read loop never runs against a command that hangs without
# printing. The deadline therefore has to come from outside the read loop: a
# timer kills the process tree, the blocked read hits EOF, and the generator
# unwinds — the same unwind the Stop / disconnect kill switch already relies on.
# Two triggers, one kill path.


class ProcessWatchdog:
    """Wall-clock deadline for a streaming subprocess.

    Arms a daemon Timer that kills the process tree when the deadline passes.
    `expired` tells the caller the kill was the deadline's doing — a retryable
    timeout — as opposed to the user's Stop, which must stay terminal
    (§5 2026-06-04: a user denial/interrupt is never a retryable error).
    """

    def __init__(self, proc: subprocess.Popen, timeout_seconds: float) -> None:
        self._proc = proc
        self.expired = False
        self._timer = threading.Timer(timeout_seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        if self._proc.poll() is not None:
            return  # finished under the deadline — not a timeout
        self.expired = True
        _kill_process_tree(self._proc)

    def cancel(self) -> None:
        self._timer.cancel()


@dataclass
class StreamedRun:
    """Outcome of `stream_subprocess` — returned via StopIteration.value."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def stream_subprocess(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float = 60,
) -> Generator[str, None, StreamedRun]:
    """Run `cmd` shell=False, yielding stdout lines as they arrive.

    The single subprocess invocation shared by the streaming command tools
    (az_cli via `_az_base`, execute_script). It owns the whole lifecycle:
    spawn in a killable group, register with the per-conversation kill
    registry, arm a `ProcessWatchdog` for the wall-clock deadline, drain
    stderr on a side thread, and unregister on the way out. Callers keep
    their own output formatting and error vocabulary.
    """
    popen_kwargs = dict(SUBPROCESS_FLAGS)
    if sys.platform != "win32":
        # Killable process group for os.killpg; on Windows taskkill /T walks
        # the PID tree instead (see _kill_process_tree).
        popen_kwargs["start_new_session"] = True

    conv_id = get_conversation_id()
    proc = subprocess.Popen(
        cmd,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=cwd,
        **popen_kwargs,
    )
    register_process(conv_id, proc)
    watchdog = ProcessWatchdog(proc, timeout)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        def _read_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=_read_stderr, daemon=True)
        t.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            yield line

        # stdout is EOF. If the process closed stdout but lingers, this wait is
        # still bounded: the watchdog kills the tree at the deadline.
        proc.wait()
        t.join(timeout=5)
    finally:
        watchdog.cancel()
        unregister_process(conv_id, proc)

    return StreamedRun(
        returncode=proc.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        timed_out=watchdog.expired,
    )


def consume_stream(gen: Generator[str, None, str]) -> str:
    """Drain a tool's `execute_streaming` generator and return its value.

    Lets `execute()` be a thin wrapper over the streaming implementation so a
    subprocess tool has exactly one invocation path to harden (§5 2026-06-13).
    """
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value if stop.value is not None else ""

# Characters that must always be blocked in az CLI argument values.
#
# shell=False is enforced for every _run_az() call (CR#1), so Python's
# subprocess passes each list element directly to CreateProcess / execv
# without involving a shell.  That neutralises most metacharacters.
# We additionally screen for:
#   NUL (\x00)   – truncates C strings in the kernel
#   backtick (`) – PowerShell execution operator
#   % (percent)  – cmd.exe expands %VAR% inside quoted tokens on Windows,
#                  e.g. %AZURE_OPENAI_API_KEY% → live value.  Defence-in-
#                  depth on top of the env allowlist (B1).  (B2)
#   & (ampersand)– cmd.exe command chaining.  No legitimate az argument
#                  value should contain a bare &.  (CR#1)
#
# NOT blocked: pipe `|` — KQL queries passed as a single "-q" arg value
# legitimately contain pipes (e.g. "Resources | count"), and with
# shell=False the pipe is never interpreted by a shell.
_SHELL_METACHAR_PATTERN = r'[`\x00%&]'

import re as _re


def check_shell_injection(value: str, field_name: str = "argument") -> str | None:
    """Return an error string if *value* contains characters that could enable
    command injection when passed as an az CLI argument.

    Blocked characters (see _SHELL_METACHAR_PATTERN for rationale):
      - NUL (\\x00): truncates the command string in cmd.exe / libc
      - backtick (`): PowerShell execution operator
      - % (percent): cmd.exe env-variable expansion, e.g. %AZURE_OPENAI_API_KEY%
      - & (ampersand): cmd.exe command chaining

    Not blocked: pipe `|` — legitimate in KQL query values and safe with
    shell=False since no shell is involved.

    Returns None if safe, or an error message string if blocked.
    """
    if _re.search(_SHELL_METACHAR_PATTERN, value):
        logger.warning(
            "Shell injection blocked in %s: %s", field_name, value[:120],
        )
        bad = ', '.join(repr(c) for c in '`\x00%&' if c in value)
        return (
            f"Error: {field_name} contains characters that are not allowed "
            f"for security reasons ({bad}). Remove and retry."
        )
    return None


class Tool(ABC):
    """Base class for all tools the LLM can call."""

    name: str
    description: str
    parameters_schema: dict
    requires_approval: bool = False
    enabled_by_config: bool = True
    rate_limit_calls: int | None = None
    rate_limit_window: int = 60  # seconds

    # ── Capability attributes (bundle-decoupling, DESIGN.md §5 2026-06-05) ─────
    # These let the orchestrator/loader treat every tool by declared capability
    # instead of hardcoded name-sets, so a bundle owns the facts about its own
    # tools. Defaults reproduce today's generic (non-Azure) behaviour; a tool
    # opts in by overriding the relevant attribute on its subclass.
    #
    #   retry_eligible      — failures drive multi-strategy retry escalation
    #                         (was orchestrator `_COMMAND_TOOLS`)
    #   learning_eligible   — failure→success transitions yield a learning
    #                         (was orchestrator `_LEARNING_ELIGIBLE_TOOLS`)
    #   result_limit        — in-prompt size cap for this tool's result, or None
    #                         (was orchestrator `_TOOL_RESULT_LIMITS`)
    #   is_diagram_tool     — drawio-family tool: strip echoed XML on truncation
    #                         (was orchestrator `_DRAWIO_TOOLS`)
    #   attaches_render     — a successful call leaves a fresh PNG render on
    #                         disk: the orchestrator inlines it for the model's
    #                         vision review and attaches it to the final
    #                         assistant message (was a hardcoded name tuple,
    #                         which silently missed newly added diagram tools)
    #   requires_credentials— needs its bundle's per-request credential set up
    #                         (was orchestrator `isinstance(tool, AzureToolBase)`)
    #   config_flag         — Settings attribute name that enables/disables this
    #                         tool, or None when always enabled (was the
    #                         `config_mapping` table in init_tools)
    retry_eligible: bool = False
    learning_eligible: bool = False
    result_limit: int | None = None
    is_diagram_tool: bool = False
    attaches_render: bool = False
    requires_credentials: bool = False
    config_flag: str | None = None

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    @abstractmethod
    def execute(self, args: dict, user: User) -> str:
        """Execute the tool and return a string result."""
        ...

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Auto-register concrete tools that define a name
        if hasattr(cls, "name") and not getattr(cls, "__abstractmethods__", None):
            TOOL_REGISTRY[cls.name] = cls()

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        """Execute the tool, yielding output chunks as they arrive.

        Returns the full combined output. Subclasses that run subprocesses
        should override this. Default implementation falls back to execute().
        """
        result = self.execute(args, user)
        yield result
        return result

    def retry_docs_query(self, func_args: dict, error_text: str) -> str | None:
        """Search query for `fetch_ms_docs` when this tool fails (retry Strategy
        1). Return None to use the orchestrator's generic query. Override in
        tools whose docs lookup benefits from tool-specific phrasing
        (DESIGN.md §5 2026-06-05 — keeps retry advice with the tool)."""
        return None

    def retry_alt_hint(self) -> str | None:
        """Strategy-2 'try a different approach' guidance specific to this tool
        (e.g. which alternative tool to reach for). Return None to use the
        generic 'try a different tool' message."""
        return None

    def redact_output(self, func_args: dict, output: str) -> str:
        """Return the tool output with secret material redacted before it is
        persisted to the messages table or replayed to the LLM (§5 2026-06-13).
        Default: pass through unchanged. Tools whose output can contain
        credentials (az_cli credential-reads) override this. The live SSE stream
        and the current turn's in-memory history keep the real value."""
        return output

    def mask_args(self, func_args: dict) -> dict:
        """Return func_args with secret argument values masked before the
        assistant's tool_calls are persisted / replayed (§5 2026-06-13).
        Default: unchanged. Override in tools that take secrets as arguments."""
        return func_args


def redact_tool_output(tool_name: str, func_args: dict, output: str) -> str:
    """Resolve a tool's `redact_output` hook via the registry and apply it.

    Core calls this before saving a tool result so the bundle owns the facts
    about which of its outputs are secret — no core→bundle import (mirrors
    risk_review's hook resolution). Never raises; on any failure the output is
    returned unchanged EXCEPT that an unresolved tool is left as-is (a missing
    tool means a disabled bundle, not a secret to hide)."""
    try:
        tool = get_tool(tool_name)
        hook = getattr(tool, "redact_output", None)
        if hook is None:
            return output
        result = hook(func_args, output)
        return result if isinstance(result, str) else output
    except Exception as e:  # noqa: BLE001 — redaction must never break the turn
        logger.warning("redact_output hook failed for %s: %s", tool_name, str(e)[:120])
        return output


def mask_tool_call_args(tool_name: str, func_args: dict) -> dict:
    """Resolve a tool's `mask_args` hook via the registry and apply it before
    the assistant's tool_calls are persisted / replayed. Never raises."""
    try:
        tool = get_tool(tool_name)
        hook = getattr(tool, "mask_args", None)
        if hook is None:
            return func_args
        result = hook(func_args)
        return result if isinstance(result, dict) else func_args
    except Exception as e:  # noqa: BLE001 — masking must never break the turn
        logger.warning("mask_args hook failed for %s: %s", tool_name, str(e)[:120])
        return func_args


def retry_with_backoff(
    func, max_retries: int = 3, base_delay: float = 2.0, retryable_errors: tuple = ("429", "500", "502", "503", "504", "Too Many Requests")
) -> subprocess.CompletedProcess:
    """Execute a subprocess call with exponential backoff for retryable errors."""
    import time
    import random
    
    for attempt in range(max_retries + 1):
        result = func()
        if result.returncode == 0 or attempt == max_retries:
            return result
        
        error_output = (result.stderr or "") + (result.stdout or "")
        if not any(err in error_output for err in retryable_errors):
            return result
            
        logger.warning(
            "Retryable error encountered (attempt %d/%d). Retrying in backoff...",
            attempt + 1,
            max_retries,
        )
        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
        time.sleep(delay)
    return result


# Tool-outcome telemetry. Lets us spot tool-quality regressions without
# scraping the messages table by hand (`success/empty/error` ratios per tool).
# The orchestrator increments this once per tool invocation; tool implementations
# do not call it directly so the outcome classification stays in one place.
TOOL_CALLS = Counter(
    "nexus_tool_calls_total",
    "Tool invocations grouped by outcome",
    ["tool", "outcome"],
)

# Result-string sniff threshold for "empty" classification. Tools that return a
# JSON envelope with an empty `data`/`results`/`items`/`value` array OR a plain
# response shorter than this (after error-prefix strip) count as empty.
_EMPTY_RESULT_MAX_LEN = 30
_EMPTY_JSON_RE = re.compile(
    r'"(?:data|results|items|value)"\s*:\s*\[\s*\]'
)


def classify_tool_outcome(result: str) -> str:
    """Classify a tool result string as 'success', 'empty', or 'error'.

    Used for the `nexus_tool_calls_total` metric. The taxonomy is deliberately
    coarse — finer breakdowns belong in the logger telemetry block.
    """
    if not isinstance(result, str):
        return "success"
    s = result.strip()
    if not s:
        return "empty"
    # Match plain "Error: ..." prefix and JSON envelope { "status": "error", ... }
    if s.lower().startswith("error"):
        return "error"
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        status = parsed.get("status")
        if isinstance(status, str) and status.lower() == "error":
            return "error"
    # Empty-array envelopes (search tools returning no hits)
    if _EMPTY_JSON_RE.search(s):
        return "empty"
    if isinstance(parsed, list) and len(parsed) == 0:
        return "empty"
    if len(s) <= _EMPTY_RESULT_MAX_LEN:
        return "empty"
    return "success"


# Global tool registry
TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    """Register a tool in the global registry."""
    TOOL_REGISTRY[tool.name] = tool
    logger.debug("Registered tool: %s", tool.name)


def get_tool(name: str) -> Tool | None:
    """Get a tool by name."""
    return TOOL_REGISTRY.get(name)


def list_tools() -> list[Tool]:
    """List all enabled tools."""
    return [t for t in TOOL_REGISTRY.values() if t.enabled_by_config]


def resolve_tools(tool_names: list[str]) -> list[Tool]:
    """Resolve a list of tool names to Tool instances, filtering disabled ones."""
    from app.phases import is_tool_enabled

    settings = get_settings()
    tools = []
    for name in tool_names:
        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            logger.warning("Tool not found in registry: %s", name)
            continue
        if not tool.enabled_by_config:
            logger.warning("Tool %s is disabled by config, skipping", name)
            continue
        if not is_tool_enabled(name):
            # Phase-gated off at the current NEXUS_PHASE — see app/phases.py.
            logger.debug(
                "Tool %s gated off at current NEXUS_PHASE, skipping", name
            )
            continue
        tools.append(tool)
    return tools


def init_tools() -> None:
    """Initialize and register all tools via auto-discovery. Called on startup.

    Generic tools (app/tools/generic/) are always loaded. Bundles are
    discovered by scanning bundles/ for sub-packages: each bundle's __init__
    registers a manifest (app/tools/bundle.py) and its tool modules load only
    when its declared config_flag is true. Core never names a bundle — drop a
    folder under bundles/ + add its TOOL_BUNDLE_<NAME>_ENABLED flag.
    """
    import pkgutil
    import importlib
    import app.tools.generic
    import bundles
    from app.tools.bundle import BUNDLE_REGISTRY

    settings = get_settings()

    # 1a. Always load generic tools
    for _, module_name, _ in pkgutil.iter_modules(app.tools.generic.__path__):
        if not module_name.startswith("_"):
            try:
                importlib.import_module(f"app.tools.generic.{module_name}")
            except Exception as e:
                logger.error("Failed to load generic tool %s: %s", module_name, e)

    # 1b. Discover bundles by directory scan. Importing a bundle package runs
    #     its __init__ (which calls register_bundle); we then load that bundle's
    #     tool modules only when its manifest's config_flag is enabled. Core
    #     names no bundle here (DESIGN.md §5 2026-06-05).
    for _, pkg_name, is_pkg in pkgutil.iter_modules(bundles.__path__):
        if not is_pkg or pkg_name.startswith("_"):
            continue
        try:
            pkg = importlib.import_module(f"bundles.{pkg_name}")  # runs register_bundle
        except Exception as e:
            logger.error("Failed to import bundle %s: %s", pkg_name, e)
            continue
        bundle = BUNDLE_REGISTRY.get(pkg_name)
        flag = bundle.config_flag if bundle else f"TOOL_BUNDLE_{pkg_name.upper()}_ENABLED"
        if not bool(getattr(settings, flag, False)):
            # Disabled — drop its manifest so its hooks don't fire, skip tools.
            BUNDLE_REGISTRY.pop(pkg_name, None)
            continue
        for _, module_name, _ in pkgutil.iter_modules(pkg.__path__):
            if not module_name.startswith("_"):
                try:
                    importlib.import_module(f"bundles.{pkg_name}.{module_name}")
                except Exception as e:
                    logger.error("Failed to load %s tool %s: %s", pkg_name, module_name, e)

    # 2. Apply per-tool config flags. Each tool declares the Settings attribute
    #    that enables/disables it via its `config_flag` attribute (DESIGN.md §5
    #    2026-06-05) — core no longer enumerates tool names in a central table,
    #    so a bundle owns its own toggles. Tools with config_flag=None are
    #    always enabled. Missing settings default to True (fail-open, matching
    #    the prior behaviour for unmapped tools).
    for tool in TOOL_REGISTRY.values():
        if tool.config_flag:
            tool.enabled_by_config = bool(getattr(settings, tool.config_flag, True))

    logger.info(
        "Initialized %d tools (%d enabled)",
        len(TOOL_REGISTRY),
        len([t for t in TOOL_REGISTRY.values() if t.enabled_by_config]),
    )
