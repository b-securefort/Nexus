"""
Agent orchestrator — main agent loop with tool calling and approval gating.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import AsyncGenerator

from openai import AzureOpenAI
from sqlmodel import Session, select

from app.agent.approvals import create_pending_approval, update_approval_risk, wait_for_approval
from app.agent.circuit_breaker import CircuitOpenError, check as cb_check, record_failure as cb_failure, record_success as cb_success
from app.agent.compaction import get_original_task, load_compacted_history
from app.agent.concurrency import get_user_semaphore, run_in_tool_executor
from app.agent.questions import create_pending_question, wait_for_answer
from app.agent.risk_review import assess_risk
from app.agent.streaming import (
    sse_approval_required,
    sse_done,
    sse_error,
    sse_message_saved,
    sse_question_answered,
    sse_question_required,
    sse_token,
    sse_token_refresh_required,
    sse_tool_call_start,
    sse_tool_executing,
    sse_tool_output_chunk,
    sse_tool_result,
)
from app.auth.entra import (
    arm_token_status,
    clear_arm_token_override,
    get_arm_token_override,
)
from app.auth.models import User
from app.config import get_settings
from app.db.models import Conversation, Message
from app.kb.indexer import get_index_summary
from app.skills.models import Skill
from app.tools.base import (
    AzureToolBase,
    TOOL_CALLS,
    Tool,
    classify_tool_outcome,
    kill_conversation_processes,
    resolve_tools,
    set_arm_token,
    set_conversation_id,
    set_skill_name,
)

logger = logging.getLogger(__name__)

# Safety caps
MAX_TOOL_ITERATIONS = 15  # Increased to allow room for retry strategies

# A4 — Conversation lease heartbeat. The orchestrator stamps
# `conversations.lease_heartbeat_at` at most this often during a long turn so
# the frontend can detect a dead worker (heartbeat older than ~2× this value)
# and offer the user a "restart turn" affordance.
LEASE_HEARTBEAT_INTERVAL_SECONDS = 30.0
LEASE_STALE_AFTER_SECONDS = 60.0


def _lease_owner_id() -> str:
    """Identify the FastAPI worker that's writing heartbeats. Hostname:pid is
    enough to disambiguate replicas in a multi-instance deployment; pid alone
    is enough for the current single-replica setup."""
    import os
    import socket
    try:
        return f"{socket.gethostname()}:{os.getpid()}"
    except Exception:
        return f"unknown:{os.getpid()}"


def _write_lease_heartbeat(session: Session, conversation_id: int) -> None:
    """Update the lease heartbeat for this conversation. Best-effort — a
    failure to write the heartbeat must not abort the chat turn."""
    try:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            return
        conv.lease_heartbeat_at = datetime.now(timezone.utc)
        conv.lease_owner = _lease_owner_id()
        session.add(conv)
        session.commit()
    except Exception:
        logger.exception("Lease heartbeat write failed for conv=%s", conversation_id)


def _clear_lease(session: Session, conversation_id: int) -> None:
    """Drop the lease at end-of-turn so the next request can immediately tell
    that no one is currently holding it. Best-effort."""
    try:
        conv = session.get(Conversation, conversation_id)
        if conv is None:
            return
        conv.lease_heartbeat_at = None
        conv.lease_owner = None
        session.add(conv)
        session.commit()
    except Exception:
        logger.exception("Lease clear failed for conv=%s", conversation_id)


def cleanup_interrupted_turn(session: Session, conversation_id: int) -> None:
    """Idempotent end-of-turn cleanup, safe to call when a turn is interrupted.

    The normal done-path in `handle_chat` already clears these, but a client
    disconnect / Stop never reaches it — the generator is closed mid-stream. The
    chat endpoint calls this from a `finally` so a stopped turn doesn't leave a
    stale conversation lease (which would mislead the "restart turn" affordance)
    or a dangling ARM-token override behind.
    """
    # Kill any script still running for this conversation (the Stop / disconnect
    # kill switch — see §5 2026-06-04). No-op when nothing is registered.
    try:
        kill_conversation_processes(conversation_id)
    except Exception:
        logger.debug("process kill failed for conv=%s", conversation_id, exc_info=True)
    _clear_lease(session, conversation_id)
    try:
        clear_arm_token_override(conversation_id)
    except Exception:
        logger.debug("ARM override clear failed for conv=%s", conversation_id, exc_info=True)

# Tools whose errors should trigger automatic multi-strategy retry
_COMMAND_TOOLS = {"az_cli", "execute_script", "az_resource_graph"}

# Tools whose failure→success transitions yield a generalizable learning. A
# superset of _COMMAND_TOOLS: retry escalation stays gated on _COMMAND_TOOLS,
# but learning capture also covers REST and diagram-as-code tools, which emit
# real errors (status:error) the agent recovers from within a turn. Read/search/
# ask_user are intentionally excluded — their "failures" (missing path, no
# results, user intent) don't generalize into a reusable lesson.
# See DESIGN.md §5 2026-06-04 "Decouple learning-eligibility from retry".
_LEARNING_ELIGIBLE_TOOLS = _COMMAND_TOOLS | {
    "az_rest_api",
    "az_devops",
    "generate_drawio_from_python",
    "generate_python_diagram",
}

# Strong references to in-flight background learning-write tasks. asyncio holds
# only weak references to tasks, so without this a fire-and-forget task can be
# GC'd before it finishes. Entries are removed via add_done_callback.
_learning_write_tasks: set = set()

# Max consecutive failures on the same type of tool before giving up
_MAX_RETRIES_PER_TOOL = 3

# After this many user denials in a single turn, the orchestrator stops
# prompting the user and auto-refuses any further approval-gated calls for the
# rest of the turn — a structural backstop against an agent that tries to route
# around a refusal by re-issuing the action through another tool/path. Set to 1:
# a single refusal ends the approval surface for the turn (no re-prompting).
_MAX_DENIALS_PER_TURN = 1

# Fed back to the model when the user denies an approval. A denial is an
# intentional, terminal decision — NOT a syntax error or a blocked path to be
# retried. This message must steer the model away from re-attempting the same
# outcome through a different tool, command, REST call, or script.
_DENIAL_FEEDBACK = (
    "DENIED BY USER. The user explicitly refused to allow this action. This is a "
    "final decision — not a syntax error, not a permissions problem, and not a "
    "blocked path to work around. Do NOT retry this command, and do NOT attempt to "
    "achieve the same result by any other means (a different tool, a REST API call, "
    "a generated script, or rephrased arguments). Stop, acknowledge that the user "
    "declined, and ask what they would like to do instead."
)

# Used once the per-turn denial limit is hit and the orchestrator auto-refuses
# without prompting the user again.
_DENIAL_AUTODENY_FEEDBACK = (
    "DENIED (auto). The user already refused this action in this turn, so it was "
    "blocked automatically without prompting them again. Stop attempting it by any "
    "means and respond to the user in plain text — do not emit further tool calls "
    "to accomplish this."
)


def _tool_control_outcome(approval_denied: bool, tool_result: str) -> tuple[str, bool]:
    """Decide the control-flow outcome of a tool result.

    Returns ``(envelope_status, is_error)`` where ``envelope_status`` is one of
    ``"denied" | "error" | "success"`` and ``is_error`` drives the
    multi-strategy retry. A user denial is **terminal**: status ``"denied"`` and
    ``is_error=False``, so it can never feed the retry escalation that routes the
    model around a refusal via another tool/path. (Approval timeouts remain
    errors but are not denials.)
    """
    if approval_denied:
        return "denied", False
    is_error = (
        tool_result.strip().startswith("Error")
        or "Approval timed out" in tool_result
    )
    return ("error" if is_error else "success"), is_error

# Narration-instead-of-action detection. The architect / drawio-diagrammer
# skill prompts say "tool calls are not narration", but the model sometimes
# ends a response with "I'll generate the diagram now" and emits NO tool
# call, leaving the user to type "continue" to advance. When the model does
# this we inject a synthetic system message and re-enter the loop once,
# instead of yielding `done`. Capped at one nudge per chat turn so we can't
# infinite-loop on a model that keeps narrating.
_DEFERRED_ACTION_PATTERN = re.compile(
    r"\b("
    r"i\s?'?ll|i\s+will|i\s?'?m\s+going\s+to|i\s?'?m\s+about\s+to|"
    r"let\s+me|next\s+i\s?'?ll|i\s?'?ll\s+now|i\s+can"
    r")\s+"
    # Optional adverbial modifier between the intent and the verb:
    # "I'll NOW generate", "Let me FIRST render", "I will THEN write".
    r"(?:(?:now|then|first|finally|also|just|quickly|briefly)\s+)?"
    r"(generate|render|create|write|run|execute|query|fetch|read|call|"
    r"patch|add|build|draw|sketch|produce|emit|make)\b",
    re.IGNORECASE,
)


def _looks_like_deferred_action(text: str) -> bool:
    """True if the tail of an assistant message announces a future action
    that should have been a tool call. Only inspects the last 400 chars,
    because the announcement is almost always the closing sentence; matching
    against the full body causes false positives on agents recapping past
    work ("I'll briefly remind you that I generated...")."""
    if not text:
        return False
    tail = text[-400:] if len(text) > 400 else text
    return _DEFERRED_ACTION_PATTERN.search(tail) is not None


_NARRATION_NUDGE_MESSAGE = (
    "[system nudge] Your previous response announced you would call a tool "
    "but did not actually call it. The user cannot see your intent — only "
    "your tool calls. Make the tool call NOW, in this same response. If "
    "you have no tool to call (you were waiting for user input, the request "
    "is complete, or you need clarification), reply with that explicitly "
    "instead of restating intent."
)
MAX_HISTORY_MESSAGES = 50

# Per-tool size caps for the in-memory `messages` list that goes back to the
# LLM. The full result is still persisted to DB and streamed to the UI; only
# the prompt copy is trimmed. Keeps long shell/CLI dumps from drowning out
# the original task across iterations.
_TOOL_RESULT_LIMITS = {
    "az_cli": 4_000,
    "az_resource_graph": 4_000,
    "execute_script": 4_000,
    "read_kb_file": 6_000,
    "read_file": 6_000,
    "search_kb_hybrid": 4_000,
}
_DRAWIO_TOOLS = {"render_drawio", "validate_drawio", "generate_file", "patch_drawio_cell"}

# Track 4D — threshold above which the head+tail truncation is replaced by
# an LLM summarisation pass. The old head+tail split could leave the model
# staring at half a JSON object and either truncate mid-value (parse error)
# or duplicate keys (model confusion). Below the threshold, head+tail is
# fine — JSON parse errors won't matter because the model only sees a tiny
# snippet either way.
_LLM_TRUNCATE_THRESHOLD = 2_048


def _summarize_tool_result_with_llm(tool_name: str, enveloped_result: str) -> str | None:
    """LLM-summarise a tool result that's too large for the in-prompt context.
    Returns a compact summary string on success, or None on failure (caller
    falls back to head+tail truncation).

    Synchronous so it can be invoked from the orchestrator via
    `asyncio.to_thread`. Uses the same Azure OpenAI deployment as the chat
    model — a separate gpt-4o-mini deployment isn't a hard requirement; what
    matters is that the call is bounded, deterministic, and short.
    """
    settings = get_settings()
    try:
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            timeout=float(settings.AOAI_TIMEOUT_SECONDS),
            max_retries=0,
        )
        # Cap the input we send to the summariser so a multi-MB shell dump
        # doesn't burn the model's context window before it can compress it.
        # Keep both head and tail so summarisation reflects both ends.
        truncated_input = enveloped_result
        max_in = 24_000
        if len(enveloped_result) > max_in:
            half = max_in // 2
            truncated_input = (
                enveloped_result[:half]
                + f"\n...[middle {len(enveloped_result) - max_in} chars elided]...\n"
                + enveloped_result[-half:]
            )
        resp = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compress tool output for a downstream agent. "
                        "Preserve EVERY detail relevant to deciding the next "
                        "action: resource names, error codes, counts, specific "
                        "field values. Drop boilerplate, headers, pagination "
                        "tokens, blank lines, repeated values. Keep it under "
                        "1500 characters. Output the summary text only — no "
                        "preface, no JSON wrapper, no quotes."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Tool: {tool_name}\n\n"
                        f"Raw output (length {len(enveloped_result)}):\n"
                        f"{truncated_input}\n\n"
                        "Produce the compressed summary now."
                    ),
                },
            ],
            temperature=0.0,
            max_completion_tokens=600,
        )
        compressed = (resp.choices[0].message.content or "").strip()
        if not compressed:
            return None
        return (
            f"[LLM-compressed tool output for `{tool_name}` "
            f"— original {len(enveloped_result)} chars, full in DB]\n"
            f"{compressed}"
        )
    except Exception:
        logger.exception("LLM tool-output summarisation failed for %s", tool_name)
        return None


def _truncate_tool_result(tool_name: str, enveloped_result: str) -> str:
    """Trim an enveloped tool result for the in-memory messages list.

    DB content stays full — this only affects what we send back to OpenAI on
    the next iteration so retries and tool dumps don't push the original task
    out of context.

    Track 4D — For outputs above `_LLM_TRUNCATE_THRESHOLD` (2 KB) we attempt
    an LLM summarisation pass. On any summariser failure we fall back to the
    legacy head+tail split below, so a degraded LLM never breaks the chat
    turn outright.
    """
    # For drawio tools, strip echoed XML from the envelope.data.xml field
    # if it's present — the validator/renderer often echoes the whole file
    # and we don't need the model to re-read it.
    if tool_name in _DRAWIO_TOOLS and len(enveloped_result) > 4_000:
        try:
            envelope = json.loads(enveloped_result)
            data = envelope.get("data")
            if isinstance(data, dict) and isinstance(data.get("xml"), str):
                xml_len = len(data["xml"])
                if xml_len > 500:
                    data["xml"] = f"[XML omitted, {xml_len} chars — full content in DB]"
                    return json.dumps(envelope, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass

    # 4D — Attempt LLM summarisation for sufficiently-large outputs. We don't
    # touch error envelopes — the model needs the exact error text to retry.
    is_error_envelope = False
    if len(enveloped_result) > _LLM_TRUNCATE_THRESHOLD:
        try:
            envelope = json.loads(enveloped_result)
            if isinstance(envelope, dict) and envelope.get("status") == "error":
                is_error_envelope = True
        except (json.JSONDecodeError, TypeError):
            pass
    if (
        len(enveloped_result) > _LLM_TRUNCATE_THRESHOLD
        and not is_error_envelope
    ):
        summarised = _summarize_tool_result_with_llm(tool_name, enveloped_result)
        if summarised:
            return summarised
        # fall through to head+tail

    limit = _TOOL_RESULT_LIMITS.get(tool_name)
    if limit and len(enveloped_result) > limit:
        head_size = int(limit * 0.75)
        tail_size = limit - head_size
        head = enveloped_result[:head_size]
        tail = enveloped_result[-tail_size:]
        omitted = len(enveloped_result) - head_size - tail_size
        return f"{head}\n...[truncated {omitted} chars — full output in DB]...\n{tail}"
    return enveloped_result


def _strip_retry_messages_for_tool(messages: list[dict], tool_name: str) -> int:
    """Remove any `[RETRY STRATEGY ...]` system messages mentioning `tool_name`
    from the in-memory messages list. Called after a successful retry so the
    chatter that scaffolded the recovery doesn't linger across iterations.
    Returns the count removed.
    """
    marker = "[RETRY STRATEGY"
    keep: list[dict] = []
    removed = 0
    for m in messages:
        content = m.get("content")
        if (
            m.get("role") == "system"
            and isinstance(content, str)
            and content.startswith(marker)
            and tool_name in content
        ):
            removed += 1
            continue
        keep.append(m)
    if removed:
        messages[:] = keep
    return removed


def _get_openai_client() -> AzureOpenAI:
    settings = get_settings()
    return AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        timeout=float(settings.AOAI_TIMEOUT_SECONDS),
        max_retries=0,  # circuit breaker handles retry logic; don't double-count failures
    )


# B3 — ARM token expiry pre-flight.
#
# Azure tools call az.cmd via AzureToolBase._run_az(), which only sees the ARM
# token through the per-request ContextVar set at the top of handle_chat. If
# the token has expired (or is about to) we'd otherwise burn an approval round-
# trip + a subprocess timeout before az reports "AADSTS70043 token expired."
# Detect the situation here, return a structured error to the agent, and emit
# `token_refresh_required` so the frontend can drive MSAL silent refresh.
_ARM_REFRESH_THRESHOLD_SECONDS = 60


def _tool_requires_arm_token(tool: Tool) -> bool:
    """True if the tool is implemented on top of AzureToolBase and therefore
    relies on the per-request ARM token to authenticate to Azure.

    Pure registry/class check — we don't introspect the args; even a "read-only"
    Resource Graph query needs a valid bearer to ARM.
    """
    return isinstance(tool, AzureToolBase)


def _current_arm_token(user: User, conversation_id: int) -> str | None:
    """Resolve the effective ARM token for this point in the turn.

    Prefers a per-conversation override (set via POST /api/chat/refresh-token
    after a `token_refresh_required` SSE) over the request-scoped token. This
    is what lets a long-running turn pick up a refreshed token without the
    user retyping the message (Track 4C).

    If an override exists and is used, the per-request ContextVar is updated
    too so subprocesses spawned later in the turn pick it up via the existing
    `_current_arm_token` ContextVar plumbing in `tools/base.py`.
    """
    override = get_arm_token_override(conversation_id)
    if override is not None and override != user.arm_token:
        user.arm_token = override
        set_arm_token(override)
        return override
    return user.arm_token


def _arm_token_error_payload(tool_name: str, status: str) -> str:
    """Structured tool-result string handed back to the agent when a tool call
    is short-circuited by the ARM expiry pre-flight. Phrased so the model
    surfaces the issue to the user and waits instead of grinding through
    retries that will all fail with the same expired token."""
    if status == "missing":
        msg = (
            f"Error: cannot call `{tool_name}` — no Azure access token is "
            "attached to this session. Ask the user to sign in to Azure (the "
            "frontend will prompt) and retry the same request."
        )
    elif status == "expired":
        msg = (
            f"Error: cannot call `{tool_name}` — the user's Azure access "
            "token has expired. The frontend has been notified to refresh; "
            "tell the user to wait a moment and re-send the same request "
            "(or click Retry). Do NOT retry this tool until that happens."
        )
    else:  # near_expiry
        msg = (
            f"Notice: the user's Azure access token will expire in under "
            f"{_ARM_REFRESH_THRESHOLD_SECONDS}s; calling `{tool_name}` now "
            "may fail mid-flight. The frontend has been told to refresh."
        )
    return msg


def _is_deployed_environment() -> bool:
    """True if Nexus is running on a hosted platform where the frontend is
    expected to supply per-user identity via the X-ARM-Token passthrough.

    Detected via `CONTAINER_APP_NAME`, which Azure Container Apps injects into
    every replica's environment. Local dev (env var unset) falls through to
    the server's `az login` session for Azure tools — DESIGN §2 Auth, §5
    2026-06-01. URL-based detection was rejected: every request-carried signal
    (Host, Origin, peer IP) is either client-spoofable from the internet or
    reports localhost behind the Container Apps ingress controller, inverting
    the intent.
    """
    return bool(os.environ.get("CONTAINER_APP_NAME", "").strip())


def _prefetch_safe_calls(
    tool_calls: list[dict], tools: list[Tool], user: User
) -> dict[str, tuple[asyncio.Task, list[str]]]:
    """Pre-dispatch safe-to-parallelise tool calls so they run concurrently
    while the serial loop processes each in arrival order.

    Returns `{call_id: (task, stream_chunks)}`. The serial loop awaits the
    task when it reaches that call instead of starting a fresh one. Chunks
    accumulate during execution; the serial loop yields them in order so the
    SSE event stream stays consistent regardless of parallelism.

    Calls that aren't safe (bad JSON, unknown tool, needs approval, ask_user,
    Azure tool with missing/expired ARM token) are not prefetched — the
    serial loop handles them with the original code path.
    """
    prefetched: dict[str, tuple[asyncio.Task, list[str]]] = {}
    for call in tool_calls:
        call_id = call.get("id") or ""
        func_name = call.get("function", {}).get("name", "")
        raw_args = call.get("function", {}).get("arguments") or ""
        if not call_id or not func_name:
            continue
        if func_name == "ask_user":
            continue
        try:
            func_args = json.loads(raw_args)
        except json.JSONDecodeError:
            continue
        tool = next((t for t in tools if t.name == func_name), None)
        if tool is None:
            continue
        if _tool_needs_approval(tool, func_args):
            continue
        if _tool_requires_arm_token(tool):
            # Prefetch reads from `user.arm_token`, which the serial loop
            # keeps in sync with any per-conversation override (Track 4C) via
            # `_current_arm_token()`. So a refreshed token posted between
            # turns is already reflected here.
            status_ = arm_token_status(
                user.arm_token,
                refresh_threshold_seconds=_ARM_REFRESH_THRESHOLD_SECONDS,
            )
            # "missing" only blocks in deployed environments where the frontend
            # supplies the ARM token. Locally (no Container Apps env var) the
            # call falls through to the server's `az login` session (DESIGN §2
            # Auth, §5 2026-06-01) and is safe to prefetch.
            block_statuses = (
                ("missing", "expired") if _is_deployed_environment() else ("expired",)
            )
            if status_ in block_statuses:
                continue  # serial loop will emit the refresh SSE event
        chunks: list[str] = []
        task = asyncio.create_task(
            _gated_tool_execute(
                user_oid=user.oid or "anonymous",
                tool=tool,
                func_args=func_args,
                user=user,
                call_id=call_id,
                chunk_sink=chunks,
            )
        )
        prefetched[call_id] = (task, chunks)
    return prefetched


async def _gated_tool_execute(
    *,
    user_oid: str,
    tool: Tool,
    func_args: dict,
    user: User,
    call_id: str,
    chunk_sink: list[str],
) -> str:
    """Run `_execute_tool_streaming` on the dedicated tool executor while
    holding the per-user concurrency semaphore.

    This is the single chokepoint A2 introduced: every tool dispatch (prefetch
    or serial) routes through here so:
      - Tool subprocesses share a bounded pool that doesn't compete with SQLite
        / KB / OpenAI threads on Python's default executor.
      - A single user can't fill the pool with their own parallel calls — the
        semaphore caps them at `_DEFAULT_USER_MAX_CONCURRENT` slots.
    """
    sem = get_user_semaphore(user_oid)
    async with sem:
        return await run_in_tool_executor(
            _execute_tool_streaming, tool, func_args, user, call_id, chunk_sink,
        )


def _tool_needs_approval(tool: Tool, args: dict) -> bool:
    """Check if a tool invocation requires user approval.
    
    Supports both static (requires_approval attribute) and dynamic
    approval (e.g., az_rest_api where GET is safe but mutations need approval).
    """
    if hasattr(tool, '_needs_approval'):
        # Pass the discriminator field (method for REST, action for DevOps)
        key = args.get("method") or args.get("action") or "GET"
        return tool._needs_approval(key)
    return tool.requires_approval


def _compose_system_prompt(
    skill: Skill,
    user: User,
    original_task: str = "",
    current_user_message: str = "",
) -> tuple[str, list[int], dict[str, str]]:
    """Compose the final system prompt per §11.6.

    `original_task` is the very first user message of the conversation; it is
    pinned at the end of the prompt so it survives history compaction and
    keeps the model focused across long tool-heavy turns.

    `current_user_message` drives retrieval of relevant agent learnings. We
    no longer inject the entire learn.md unconditionally — instead we pull
    top-K relevant entries from `agent_learnings` via embedding similarity.
    See app/agent/learnings.py.

    Returns (system_prompt, retrieved_learning_ids, segments). The caller passes
    the IDs back into mark_learning_outcome after subsequent tool calls so
    validation_count / failure_count stay current. `segments` is an ordered
    {display_label: text} map of the prompt's structural parts, used by the
    context-usage gauge to show what is filling the window (see token_usage.py).
    """
    from app.agent.learnings import retrieve_relevant_learnings

    kb_summary = get_index_summary()

    # Retrieval-on-context replaces the previous always-on injection.
    # Query is the current user message if present, otherwise the original
    # task — both are short and concrete enough to drive embedding search.
    retrieval_query = current_user_message.strip() or original_task.strip() or skill.display_name
    try:
        retrieved = retrieve_relevant_learnings(
            query=retrieval_query,
            tool_name_hint=None,  # could derive from skill in a follow-up
            top_k=5,
        )
    except Exception as e:
        logger.warning("Learnings retrieval failed (continuing without): %s", e)
        retrieved = []
    retrieved_ids = [r.id for r in retrieved]

    try:
        from bundles.azure.az_login_check import get_az_context_prompt
        az_context = get_az_context_prompt()
    except ImportError:
        az_context = ""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Static content FIRST (maximizes Azure OpenAI prompt cache prefix) ---
    static_policy = (
        "\n---\n"
        "## Tool hierarchy\n"
        "Always prefer tools in this order when querying Azure resources:\n"
        "1. **`az_resource_graph`** (KQL) — fastest, read-only, no approval. Use first for resource queries.\n"
        "2. **`az_cost_query`** — cost/usage data. No approval.\n"
        "3. **`az_monitor_logs`** — Log Analytics KQL queries. No approval.\n"
        "4. **`az_advisor`** / **`az_policy_check`** — recommendations and compliance. No approval.\n"
        "5. **`az_cli`** — general Azure operations. Requires approval for mutations.\n"
        "6. **`az_rest_api`** — direct ARM REST calls. GET=no approval, mutations=approval.\n"
        "7. **`az_devops`** — Azure DevOps pipelines/PRs/builds. Read=no approval, mutations=approval.\n"
        "8. **`execute_script`** — run a .ps1/.sh script that already exists under output/scripts/. Always requires approval. Write the script with `generate_file` first.\n\n"
        "Other tools:\n"
        "- **`network_test`** — DNS/port checks, NSG rules. No approval.\n"
        "- **`generate_file`** — Write files to output/ sandbox. No approval.\n"
        "- **`web_fetch`** — Fetch web page content. No approval.\n"
        "Before running any command, call `fetch_ms_docs` to verify the correct syntax.\n\n"
        "## Thinking before acting\n"
        "ALWAYS include a brief text explanation BEFORE making any tool call(s). "
        "The user must see your reasoning. Specifically:\n"
        "- **Before a tool call**: Explain what you're about to do and why (1-2 sentences).\n"
        "- **Before a retry**: Explain what went wrong with the previous attempt and what you'll try differently.\n"
        "- **Before multiple tool calls**: Explain why you need each one.\n"
        "NEVER emit tool calls without accompanying text content in the same response.\n\n"
        "## Retry policy\n"
        "When a tool call fails, you MUST try at least 3 different approaches before giving up:\n"
        "1. **Fix the syntax** — Read the error carefully, check docs with `fetch_ms_docs`, and retry.\n"
        "2. **Try a different approach** — Move down the tool hierarchy (Resource Graph → Az CLI/PowerShell → REST API).\n"
        "3. **Try the simplest form** — Strip to minimal parameters, or use a completely different tool.\n\n"
        "## Learning policy\n"
        "Relevant learnings from past failures (if any) are retrieved automatically and "
        "shown below. You do NOT call any tool to record or read learnings — the orchestrator "
        "records validated learnings automatically when you succeed after a failure. Your job "
        "is to *use* the retrieved learnings: review them before executing, and if a "
        "documented approach matches the current task, apply it.\n"
        "---"
    )

    # --- Dynamic content AFTER static (changes per conversation/turn) ---
    kb_block = (
        "\n---\n"
        "Knowledge base index (use read_kb_file or search_kb to retrieve full content):\n"
        f"{kb_summary}\n"
        "---"
    )

    learnings_block = ""
    if retrieved:
        bullets = []
        for r in retrieved:
            status_marker = "[CANONICAL]" if r.status == "active" else "[PROVISIONAL]"
            bullets.append(
                f"- {status_marker} [{r.category}] ({r.tool_name}) {r.summary}\n"
                f"    {r.details[:400]}"
            )
        learnings_block = (
            "\n---\n"
            "**Relevant agent learnings** (retrieved by similarity to the current request — "
            "treat CANONICAL as confirmed across multiple runs; PROVISIONAL is a single "
            "observation still being validated):\n"
            + "\n".join(bullets)
            + "\n---\n"
            "If a learning matches the current task, follow the documented approach. The "
            "orchestrator will track whether the operation then succeeds — confirmations "
            "promote provisional entries to canonical; repeated failures auto-archive them."
        )

    context_block = (
        f"\nCurrent user: {user.display_name} ({user.email})\n"
        f"Current date: {now}\n\n"
        f"{az_context}"
    )

    task_block = ""
    if original_task.strip():
        # Pin the user's first message so it can't be summarized away. The
        # truncation cap protects against pathological pastes; long tasks
        # should be in the message history too, this is just an anchor.
        task = original_task.strip()
        if len(task) > 2000:
            task = task[:2000] + " …[truncated]"
        task_block = (
            "\n---\n"
            "[Original task from user — always stay focused on this. Tool "
            "results and intermediate messages are scaffolding, not the goal]:\n"
            f"{task}\n"
            "---"
        )

    # Joined prompt: order is load-bearing (static prefix first for prompt-cache
    # hit rate, dynamic content after). Do not reorder.
    parts = [skill.system_prompt, static_policy, kb_block]
    if learnings_block:
        parts.append(learnings_block)
    parts.append(context_block)
    if task_block:
        parts.append(task_block)

    # Structural segments for the context-usage gauge. Skill body, framework
    # policy, user/date/Azure context, and the pinned task are all "System
    # prompt"; KB index and retrieved learnings are surfaced separately because
    # they're the variable, content-driven parts a user can act on.
    segments: dict[str, str] = {
        "System prompt": "\n".join(
            p for p in (skill.system_prompt, static_policy, context_block, task_block) if p
        ),
        "Knowledge base": kb_block,
    }
    if learnings_block:
        segments["Learnings"] = learnings_block

    return "\n".join(parts), retrieved_ids, segments


def _build_render_review_message(args: dict) -> dict | None:
    """If a render_drawio call produced an image, build a synthetic user message
    with the image inlined for the next model turn so the vision-capable model
    can review the rendered output.

    Returns None if the file doesn't exist, can't be read, or the format isn't
    something the vision API accepts. Not persisted to DB - lives only in the
    in-memory `messages` list for the current handle_chat invocation.
    """
    import base64
    from pathlib import Path

    filename = (args.get("filename") or args.get("file_name") or "").strip()
    fmt = (args.get("format") or "png").strip().lower()

    if not filename:
        return None
    # Tools like generate_drawio_from_python pass a stem (no extension) — the
    # .drawio file is what gets written + rendered to .png next to it.
    if not filename.endswith(".drawio"):
        filename = f"{filename}.drawio"
    if fmt not in ("png", "jpg", "jpeg"):
        # PDF/SVG aren't sent through OpenAI vision; skip image injection.
        return None

    image_path = (Path("output") / filename).with_suffix(f".{fmt}")
    try:
        if not image_path.is_file():
            return None
        data = image_path.read_bytes()
    except OSError:
        return None
    if not data:
        return None

    mime = "image/png" if fmt == "png" else "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    review_text = (
        f"Rendered image of {filename} for visual review. Check: "
        "(1) every edge label is readable and not overlapping any icon or "
        "another label, (2) every numbered badge sits next to the connector "
        "or icon it annotates, (3) connection lines do not pass through "
        "unrelated icons or container titles, (4) bidirectional flows are "
        "explicit. If you find issues, edit the .drawio with overwrite=true, "
        "re-render, and review again. If it looks good, tell the user it's ready."
    )
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": review_text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                    "detail": "high",
                },
            },
        ],
    }


def _attachment_for_rendered_png(args: dict) -> dict | None:
    """If a diagram tool call produced a PNG next to its .drawio file, build
    an attachment dict for the eventual assistant message's `attachments_json`.

    Mirrors the path resolution in `_build_render_review_message` but produces
    a frontend-friendly attachment record (served via `GET /api/output/<file>`)
    instead of an OpenAI vision message. Returns None when no PNG exists on
    disk yet, so iterations that fail validation don't attach stale images.
    """
    from pathlib import Path

    filename = (args.get("filename") or args.get("file_name") or "").strip()
    fmt = (args.get("format") or "png").strip().lower()
    if not filename:
        return None
    if not filename.endswith(".drawio"):
        filename = f"{filename}.drawio"
    if fmt not in ("png", "jpg", "jpeg"):
        return None

    image_path = (Path("output") / filename).with_suffix(f".{fmt}")
    try:
        stat = image_path.stat()
    except OSError:
        return None
    if not image_path.is_file() or stat.st_size == 0:
        return None

    png_name = image_path.name
    # Cache-bust on the file's mtime so that *editing* a diagram (which
    # overwrites <stem>.png in place, reusing the filename) yields a distinct
    # URL per render. Without this, the reused filename maps to one stable URL
    # and the browser keeps showing the already-painted image even though the
    # bytes on disk changed. serve_output matches only the path param, so the
    # query string passes through untouched.
    return {
        "url": f"/api/output/{png_name}?v={stat.st_mtime_ns}",
        "filename": png_name,
        "original_name": png_name,
        "mime": "image/png" if fmt == "png" else "image/jpeg",
    }


def _build_content_with_images(text: str, attachments_json: str) -> list[dict]:
    """Build OpenAI multi-part content array from text + image attachments.

    Converts stored attachment URLs to base64 data URLs for the OpenAI vision API.
    """
    import base64
    from pathlib import Path

    settings = get_settings()

    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})

    try:
        attachments = json.loads(attachments_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse attachments_json: %s", attachments_json[:200])
        return parts or [{"type": "text", "text": text}]

    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    for att in attachments:
        filename = att.get("filename", "")
        content_type = att.get("content_type", "image/png")
        file_path = upload_dir / filename

        if file_path.is_file():
            data = file_path.read_bytes()
            if not data:
                logger.warning("Image file is empty: %s", file_path)
                continue
            b64 = base64.b64encode(data).decode("ascii")
            logger.info("Attached image %s (%d bytes) to message", filename, len(data))
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{content_type};base64,{b64}", "detail": "auto"},
            })
        else:
            logger.warning("Image file not found: %s", file_path)

    return parts if parts else [{"type": "text", "text": text}]


def _load_message_history(session: Session, conversation_id: int) -> list[dict]:
    """Load message history for the conversation, limited to MAX_HISTORY_MESSAGES.

    Ensures tool-role messages are always preceded by an assistant message
    with matching tool_calls — the OpenAI API rejects orphaned tool messages.
    This can happen when the LIMIT window cuts off mid-tool-call sequence.
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())  # type: ignore
        .limit(MAX_HISTORY_MESSAGES)
    )
    rows = list(session.exec(stmt).all())
    rows.reverse()  # Chronological order

    messages = []
    for row in rows:
        msg: dict = {"role": row.role, "content": row.content}
        if row.role == "assistant" and row.tool_calls_json:
            tool_calls = json.loads(row.tool_calls_json)
            if tool_calls:
                msg["tool_calls"] = tool_calls
        if row.role == "tool":
            msg["tool_call_id"] = row.tool_call_id or ""
        # Include image attachments as multi-part content for OpenAI vision
        if row.role == "user" and row.attachments_json:
            msg["content"] = _build_content_with_images(row.content, row.attachments_json)
        messages.append(msg)

    # Collect tool_call_ids provided by assistant messages in the history
    valid_tool_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id") or ""
                if tc_id:
                    valid_tool_call_ids.add(tc_id)

    # Collect tool_call_ids that have a corresponding tool-role response
    answered_tool_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                answered_tool_call_ids.add(tc_id)

    # Drop tool-role messages whose tool_call_id has no matching assistant message
    # AND strip tool_calls from assistant messages whose responses are missing
    cleaned: list[dict] = []
    for msg in messages:
        if msg.get("role") == "tool":
            if msg.get("tool_call_id", "") not in valid_tool_call_ids:
                continue  # orphaned tool response
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            kept = [
                tc for tc in msg["tool_calls"]
                if (tc.get("id") or "") in answered_tool_call_ids
            ]
            if not kept:
                # All tool_calls are unanswered — drop the key entirely
                msg = {k: v for k, v in msg.items() if k != "tool_calls"}
            else:
                msg = {**msg, "tool_calls": kept}
        cleaned.append(msg)

    return cleaned


def _save_message(
    session: Session,
    conversation_id: int,
    role: str,
    content: str,
    tool_calls_json: str | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    attachments_json: str | None = None,
) -> Message:
    """Save a message to the database."""
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls_json=tool_calls_json,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        attachments_json=attachments_json,
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _skill_from_snapshot(snapshot_json: str) -> Skill:
    """Reconstruct a Skill from a conversation's skill snapshot."""
    data = json.loads(snapshot_json)
    return Skill(
        id=data.get("id", ""),
        name=data.get("name", ""),
        display_name=data.get("display_name", ""),
        description=data.get("description", ""),
        system_prompt=data.get("system_prompt", ""),
        tools=data.get("tools", []),
        source=data.get("source", "shared"),
    )


# ── Multi-strategy retry system ──────────────────────────────────────────────

def _build_docs_query(tool_name: str, func_args: dict, error_text: str) -> str:
    """Build a search query from a failed tool call to look up docs."""
    if tool_name == "az_cli":
        args = func_args.get("args", [])
        if args:
            return f"az {' '.join(args[:3])} syntax parameters"
    elif tool_name == "az_resource_graph":
        return f"Azure Resource Graph KQL query syntax {func_args.get('query', '')[:80]}"
    elif tool_name == "execute_script":
        path = func_args.get("path", "")
        return f"{path[:80]} script error"
    return f"Azure CLI {error_text[:60]}"


def _auto_lookup_docs(tool_name: str, func_args: dict, error_text: str) -> str | None:
    """Look up Microsoft Learn docs for a failed command."""
    from app.tools.base import TOOL_REGISTRY

    ms_docs_tool = TOOL_REGISTRY.get("fetch_ms_docs")
    if not ms_docs_tool or not ms_docs_tool.enabled_by_config:
        return None

    query = _build_docs_query(tool_name, func_args, error_text)
    try:
        from app.auth.models import User as UserModel
        dummy_user = UserModel(oid="system", email="system", display_name="system")
        docs_result = ms_docs_tool.execute({"query": query}, dummy_user)
        if docs_result and not docs_result.startswith("Error"):
            return docs_result
    except Exception as e:
        logger.debug("Auto docs lookup failed: %s", e)
    return None


def _get_retry_strategy(failure_count: int, tool_name: str, func_args: dict, error_text: str) -> str | None:
    """
    Return a strategy hint message based on how many times the same tool type
    has failed consecutively. Returns None if max retries exhausted.

    Strategy 1 (1st failure): Look up docs, fix syntax, retry
    Strategy 2 (2nd failure): Try a completely different command/approach
    Strategy 3 (3rd failure): Try a different tool entirely, or record learning and report
    """
    if failure_count >= _MAX_RETRIES_PER_TOOL:
        return None  # Give up

    docs_hint = _auto_lookup_docs(tool_name, func_args, error_text) or ""

    if failure_count == 1:
        # Strategy 1: Fix syntax using docs
        return (
            f"[RETRY STRATEGY 1/3 — Fix syntax] The `{tool_name}` call failed with:\n"
            f"```\n{error_text[:500]}\n```\n\n"
            f"Here are relevant Microsoft Learn docs:\n{docs_hint}\n\n"
            "**Action**: Carefully read the error message and docs above. "
            "Fix the command syntax, parameters, or flags and retry with the corrected command. "
            "Common issues: wrong parameter names, missing required args, incorrect flag format."
        )

    elif failure_count == 2:
        # Strategy 2: Try a different command/approach entirely
        alt_tools = {
            "az_cli": "For read queries, try `az_resource_graph` (KQL) — it's faster and needs no approval. For ARM operations not exposed by az_cli, use `az_rest_api` (with `body_file` for large payloads).",
            "az_resource_graph": "Try using `az_cli` with `az resource list` or similar commands. If that also fails, use `az_rest_api` to call the Azure REST API directly.",
            "execute_script": "Don't retry the same script. Inspect the script with `read_file`, fix it with `generate_file` (overwrite=true), and re-run. For Azure-specific work, prefer `az_cli` / `az_rest_api` over generating a wrapper script.",
        }
        alt_hint = alt_tools.get(tool_name, "Try a different tool.")
        return (
            f"[RETRY STRATEGY 2/3 — Different approach] `{tool_name}` has now failed twice.\n"
            f"Error: {error_text[:300]}\n\n"
            f"**Action**: Do NOT retry the same command again. Instead:\n"
            f"1. {alt_hint}\n"
            "2. Break the problem into smaller steps — first verify prerequisites, then attempt the operation.\n"
            "3. Re-read the **Relevant agent learnings** section in your system prompt — relevant entries are already retrieved.\n"
            "4. If using az_cli, try `az <command> --help` first to see the correct syntax."
        )

    else:
        # failure_count == 3 → Strategy 3: Last resort
        return (
            f"[RETRY STRATEGY 3/3 — Final attempt] `{tool_name}` has failed {failure_count} times.\n"
            f"Latest error: {error_text[:300]}\n\n"
            "**Action**: This is your LAST attempt. Choose ONE:\n"
            "1. Use a completely different tool to achieve the same goal.\n"
            "2. Try the simplest possible version of the command (fewer parameters, basic form).\n"
            "3. If nothing works, explain to the user what you tried and suggest they run "
            "the command manually. The orchestrator records learnings automatically on success-after-failure; "
            "no tool call is needed.\n\n"
            "Do NOT repeat the same command that already failed."
        )


def _build_failure_summary_for_learning(
    tool_name: str, attempts: list[tuple[dict, str]]
) -> str:
    """Build a summary of all failed attempts for recording in learn.md."""
    lines = [f"Failed {len(attempts)} attempts with `{tool_name}`:"]
    for i, (args, error) in enumerate(attempts, 1):
        args_str = json.dumps(args)[:200]
        lines.append(f"  Attempt {i}: args={args_str}, error={error[:150]}")
    return "\n".join(lines)


# B4 — Per-user tool call history.
#
# Previously a single global `dict[tool_name, list[timestamp]]` shared by every
# user — a noisy neighbour could trip the rate limit for everyone else, and
# concurrent calls could race on the same list without a lock. Now keyed by
# (user_oid, tool_name) and guarded by a single coarse lock. Stale entries
# (older than the largest configured window) are pruned on every access so the
# dict can't grow unbounded for users who fire one call and never come back.
def _schedule_learning_write(
    *,
    tool_name: str,
    final_successful_args: dict,
    prior_failures: list[tuple[dict, str]],
    originating_conversation_id: int,
) -> None:
    """LMI #1 — Schedule the learning derivation + judge + write on a
    background task so the orchestrator's SSE stream is never blocked by an
    LLM call (~1-3s for the rephraser + judge round trips).

    Runs in its own DB session because the orchestrator's session is owned by
    the request handler and may already have been closed by the time the
    background task fires. Failures are logged and silently swallowed — a
    missed learning is far less bad than failing the user's chat turn.
    """
    async def _do_write() -> None:
        try:
            from app.agent.learnings import (
                derive_learning_from_success,
                record_validated_learning,
            )
            from app.db.engine import get_session

            # derive now makes an LLM call (synthesis), so run it in a thread —
            # never on the event loop. It returns None when there's no
            # generalizable lesson, in which case there's nothing to record.
            derived = await asyncio.to_thread(
                derive_learning_from_success,
                tool_name=tool_name,
                final_successful_args=final_successful_args,
                prior_failures=prior_failures,
            )
            if not derived:
                logger.info("No generalizable learning derived for %s; nothing to record", tool_name)
                return

            def _persist() -> None:
                with get_session() as bg_session:
                    record_validated_learning(
                        session=bg_session,
                        tool_name=derived["tool_name"],
                        category=derived["category"],
                        summary=derived["summary"],
                        details=derived["details"],
                        prior_failures_summary=derived["prior_failures_summary"],
                        originating_conversation_id=originating_conversation_id,
                    )

            await asyncio.to_thread(_persist)
            logger.info(
                "Background-recorded learning for %s after %d failures",
                tool_name, len(prior_failures),
            )
        except Exception:
            logger.exception(
                "Background learning write failed for %s", tool_name,
            )

    try:
        # Retain a strong reference until completion. asyncio only keeps a weak
        # reference to tasks, so a fire-and-forget task with no saved reference
        # can be garbage-collected mid-flight — silently dropping the learning
        # write. Hold it in a module-level set and discard on done.
        task = asyncio.create_task(_do_write())
        _learning_write_tasks.add(task)
        task.add_done_callback(_learning_write_tasks.discard)
    except RuntimeError:
        # No running event loop (e.g. unit tests calling the orchestrator
        # sync helpers directly). Fall back to a synchronous best-effort
        # write so the behaviour matches the pre-LMI#1 path in that context.
        try:
            from app.agent.learnings import (
                derive_learning_from_success,
                record_validated_learning,
            )
            from app.db.engine import get_session

            derived = derive_learning_from_success(
                tool_name=tool_name,
                final_successful_args=final_successful_args,
                prior_failures=prior_failures,
            )
            if not derived:
                return
            with get_session() as bg_session:
                record_validated_learning(
                    session=bg_session,
                    tool_name=derived["tool_name"],
                    category=derived["category"],
                    summary=derived["summary"],
                    details=derived["details"],
                    prior_failures_summary=derived["prior_failures_summary"],
                    originating_conversation_id=originating_conversation_id,
                )
        except Exception:
            logger.exception(
                "Synchronous fallback learning write failed for %s", tool_name,
            )


def _build_prior_action_context(messages: list[dict]) -> str:
    """Compact description of the most recent agent action, for the
    user-correction extractor: the last assistant message's text plus the names
    of any tools it invoked. Returns "" when there's no prior agent turn (a
    first-message turn has nothing to correct)."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        parts: list[str] = []
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip()[:600])
        names = []
        for tc in (msg.get("tool_calls") or []):
            try:
                names.append(tc["function"]["name"])
            except (KeyError, TypeError, IndexError):
                continue
        if names:
            parts.append("Tools called: " + ", ".join(names))
        return "\n".join(parts) if parts else "(assistant turn with no text)"
    return ""


def _schedule_user_correction_capture(
    *,
    user_message: str,
    prior_action: str,
    originating_conversation_id: int,
) -> None:
    """Background extract→write for an explicit user teach-intent turn
    (DESIGN.md §5 2026-06-05). Mirrors `_schedule_learning_write`: runs off the
    request path on a strong-referenced task; falls back to a synchronous write
    when no event loop is running (unit tests calling the helper directly)."""
    def _extract_and_write(sync: bool) -> None:
        from app.agent.learn_capture import extract_user_correction
        from app.agent.learnings import record_user_correction_learning
        from app.db.engine import get_session

        derived = extract_user_correction(
            user_message=user_message, prior_action=prior_action,
        )
        if not derived:
            return
        with get_session() as bg_session:
            record_user_correction_learning(
                session=bg_session,
                originating_conversation_id=originating_conversation_id,
                **derived,
            )
        logger.info(
            "Captured user-correction learning for conv %s", originating_conversation_id,
        )

    async def _do_capture() -> None:
        try:
            await asyncio.to_thread(_extract_and_write, False)
        except Exception:
            logger.exception(
                "User-correction capture failed for conv %s", originating_conversation_id,
            )

    try:
        task = asyncio.create_task(_do_capture())
        _learning_write_tasks.add(task)
        task.add_done_callback(_learning_write_tasks.discard)
    except RuntimeError:
        # No running event loop (sync unit-test context). Best-effort.
        try:
            _extract_and_write(True)
        except Exception:
            logger.exception("Synchronous user-correction capture failed")


_tool_call_history: dict[str, dict[str, list[float]]] = {}
_tool_call_history_lock = threading.Lock()
# Global cap on how long any timestamp is retained — defensive pruning beyond
# the per-tool window so the dict can't leak for users who used a tool once
# and never came back. Set generously (1 hour) so we never prune entries that
# might still be inside a tool's own rate_limit_window.
_HISTORY_RETENTION_SECONDS = 3600


def _check_user_rate_limit(
    user_oid: str, tool_name: str, limit: int, window: int, now: float
) -> tuple[bool, list[float]]:
    """Per-user rate-limit check. Atomically prunes stale entries, decides
    allow/deny, and (when allowed) records the current call timestamp.

    Returns (allowed, current_history_after_decision). Caller only consults
    `allowed`; the second tuple element is exposed for tests.
    """
    with _tool_call_history_lock:
        user_bucket = _tool_call_history.setdefault(user_oid, {})
        history = user_bucket.get(tool_name, [])
        # Prune anything older than the tool's window — the rate check itself
        # only cares about this window, and the secondary retention cap is a
        # belt-and-braces guard against leaks elsewhere.
        cutoff = now - max(window, _HISTORY_RETENTION_SECONDS)
        window_cutoff = now - window
        pruned = [ts for ts in history if ts > cutoff]
        in_window = [ts for ts in pruned if ts > window_cutoff]

        if len(in_window) >= limit:
            user_bucket[tool_name] = pruned
            return False, pruned

        pruned.append(now)
        user_bucket[tool_name] = pruned
        # Opportunistically drop empty tool buckets and empty user buckets
        # touched but not used (defensive — won't trigger on this path).
        if not user_bucket:
            _tool_call_history.pop(user_oid, None)
        return True, pruned


def _reset_tool_call_history() -> None:
    """Test hook — wipe per-user history between cases."""
    with _tool_call_history_lock:
        _tool_call_history.clear()


def _execute_tool_streaming(
    tool: Tool, func_args: dict, user, call_id: str, chunk_sink: list[str]
) -> str:
    """Execute a tool using its streaming method, collecting output chunks.

    Chunks are appended to chunk_sink so the caller (async generator) can
    yield them as SSE events after this synchronous call returns.
    Returns the full combined tool output.
    """
    start_time = time.time()

    # Rate Limiting Check — per (user, tool) instead of global so one user can
    # not exhaust another's quota.
    if getattr(tool, "rate_limit_calls", None) is not None:
        window = getattr(tool, "rate_limit_window", 60)
        user_oid = getattr(user, "oid", None) or "anonymous"
        allowed, _hist = _check_user_rate_limit(
            user_oid=user_oid,
            tool_name=tool.name,
            limit=tool.rate_limit_calls,
            window=window,
            now=start_time,
        )
        if not allowed:
            logger.warning(
                "Rate limit tripped for tool %s user=%s", tool.name, user_oid,
            )
            err = (
                f"Error: Rate limit exceeded for `{tool.name}`. Maximum "
                f"{tool.rate_limit_calls} calls per {window} seconds. Please "
                "wait or use a different strategy."
            )
            chunk_sink.append(err)
            return err
        
    gen = tool.execute_streaming(func_args, user)
    full_result = ""
    try:
        while True:
            chunk = next(gen)
            chunk_sink.append(chunk)
    except StopIteration as e:
        full_result = e.value if e.value else ""
    # If the generator didn't return a value, build from chunks
    if not full_result and chunk_sink:
        full_result = "".join(chunk_sink)
    
    duration = time.time() - start_time
    outcome = classify_tool_outcome(full_result)
    TOOL_CALLS.labels(tool=tool.name, outcome=outcome).inc()

    # Structured Telemetry
    telemetry = {
        "event": "tool_execution",
        "tool_name": tool.name,
        "args_len": len(json.dumps(func_args)),
        "duration_sec": round(duration, 3),
        "result_len": len(full_result),
        "outcome": outcome,
    }
    logger.info("TELEMETRY: %s", json.dumps(telemetry))

    return full_result


async def handle_chat(
    session: Session,
    conversation: Conversation,
    user_message: str,
    user: User,
    attachments_json: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Main agent loop. Yields SSE events as strings.
    """
    settings = get_settings()

    # Propagate the user's ARM token into the tool execution context so every
    # Azure tool subprocess authenticates as the current user.
    set_arm_token(user.arm_token)
    # Propagate the conversation id so a tool running in the executor thread can
    # register its subprocess for the Stop / disconnect kill switch (§5 2026-06-04).
    set_conversation_id(conversation.id)

    # 1. Persist user message
    user_msg = _save_message(
        session, conversation.id, role="user", content=user_message,
        attachments_json=attachments_json,
    )
    yield sse_message_saved(user_msg.id, "user")

    # 2. Build request
    skill = _skill_from_snapshot(conversation.skill_snapshot_json)
    # Propagate the active skill slug into the tool execution context so tools
    # can enforce skill-scoped behaviour that the LLM keeps ignoring in the
    # system prompt (see generate_file's .drawio guard).
    set_skill_name(skill.name)
    client = _get_openai_client()
    messages, _deferred_compaction = load_compacted_history(
        session, conversation.id, client, settings.AZURE_OPENAI_DEPLOYMENT
    )
    original_task = get_original_task(session, conversation.id)
    system_prompt, retrieved_learning_ids, prompt_segments = _compose_system_prompt(
        skill, user,
        original_task=original_task,
        current_user_message=user_message,
    )
    tools = resolve_tools(skill.tools)
    tool_schemas = [t.to_openai_schema() for t in tools] if tools else None

    # User-correction capture (DESIGN.md §5 2026-06-05). When this turn is an
    # explicit teach-intent message AND there is a prior agent action to correct,
    # extract a generalizable lesson in the background. The marker pre-gate is a
    # cheap regex; the extractor + write run off the request path and never block
    # the turn. Read/diagram/command tools are irrelevant here — the signal is
    # the user's words, not a tool outcome.
    if settings.LEARN_FROM_USER_CORRECTIONS:
        from app.agent.learn_capture import looks_like_teach_intent
        if looks_like_teach_intent(user_message):
            prior_action = _build_prior_action_context(messages)
            if prior_action:
                _schedule_user_correction_capture(
                    user_message=user_message,
                    prior_action=prior_action,
                    originating_conversation_id=conversation.id,
                )

    iteration = 0

    # Track consecutive failures per tool type for multi-strategy retry
    failure_tracker: dict[str, int] = {}  # tool_name -> consecutive failure count
    failure_history: dict[str, list[tuple[dict, str]]] = {}  # tool_name -> [(args, error), ...]

    # Denial tracking for this turn. A user refusal is terminal; once the limit
    # is hit, further approval-gated calls are auto-refused without re-prompting.
    denials_this_turn = 0
    auto_deny_approvals = False

    # Track .drawio iteration count per filename so we can encourage the model
    # to keep going after repeated validation failures. Smaller models tend to
    # give up after 3-4 failed validate cycles even when iterations remain.
    drawio_attempt_count: dict[str, int] = {}

    # PNGs produced by diagram tools during this turn. Attached to the
    # terminating assistant message (the one with no tool_calls) so the user
    # sees the rendered diagram inline alongside the agent's description.
    # De-duplicated by filename so multiple iterations of the same diagram
    # only show the latest render once.
    pending_render_attachments: dict[str, dict] = {}

    # Count of narration-nudge re-entries used this turn. Capped at 1 so the
    # loop can't be tricked into infinite continuation by a model that keeps
    # narrating without acting.
    narration_nudges_used: int = 0

    # Context-usage gauge payload. Captured on the FIRST LLM call of the turn so
    # it reflects RESTING occupancy (the context entering the turn, before this
    # turn's tool outputs balloon `messages`). Compaction bounds resting
    # occupancy, so this is stable turn-to-turn; sampling the last, tool-laden
    # call would report transient peak that compaction discards next turn. See
    # DESIGN.md §5 2026-06-05.
    resting_usage: dict | None = None

    # A4 — Mark this conversation as held by this worker. Refreshed at the
    # top of each iteration AND opportunistically during long approval waits.
    _last_heartbeat_ts: float = 0.0
    _write_lease_heartbeat(session, conversation.id)
    _last_heartbeat_ts = time.time()

    while iteration < MAX_TOOL_ITERATIONS:
        iteration += 1

        # Refresh the lease heartbeat. Throttled to the configured interval
        # so we don't slam SQLite with one write per loop iteration on
        # fast-iterating turns.
        if time.time() - _last_heartbeat_ts >= LEASE_HEARTBEAT_INTERVAL_SECONDS:
            _write_lease_heartbeat(session, conversation.id)
            _last_heartbeat_ts = time.time()

        try:
            # 3. Call Azure OpenAI
            api_messages = [{"role": "system", "content": system_prompt}] + messages

            create_kwargs = {
                "model": settings.AZURE_OPENAI_DEPLOYMENT,
                "messages": api_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_completion_tokens": 16384,
            }
            if tool_schemas:
                create_kwargs["tools"] = tool_schemas

            # Run the synchronous OpenAI streaming call in a thread so we
            # don't block the event loop (which would stall all other HTTP
            # requests, including health-checks and approval POSTs).
            # B10 — Use asyncio.to_thread instead of run_in_executor so the
            # current contextvars.Context (ARM token, active skill) propagates
            # into the worker thread. run_in_executor does NOT copy the
            # context by default, which silently broke any code path that
            # reads ContextVars from inside the streaming thread.
            chunk_queue: asyncio.Queue = asyncio.Queue()
            _SENTINEL = object()

            def _consume_openai_stream():
                try:
                    cb_check()
                    s = client.chat.completions.create(**create_kwargs)
                    for c in s:
                        chunk_queue.put_nowait(c)
                    cb_success()
                except CircuitOpenError as exc:
                    chunk_queue.put_nowait(exc)
                except Exception as exc:
                    cb_failure()
                    chunk_queue.put_nowait(exc)
                finally:
                    chunk_queue.put_nowait(_SENTINEL)

            stream_thread = asyncio.create_task(
                asyncio.to_thread(_consume_openai_stream)
            )

            # Consume stream
            assistant_content = ""
            tool_calls_accumulator: dict[int, dict] = {}
            stream_usage = None

            try:
                while True:
                    item = await chunk_queue.get()
                    if item is _SENTINEL:
                        break
                    if isinstance(item, Exception):
                        raise item
                    chunk = item

                    # Capture usage from the final chunk
                    if hasattr(chunk, 'usage') and chunk.usage is not None:
                        stream_usage = chunk.usage

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    # Text content
                    if delta.content:
                        assistant_content += delta.content
                        yield sse_token(delta.content)

                    # Tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.id:
                                tool_calls_accumulator[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_accumulator[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_accumulator[idx]["function"]["arguments"] += tc.function.arguments
            except Exception as stream_err:
                err_str = str(stream_err)
                if isinstance(stream_err, CircuitOpenError):
                    yield sse_error(str(stream_err))
                    return
                if "content_filter" in err_str or "content management policy" in err_str:
                    yield sse_error(
                        "Your message was flagged by the content filter. "
                        "Please rephrase and try again."
                    )
                    return
                raise
            finally:
                await stream_thread

            # Capture token usage and cache stats. Logged every iteration (per-call
            # visibility); the gauge payload is built only ONCE, on the first call
            # of the turn, to report resting occupancy (see resting_usage above).
            if stream_usage:
                prompt_tokens = stream_usage.prompt_tokens or 0
                completion_tokens = stream_usage.completion_tokens or 0
                cached = 0
                if hasattr(stream_usage, 'prompt_tokens_details') and stream_usage.prompt_tokens_details:
                    cached = getattr(stream_usage.prompt_tokens_details, 'cached_tokens', 0) or 0
                cache_pct = (cached / prompt_tokens * 100) if prompt_tokens > 0 else 0
                logger.info(
                    "Token usage — prompt: %d (cached: %d, %.1f%%), completion: %d, total: %d",
                    prompt_tokens, cached, cache_pct, completion_tokens,
                    prompt_tokens + completion_tokens,
                )
                if resting_usage is None:
                    # Structural, input-side occupancy breakdown for the gauge.
                    # tiktoken-counts each prompt segment and scales them to sum
                    # to the authoritative API prompt_tokens. On this first
                    # iteration `messages` is exactly the resting context that
                    # was sent — this turn's assistant reply and tool outputs
                    # have not been appended yet.
                    from app.agent.token_usage import build_segments, context_window_for_model
                    segments = build_segments(
                        system_segments=prompt_segments,
                        tool_schemas=tool_schemas,
                        messages=messages,
                        model=settings.AZURE_OPENAI_DEPLOYMENT,
                        prompt_tokens=prompt_tokens,
                    )
                    resting_usage = {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cached_tokens": cached,
                        "context_window": context_window_for_model(
                            settings.AZURE_OPENAI_DEPLOYMENT,
                            settings.AZURE_OPENAI_CONTEXT_WINDOW_TOKENS,
                        ),
                        "model": settings.AZURE_OPENAI_DEPLOYMENT,
                        "segments": segments,
                    }

            # Build tool_calls list
            tool_calls = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]

            # Save assistant message. When the model has finished iterating
            # this turn (no further tool calls), attach any PNGs produced by
            # diagram tools so the user sees the rendered diagram inline
            # alongside the description. Mid-loop assistant messages don't
            # get attachments — they'd appear on intermediate bubbles that
            # then trigger more tools, which reads as visual noise.
            tc_json = json.dumps(tool_calls) if tool_calls else None
            attachments_json: str | None = None
            if not tool_calls and pending_render_attachments:
                attachments_json = json.dumps(list(pending_render_attachments.values()))
                pending_render_attachments.clear()
            assistant_msg = _save_message(
                session,
                conversation.id,
                role="assistant",
                content=assistant_content,
                tool_calls_json=tc_json,
                attachments_json=attachments_json,
            )
            yield sse_message_saved(assistant_msg.id, "assistant")

            # Append to history
            assistant_hist: dict = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_hist["tool_calls"] = tool_calls
            messages.append(assistant_hist)

            if not tool_calls:
                # Detect the narration-instead-of-action pattern: the model
                # wrote "I'll generate the diagram now" but emitted no tool
                # call, leaving the user to type "continue". When detected,
                # inject one system reminder and re-enter the loop instead
                # of terminating. Cap at one nudge per turn so a stubborn
                # narrator can't infinite-loop us. Feature-flagged so an
                # operator can turn it off if false positives hurt.
                if (
                    settings.NARRATION_NUDGE_ENABLED
                    and narration_nudges_used < 1
                    and iteration < MAX_TOOL_ITERATIONS
                    and _looks_like_deferred_action(assistant_content)
                ):
                    narration_nudges_used += 1
                    messages.append({
                        "role": "system",
                        "content": _NARRATION_NUDGE_MESSAGE,
                    })
                    logger.info(
                        "Narration nudge fired (iter=%d, tail=%r)",
                        iteration,
                        (assistant_content or "")[-120:],
                    )
                    continue
                yield sse_done(conversation.id, usage=resting_usage)
                # Schedule deferred compaction work (LLM summarisation of cache
                # misses encountered when loading history this turn).  These run
                # after the response is delivered so they don't block the user.
                for _work_fn in _deferred_compaction:
                    asyncio.ensure_future(asyncio.to_thread(_work_fn))
                _deferred_compaction.clear()
                # A4 — Clear the lease so the next polling client immediately
                # sees that no worker is holding the conversation.
                _clear_lease(session, conversation.id)
                # 4C — Drop any leftover ARM token override; the next chat
                # request will attach a fresh one in the X-ARM-Token header.
                clear_arm_token_override(conversation.id)
                return

            # 4. Execute tool calls
            # Synthetic user messages (e.g. rendered-PNG image attachments) are
            # collected here and appended only AFTER every tool_call_id from the
            # current assistant turn has been answered. Inserting a user message
            # mid-loop would orphan later tool responses and the API would 400.
            post_iteration_messages: list[dict] = []

            # A5 — Pre-dispatch parallelisable tool calls so multiple safe
            # tools issued in the same assistant turn (e.g. several Resource
            # Graph queries) run concurrently. The serial loop below still
            # iterates calls in arrival order — it just awaits the prefetched
            # task instead of starting a fresh one.
            #
            # A call is parallelisable when ALL of:
            #   - args parse as JSON (else we report the parse error inline)
            #   - tool exists in the registry
            #   - tool does NOT need approval (approvals are inherently serial
            #     because they require user interaction)
            #   - tool is NOT `ask_user` (waits on user)
            #   - the ARM-token pre-flight wouldn't reject it
            prefetched_calls = _prefetch_safe_calls(tool_calls, tools, user)
            for call in tool_calls:
                call_id = call["id"]
                func_name = call["function"]["name"]
                raw_args = call["function"]["arguments"] or ""
                json_parse_error: str | None = None
                try:
                    func_args = json.loads(raw_args)
                except json.JSONDecodeError as je:
                    func_args = {}
                    # Surface the real cause instead of silently passing {} to the tool.
                    # Almost always means the model's output hit max_completion_tokens
                    # mid-argument, leaving an unterminated JSON string. Tell the model
                    # explicitly so it can recover (split into smaller writes, etc.).
                    json_parse_error = (
                        f"Tool call arguments JSON failed to parse: {je.msg} "
                        f"at char {je.pos} of {len(raw_args)}. "
                        "This usually means the response was truncated by the model's "
                        "token limit while emitting a large argument (e.g. file content). "
                        "Retry with a smaller payload — for example, split the file into "
                        "multiple writes using overwrite=true and append in chunks."
                    )
                    logger.warning(
                        "Tool args JSON parse failed for %s: %s (raw_len=%d)",
                        func_name, je, len(raw_args),
                    )

                yield sse_tool_call_start(call_id, func_name, func_args)

                # Chunk sink for streaming tool output
                _stream_chunks: list[str] = []

                # Find tool
                tool = next((t for t in tools if t.name == func_name), None)
                arm_short_circuit_status: str | None = None
                if (
                    tool is not None
                    and not json_parse_error
                    and _tool_requires_arm_token(tool)
                ):
                    # B3 — Pre-flight the ARM token. If we don't have one, or
                    # it's expired / about to expire, short-circuit before we
                    # ask for approval / burn a subprocess slot. The frontend
                    # listens for `token_refresh_required` and drives the
                    # MSAL silent refresh, after which the user re-sends.
                    #
                    # _current_arm_token() prefers a per-conversation override
                    # (Track 4C) so a refresh posted while the turn was idle
                    # is honoured here without restarting the turn.
                    effective_token = _current_arm_token(user, conversation.id)
                    status_ = arm_token_status(
                        effective_token,
                        refresh_threshold_seconds=_ARM_REFRESH_THRESHOLD_SECONDS,
                    )
                    # "missing" means no user ARM token was ever attached. In
                    # deployed environments (Container Apps) a missing token is
                    # a hard stop: Azure tools must run as the signed-in user,
                    # never the server identity. Locally we fall through to the
                    # developer's `az login` session (DESIGN §2 Auth, §5
                    # 2026-06-01). "expired"/"near_expiry" always refresh —
                    # they can only arise when a token was actually present.
                    refresh_statuses = ("expired", "near_expiry")
                    if _is_deployed_environment():
                        refresh_statuses = ("missing",) + refresh_statuses
                    if status_ in refresh_statuses:
                        arm_short_circuit_status = status_
                        yield sse_token_refresh_required(
                            conversation_id=conversation.id,
                            tool_name=func_name,
                            status=status_,
                        )
                        logger.warning(
                            "ARM token %s — short-circuiting %s for conv=%s",
                            status_, func_name, conversation.id,
                        )

                # Per-call flag: set when the user (or the auto-deny backstop)
                # refuses this approval-gated call. A denial is terminal — it
                # must not feed the multi-strategy retry or the learning path.
                approval_denied = False

                if json_parse_error:
                    # Skip tool execution — feed the parse error back so the model
                    # understands what went wrong on its own previous turn.
                    tool_result = f"Error: {json_parse_error}"
                elif not tool:
                    tool_result = f"Error: Unknown tool '{func_name}'"
                elif arm_short_circuit_status is not None and arm_short_circuit_status != "near_expiry":
                    # Only "missing" and "expired" are hard short-circuits.
                    # "near_expiry" still allows the call (better than leaving
                    # the user staring at a refresh prompt for a token that
                    # still has 50s left). The notification has been emitted
                    # so the frontend can refresh while the call is in flight.
                    tool_result = _arm_token_error_payload(func_name, arm_short_circuit_status)
                elif func_name == "ask_user":
                    # Special case: ask_user pauses the agent until the user
                    # picks options in the UI. The tool's execute() is a
                    # no-op fallback - the real flow is here: validate args,
                    # persist a PendingQuestion, emit the SSE event, await
                    # the answer, and feed the structured answers back to
                    # the model as the tool result.
                    from app.tools.generic.ask_user import validate_questions
                    validated, validation_err = validate_questions(
                        func_args.get("questions")
                    )
                    if validation_err is not None:
                        tool_result = f"Error: {validation_err}"
                    else:
                        record = create_pending_question(
                            session=session,
                            conversation_id=conversation.id,
                            user_oid=user.oid,
                            questions=validated,
                        )
                        yield sse_question_required(record.id, call_id, validated)
                        status, answers = await wait_for_answer(record.id)
                        if status == "answered" and answers is not None:
                            yield sse_question_answered(record.id, call_id, answers)
                            tool_result = json.dumps(
                                {"status": "answered", "answers": answers},
                                ensure_ascii=False,
                            )
                        else:
                            tool_result = json.dumps({
                                "status": "expired",
                                "message": (
                                    "The user did not answer in time. Make a "
                                    "reasonable default choice based on the "
                                    "request and proceed; tell the user which "
                                    "assumptions you made."
                                ),
                            })
                elif _tool_needs_approval(tool, func_args):
                    if auto_deny_approvals:
                        # The per-turn denial limit was already hit. Refuse
                        # without prompting the user again so a denial can't be
                        # turned into approval-spam by re-issuing the action.
                        approval_denied = True
                        tool_result = _DENIAL_AUTODENY_FEEDBACK
                        logger.warning(
                            "Auto-denied %s (conv=%s) — denial limit reached this turn",
                            func_name, conversation.id,
                        )
                    else:
                        # Render the card immediately (risk "pending"), then run the
                        # independent advisory risk review off the event loop and
                        # re-emit the same card with the resolved verdict. The
                        # frontend keeps Allow disabled until the verdict arrives.
                        # Advisory only — never gates execution (§5 2026-06-04).
                        reason = func_args.get("reason", "No reason provided")
                        approval = create_pending_approval(
                            session=session,
                            conversation_id=conversation.id,
                            user_oid=user.oid,
                            tool_name=func_name,
                            tool_args_json=json.dumps(func_args),
                            reason=reason,
                            risk_level="pending",
                        )
                        yield sse_approval_required(
                            approval.id, func_name, func_args, reason,
                            risk_level="pending",
                        )

                        # Separate review LLM (fails closed to >= caution).
                        verdict = await asyncio.to_thread(assess_risk, func_name, func_args)
                        update_approval_risk(
                            session, approval.id, verdict.risk_level, verdict.description
                        )
                        yield sse_approval_required(
                            approval.id, func_name, func_args, reason,
                            risk_level=verdict.risk_level,
                            risk_description=verdict.description,
                        )

                        # Wait for approval
                        status = await wait_for_approval(approval.id)

                        if status == "approved":
                            yield sse_tool_executing(call_id, func_name)
                            tool_result = await _gated_tool_execute(
                                user_oid=user.oid or "anonymous",
                                tool=tool,
                                func_args=func_args,
                                user=user,
                                call_id=call_id,
                                chunk_sink=_stream_chunks,
                            )
                        elif status == "denied":
                            # Terminal user refusal — not a retryable failure.
                            approval_denied = True
                            denials_this_turn += 1
                            if denials_this_turn >= _MAX_DENIALS_PER_TURN:
                                auto_deny_approvals = True
                            tool_result = _DENIAL_FEEDBACK
                        else:
                            tool_result = "Approval timed out."
                else:
                    yield sse_tool_executing(call_id, func_name)
                    prefetched = prefetched_calls.pop(call_id, None)
                    if prefetched is not None:
                        task, prefetched_chunks = prefetched
                        try:
                            tool_result = await task
                        except Exception as exc:
                            logger.exception(
                                "Prefetched tool task failed for %s", func_name,
                            )
                            tool_result = f"Error: {exc}"
                        # Replace local chunk sink with the prefetched one so
                        # the existing "yield chunks" loop below stays unchanged.
                        _stream_chunks = prefetched_chunks
                    else:
                        tool_result = await _gated_tool_execute(
                            user_oid=user.oid or "anonymous",
                            tool=tool,
                            func_args=func_args,
                            user=user,
                            call_id=call_id,
                            chunk_sink=_stream_chunks,
                        )

                # Yield any streaming chunks as SSE events
                for chunk in _stream_chunks:
                    yield sse_tool_output_chunk(call_id, chunk)

                # Standardise output envelope. A user denial is its own terminal
                # status — never "error" — so it cannot feed the multi-strategy
                # retry (which routes around errors by trying other tools/paths).
                envelope_status, is_error = _tool_control_outcome(approval_denied, tool_result)

                try:
                    parsed_data = json.loads(tool_result)
                except Exception:
                    parsed_data = tool_result

                envelope = {
                    "status": envelope_status,
                    "tool": func_name,
                    "data": parsed_data,
                }
                enveloped_result = json.dumps(envelope, indent=2)

                # Save tool result to DB and history as the standardised envelope
                tool_msg = _save_message(
                    session,
                    conversation.id,
                    role="tool",
                    content=enveloped_result,
                    tool_call_id=call_id,
                    tool_name=func_name,
                )
                # In-memory copy is trimmed; DB and UI still get the full envelope.
                # 4D — _truncate_tool_result may issue an LLM summarisation call
                # for outputs > 2KB, so hop off the event loop. The DB write
                # above and the SSE event below are unaffected by the trim.
                trimmed_for_prompt = await asyncio.to_thread(
                    _truncate_tool_result, func_name, enveloped_result,
                )
                messages.append({"role": "tool", "content": trimmed_for_prompt, "tool_call_id": call_id})
                
                # The UI gets the raw text for streaming/display, but can also parse the enveloped result if needed
                yield sse_tool_result(call_id, func_name, tool_result)

                # Whenever a .drawio diagram has been (re)written or rendered,
                # queue a synthetic user message that inlines the PNG so the
                # model can visually review the result on the next iteration.
                # generate_file auto-renders the PNG, so this fires on every
                # diagram generation - not just explicit render_drawio calls.
                # Defer the append until all tool_call_ids are answered to keep
                # API ordering valid.
                if not is_error and func_name in (
                    "render_drawio", "generate_file", "patch_drawio_cell",
                    "generate_drawio_from_python",
                ):
                    review_msg = _build_render_review_message(func_args)
                    if review_msg is not None:
                        post_iteration_messages.append(review_msg)
                        logger.info(
                            "Queued rendered-image review message for %s (%s)",
                            func_args.get("filename", "<unknown>"),
                            func_name,
                        )
                    # Capture the PNG for inline display on the assistant's
                    # final response of this turn. Indexed by filename so that
                    # later iterations of the same diagram overwrite earlier
                    # captures — the user sees the most recent render only.
                    attachment = _attachment_for_rendered_png(func_args)
                    if attachment is not None:
                        pending_render_attachments[attachment["filename"]] = attachment

                # Encourage the model to keep iterating on diagram fixes
                # instead of giving up after a few failed validations. Smaller
                # models bail early when they see repeated FAILED reports;
                # tell them explicitly that iterations remain and remind them
                # to apply suggested-fix coordinates one at a time.
                if func_name in ("generate_file", "patch_drawio_cell") and not is_error:
                    diag_filename = (
                        func_args.get("filename") or func_args.get("file_name") or ""
                    )
                    if diag_filename.endswith(".drawio"):
                        if "Validation FAILED" in tool_result:
                            drawio_attempt_count[diag_filename] = (
                                drawio_attempt_count.get(diag_filename, 0) + 1
                            )
                            attempt = drawio_attempt_count[diag_filename]
                            iters_left = MAX_TOOL_ITERATIONS - iteration
                            if attempt >= 2 and iters_left >= 3:
                                post_iteration_messages.append({
                                    "role": "system",
                                    "content": (
                                        f"[diagram iteration {attempt} for "
                                        f"{diag_filename}] Validation is still "
                                        f"failing. You have ~{iters_left} tool "
                                        f"iterations left this turn — keep going, "
                                        "don't stop until the file passes and is "
                                        "rendered. Apply ONLY THE FIRST violation's "
                                        "suggested-fix coordinate this round (use "
                                        "patch_drawio_cell — it's faster and won't "
                                        "regress other parts). Trying to fix all "
                                        "violations at once is what's causing the "
                                        "back-and-forth: each rewrite shifts other "
                                        "cells and creates new violations."
                                    ),
                                })
                        elif "Validation PASSED" in tool_result:
                            drawio_attempt_count.pop(diag_filename, None)

                # Multi-strategy retry: track failures and escalate
                if approval_denied:
                    # A user refusal is terminal: not a failure to retry (that
                    # would route the model around the denial), and not a
                    # success to learn from. Skip both paths entirely.
                    pass
                elif func_name in _LEARNING_ELIGIBLE_TOOLS and is_error:
                    # Auth errors — clear cache so next attempt re-checks
                    if "az login" in tool_result or "not logged in" in tool_result.lower():
                        try:
                            from bundles.azure.az_login_check import clear_login_cache
                            clear_login_cache()
                        except ImportError:
                            pass

                    # Track this failure. This drives both multi-strategy retry
                    # (command tools) and success-after-failure learning capture
                    # (the broader learning-eligible set).
                    failure_tracker[func_name] = failure_tracker.get(func_name, 0) + 1
                    if func_name not in failure_history:
                        failure_history[func_name] = []
                    failure_history[func_name].append((func_args, tool_result))
                    count = failure_tracker[func_name]

                    # Retry escalation is only for command tools. Other
                    # learning-eligible tools (az_rest_api, az_devops, the
                    # diagram-as-code tools) have their own recovery paths — we
                    # still track their failures so a later success is learnable.
                    if func_name in _COMMAND_TOOLS:
                        strategy = _get_retry_strategy(count, func_name, func_args, tool_result)
                        if strategy:
                            messages.append({"role": "system", "content": strategy})
                            logger.info(
                                "Retry strategy %d/%d triggered for %s (failure #%d)",
                                min(count, _MAX_RETRIES_PER_TOOL),
                                _MAX_RETRIES_PER_TOOL,
                                func_name,
                                count,
                            )
                        else:
                            # All retries exhausted — report to the user. Learning
                            # is NOT recorded here (we record on success-after-failure,
                            # not on confirmed failure — a failure pattern without a
                            # known fix doesn't give the next run useful guidance).
                            summary = _build_failure_summary_for_learning(
                                func_name, failure_history[func_name]
                            )
                            give_up_msg = (
                                f"[ALL RETRIES EXHAUSTED] `{func_name}` failed {count} times.\n"
                                f"{summary}\n\n"
                                "Tell the user what you tried, what went wrong, and suggest "
                                "they run the command manually or check their environment/permissions."
                            )
                            messages.append({"role": "system", "content": give_up_msg})
                            logger.warning(
                                "All %d retries exhausted for %s", _MAX_RETRIES_PER_TOOL, func_name
                            )
                elif func_name in _LEARNING_ELIGIBLE_TOOLS:
                    # Tool succeeded — check if there were prior failures to learn from
                    prior_failures = failure_history.get(func_name)
                    if prior_failures:
                        # Strip the retry-strategy chatter from in-memory history
                        # now that we no longer need it to scaffold the recovery.
                        stripped = _strip_retry_messages_for_tool(messages, func_name)
                        if stripped:
                            logger.info(
                                "Pruned %d retry-strategy messages for %s after success",
                                stripped, func_name,
                            )
                        # LMI #1 — Schedule the learning write (judge + rephrase
                        # + gates + DB) on a background task instead of blocking
                        # the SSE stream. The orchestrator returns the chat
                        # response immediately; the judge grades + persists
                        # out-of-band. A failure here can't stall the user.
                        _schedule_learning_write(
                            tool_name=func_name,
                            final_successful_args=func_args,
                            prior_failures=list(prior_failures),
                            originating_conversation_id=conversation.id,
                        )
                    # Reset tracker
                    failure_tracker.pop(func_name, None)
                    failure_history.pop(func_name, None)

                # Validation-on-retrieval: if learnings were retrieved into this
                # turn's system prompt AND this tool call resolved, update their
                # success/failure counters. Heuristic — the agent may have
                # ignored the retrieved entries — but across many turns this
                # provides a directional signal that promotes load-bearing
                # entries and archives drifted ones.
                if retrieved_learning_ids and func_name in _LEARNING_ELIGIBLE_TOOLS:
                    try:
                        from app.agent.learnings import mark_learning_outcome
                        mark_learning_outcome(
                            retrieved_learning_ids,
                            succeeded=(not is_error),
                        )
                    except Exception:
                        logger.exception("mark_learning_outcome failed")

            # All tool_call_ids in this assistant turn are now answered;
            # safe to splice in any deferred user-role messages.
            if post_iteration_messages:
                messages.extend(post_iteration_messages)

            # A5 — Drop any prefetched tasks the serial loop never consumed.
            # Should be empty in steady state (every tool_call took one branch);
            # this guards against a future code path that classifies differently.
            for _leftover_id, (leftover_task, _) in prefetched_calls.items():
                if not leftover_task.done():
                    leftover_task.cancel()
            prefetched_calls.clear()

            # 5. Loop back

        except Exception as e:
            logger.error("Agent loop error: %s", str(e), exc_info=True)
            yield sse_error(str(e))
            return

    # Exceeded max iterations
    yield sse_error("Maximum tool call iterations exceeded")
