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
from app.agent.risk_review import assess_risk, render_for_human, review_fingerprint
from app.agent.streaming import (
    sse_approval_required,
    sse_done,
    sse_error,
    sse_iteration_limit,
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
    TOOL_CALLS,
    TOOL_REGISTRY,
    Tool,
    classify_tool_outcome,
    kill_conversation_processes,
    mask_tool_call_args,
    redact_tool_output,
    resolve_tools,
    set_arm_token,
    set_conversation_id,
    set_skill_name,
    set_user_oid,
)
from app.agent.usage_ledger import record_usage
from app.tools.bundle import (
    bundle_context_prompts,
    bundle_prompt_fragments,
    dispatch_tool_error,
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

# Per-tool capability lookup (DESIGN.md §5 2026-06-05). Whether a tool drives
# multi-strategy retry (`retry_eligible`) or success-after-failure learning
# capture (`learning_eligible`) is declared on the tool itself, not hardcoded
# here — so a bundle owns the facts about its own tools and core never names
# them. Learning is a superset of retry (REST/diagram tools are learnable but
# have their own recovery paths); that distinction is now two independent attrs.
# See DESIGN.md §5 2026-06-04 "Decouple learning-eligibility from retry".
def _tool_has(tool_name: str, attr: str) -> bool:
    """True when the registered tool named `tool_name` has capability `attr` set;
    unknown name → False. Replaces the old _COMMAND_TOOLS / _LEARNING_ELIGIBLE_TOOLS
    name-sets."""
    tool = TOOL_REGISTRY.get(tool_name)
    return bool(getattr(tool, attr, False)) if tool is not None else False

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

# Fed back when the approve→execute integrity check (hardening #20) fails: the
# reviewed/approved bytes no longer match what is on disk at execution time. This
# is terminal and NON-retryable — auto-retrying would just race the changed file
# again. The model must regenerate the artifact and obtain a fresh approval, which
# re-fingerprints from scratch.
_INTEGRITY_FEEDBACK = (
    "ABORTED — the approved content changed before it ran. The bytes you had "
    "approved no longer match what is on disk, so execution was blocked to honour "
    "exactly what the user reviewed. Do NOT reuse the prior approval. Re-generate "
    "the artifact deterministically and request approval again."
)


def _tool_control_outcome(
    approval_denied: bool, tool_result: str, integrity_failed: bool = False
) -> tuple[str, bool]:
    """Decide the control-flow outcome of a tool result.

    Returns ``(envelope_status, is_error)`` where ``envelope_status`` is one of
    ``"denied" | "error" | "success"`` and ``is_error`` drives the
    multi-strategy retry. A user denial is **terminal**: status ``"denied"`` and
    ``is_error=False``, so it can never feed the retry escalation that routes the
    model around a refusal via another tool/path. (Approval timeouts remain
    errors but are not denials.)

    An ``integrity_failed`` outcome (the #20 approve→execute fingerprint mismatch)
    is likewise terminal/non-retryable — ``is_error=False`` so the retry escalation
    can't re-race the changed file — but reuses the ``"denied"`` status since the
    user-gated action did not proceed; the distinct ``_INTEGRITY_FEEDBACK`` text
    in ``data`` tells the model *why* (changed, not refused).
    """
    if approval_denied or integrity_failed:
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
    r"let\s+me|next\s+i\s?'?ll|i\s?'?ll\s+now"
    r")\s+"
    # Optional adverbial modifier between the intent and the verb:
    # "I'll NOW generate", "Let me FIRST render", "I will THEN write".
    r"(?:(?:now|then|first|finally|also|just|quickly|briefly)\s+)?"
    r"(generate|render|create|write|run|execute|query|fetch|read|call|"
    r"patch|add|build|draw|sketch|produce|emit|make)\b",
    re.IGNORECASE,
)
# NB: "i can" is deliberately NOT in the intent list above. "I can generate a
# diagram if you'd like" is an OFFER awaiting user confirmation, not a deferred
# action — nudging it turns an offer into an unrequested tool call.


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
def _tool_result_limit(tool_name: str) -> int | None:
    """In-prompt size cap for this tool's result, declared on the tool via the
    `result_limit` capability attribute (was the _TOOL_RESULT_LIMITS table)."""
    tool = TOOL_REGISTRY.get(tool_name)
    return getattr(tool, "result_limit", None) if tool is not None else None

# Track 4D — threshold above which the head+tail truncation is replaced by
# an LLM summarisation pass. The old head+tail split could leave the model
# staring at half a JSON object and either truncate mid-value (parse error)
# or duplicate keys (model confusion).
#
# Raised 2 KB → 16 KB with the high-tier context window: a 16 KB tool result
# is ~4K tokens — trivially affordable in a 400K window, and the verbatim
# output is strictly better than a lossy summary (exact names, IDs, error
# text survive). The LLM pass also cost 1-3s of latency per large result.
# Only genuinely huge dumps (multi-hundred-KB CLI output) are summarised now.
_LLM_TRUNCATE_THRESHOLD = 16_384


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
        record_usage(getattr(resp, "usage", None), settings.AZURE_OPENAI_DEPLOYMENT)  # aux spend → ledger
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
    if _tool_has(tool_name, "is_diagram_tool") and len(enveloped_result) > 4_000:
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

    limit = _tool_result_limit(tool_name)
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


# 429 handling for the MAIN chat stream. The high-tier deployment has a modest
# TPM quota and diagram turns are the heaviest thing Nexus does (long prompt +
# a vision-review image per render), so transient throttles are normal — but
# with max_retries=0 a single 429 previously killed the whole turn AND counted
# toward opening the shared circuit breaker. Retry with the service-provided
# Retry-After (capped), and only report failure when retries are exhausted.
_RATE_LIMIT_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_SLEEP_SECONDS = 30.0


def _retry_after_seconds(exc) -> float:
    """Best-effort Retry-After from a RateLimitError; default 5s."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val:
            return min(float(val), _RATE_LIMIT_MAX_SLEEP_SECONDS)
    except (TypeError, ValueError):
        pass
    return 5.0


def _create_stream_with_429_retry(client, create_kwargs: dict):
    """`client.chat.completions.create(**kwargs)` with bounded 429 backoff.

    Synchronous — runs on the stream worker thread, never the event loop.
    Anything other than a rate limit propagates immediately.
    """
    from openai import RateLimitError

    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            return client.chat.completions.create(**create_kwargs)
        except RateLimitError as exc:
            if attempt == _RATE_LIMIT_MAX_ATTEMPTS:
                raise
            delay = _retry_after_seconds(exc)
            logger.warning(
                "429 from chat deployment (attempt %d/%d) — sleeping %.1fs then retrying",
                attempt, _RATE_LIMIT_MAX_ATTEMPTS, delay,
            )
            time.sleep(delay)


def _get_chat_client() -> AzureOpenAI:
    """Client for the MAIN agent loop. Uses the high-tier deployment's API
    version when AZURE_OPENAI_DEPLOYMENT_HIGH is configured; identical to
    `_get_openai_client()` otherwise. Auxiliary calls (compaction summaries,
    tool-output compression, judge, risk review) stay on the base client so
    the strong model's quota is spent only on agent reasoning."""
    settings = get_settings()
    return AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.chat_api_version,
        timeout=float(settings.AOAI_TIMEOUT_SECONDS),
        max_retries=0,
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
    """True if the tool needs its bundle's per-request credential (today, the
    Azure ARM token) to authenticate.

    Reads the `requires_credentials` capability attribute — reproduces the prior
    isinstance(tool, AzureToolBase) check without core naming the Azure base
    class (DESIGN.md §5 2026-06-05). NB AzCliTool does not set it, matching the
    prior isinstance behaviour.
    """
    return tool.requires_credentials


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

    # Per-turn dynamic context contributed by each enabled bundle (e.g. the
    # Azure CLI login state). Bundle-agnostic: core loops the registry instead
    # of importing a bundle by name (DESIGN.md §5 2026-06-05).
    bundle_context = bundle_context_prompts()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Static content FIRST (maximizes Azure OpenAI prompt cache prefix) ---
    static_policy = (
        "\n---\n"
        # Bundle-contributed tool guidance (e.g. the Azure tool hierarchy). It
        # lives in the static cache-prefix and is ordered deterministically by
        # bundle name so the prompt-cache prefix stays byte-stable (§5 2026-06-05).
        + bundle_prompt_fragments()
        + "## Output style\n"
        "Be concise. Lead with the answer or result; add detail only where it changes "
        "what the user does next. Do NOT restate tool output the user can already see, "
        "do NOT narrate routine tool calls, and do NOT summarize what you just did "
        "unless asked. Prefer short prose; use a list only for genuinely enumerable "
        "items, never as padding. When a tool call's purpose isn't obvious from "
        "context (or you're retrying after a failure), say why in one short clause "
        "first — otherwise just call it. Never end a reply announcing an action you "
        "can take right now: take it.\n\n"
        "## Retry policy\n"
        "When a tool call fails, you MUST try at least 3 different approaches before giving up:\n"
        "1. **Fix the syntax** — Read the error carefully, check docs with `fetch_ms_docs`, and retry.\n"
        "2. **Try a different approach** — Move down the tool hierarchy (Resource Graph → Az CLI/PowerShell → REST API).\n"
        "3. **Try the simplest form** — Strip to minimal parameters, or use a completely different tool.\n\n"
        "## Learning policy\n"
        "Relevant learnings from past failures (if any) are retrieved automatically and "
        "shown below. You do NOT call any tool to record or read learnings — the orchestrator "
        "records validated learnings automatically when you succeed after a failure, AND "
        "when the user explicitly asks to remember something ('please learn that…', 'add "
        "to learnings…'), it captures the lesson from their message automatically. When "
        "that happens, acknowledge it will be recorded — do NOT claim you saved it "
        "yourself, and do NOT say it cannot be saved. Your job is to *use* the retrieved "
        "learnings: review them before executing, and if a documented approach matches "
        "the current task, apply it.\n"
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
        f"{bundle_context}"
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


def _resolve_rendered_png(args: dict, func_name: str | None) -> tuple["Path | None", str, str]:
    """Resolve the on-disk PNG/JPG a diagram tool produced and the display name.

    Returns (image_path, display_name, fmt). image_path is None when the args
    don't name a file or the format isn't a vision-acceptable image.

    `generate_python_diagram` writes `output/<stem>.png` (no .drawio
    intermediate) and accepts a filename that may carry a `.png` suffix, so it
    resolves via the bare stem — matching the tool. The drawio tools may pass
    either a `.drawio` filename or a stem; the .drawio name is what gets
    rendered to a sibling .png (or .jpg).
    """
    from pathlib import Path

    filename = (args.get("filename") or args.get("file_name") or "").strip()
    fmt = (args.get("format") or "png").strip().lower()
    if not filename:
        return None, "", fmt
    if fmt not in ("png", "jpg", "jpeg"):
        # PDF/SVG aren't sent through OpenAI vision; skip image injection.
        return None, "", fmt

    if func_name == "generate_python_diagram":
        stem = Path(filename).stem
        return (Path("output") / f"{stem}.png"), f"{stem}.png", "png"

    # Tools like generate_drawio_from_python pass a stem (no extension) — the
    # .drawio file is what gets written + rendered to .png next to it.
    if not filename.endswith(".drawio"):
        filename = f"{filename}.drawio"
    return (Path("output") / filename).with_suffix(f".{fmt}"), filename, fmt


# Tolerance for the stale-render check. The PNG is written right after its
# .drawio source on a successful render, so "source newer than image by more
# than this" can only mean the PNG belongs to a PREVIOUS iteration (this
# iteration's export failed). Generous enough for slow sidecar renders.
_RENDER_STALE_AFTER_SECONDS = 2.0


def _render_is_stale(image_path) -> bool:
    """True when a sibling .drawio source is newer than the rendered image —
    i.e. the latest export failed and this PNG shows an OLDER version of the
    diagram. Attaching it would have the model (and the user) reviewing a
    picture that doesn't match the file that was just written. Tools without
    a .drawio intermediate (generate_python_diagram) never report stale."""
    src = image_path.with_suffix(".drawio")
    try:
        if not src.is_file():
            return False
        return src.stat().st_mtime - image_path.stat().st_mtime > _RENDER_STALE_AFTER_SECONDS
    except OSError:
        return False


# Convergence governor for the render-review loop (conv #355: 21 consecutive
# successful renders, each reviewed into "actual problems", until the iteration
# cap killed the turn — the open-ended review text never says "good enough").
# From _REVIEW_SOFT_CAP successful renders of the same file in one turn the
# review message demands semantic-only fixes and global-spacing-over-nudges;
# from _REVIEW_HARD_CAP it instructs the model to present the render as-is.
_REVIEW_SOFT_CAP = 3
_REVIEW_HARD_CAP = 5


def _build_render_review_message(
    args: dict, func_name: str | None = None, render_count: int = 1
) -> dict | None:
    """If a render_drawio call produced an image, build a synthetic user message
    with the image inlined for the next model turn so the vision-capable model
    can review the rendered output.

    `render_count` is how many successful renders this filename has had this
    turn (including this one); it selects the convergence-governor tier.

    Returns None if the file doesn't exist, can't be read, or the format isn't
    something the vision API accepts. Not persisted to DB - lives only in the
    in-memory `messages` list for the current handle_chat invocation.
    """
    import base64

    image_path, display_name, fmt = _resolve_rendered_png(args, func_name)
    if image_path is None:
        return None

    try:
        if not image_path.is_file():
            return None
        if _render_is_stale(image_path):
            # The export for THIS iteration failed; the PNG on disk is a
            # previous version. Reviewing it would mislead the model.
            logger.warning("Skipping stale render review for %s", image_path)
            return None
        data = image_path.read_bytes()
    except OSError:
        return None
    if not data:
        return None

    mime = "image/png" if fmt == "png" else "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    # One terse instruction for every diagram path. The skill owns the review
    # workflow; this message only delivers the image and sets the brevity bar —
    # a checklist here just produces a checklist-shaped essay per render.
    # The text escalates with render_count: an open-ended "fix what's wrong"
    # never converges, because a vision review can ALWAYS find a cosmetic flaw.
    if render_count >= _REVIEW_HARD_CAP:
        review_text = (
            f"Rendered image of {display_name} (render {render_count} this "
            "turn). STOP iterating on this diagram. Present this render to the "
            "user now in one or two sentences — and if the scorecard or "
            "advisories were non-zero, name each remaining defect as a caveat "
            "(never call a defective render ready). Do not call the diagram "
            "tool again this turn unless the user asks for a change."
        )
    elif render_count >= _REVIEW_SOFT_CAP:
        review_text = (
            f"Rendered image of {display_name} (render {render_count} this "
            "turn — diminishing returns). Re-render ONLY for a semantic error: "
            "wrong icon, wrong nesting, missing agreed structure, or a label "
            "that misstates the architecture. Cosmetic imperfection is not a "
            "reason to re-render. If text or lines still collide, make ONE "
            "global fix (increase spacing) instead of nudging individual "
            "nodes — per-node nudges shift neighbours and create new "
            "collisions. Otherwise present it to the user in one or two "
            "sentences — and if the scorecard or advisories were non-zero, "
            "name each remaining defect as a caveat instead of calling the "
            "diagram ready."
        )
    else:
        review_text = (
            f"Rendered image of {display_name}. Review it against what was agreed. "
            "If something is wrong (wrong icon or nesting, clipped/colliding text, a "
            "line through an icon, missing agreed structure), fix your source and "
            "re-run with the same filename. If it looks right, present it to the "
            "user in one or two sentences — mention only actual problems, never a "
            "checklist of things that passed."
        )
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": review_text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                    # "auto", not "high": structure presence/absence comes from
                    # the tool's authoritative Structure echo now — the image
                    # is only for visual quality, and "high" multiplied the
                    # input tokens of every diagram iteration (429 pressure on
                    # the modest high-tier TPM quota).
                    "detail": "auto",
                },
            },
        ],
    }


def _drop_stale_render_reviews(messages: list[dict], new_review: dict) -> int:
    """Remove superseded in-memory render-review messages for the same file.

    Each render of a filename queues a review message carrying a full base64
    image; on an iterate-heavy turn those accumulated (5 renders = 5 stale
    PNGs re-sent on every subsequent LLM call — pure 429 fuel). Only the
    LATEST render of a given file is worth reviewing, so drop earlier ones.
    These are synthetic in-memory messages, never persisted to DB.
    Returns the number removed.
    """
    try:
        new_text = new_review["content"][0]["text"]
        prefix = new_text.split(".", 1)[0]  # "Rendered image of <name>"
    except (KeyError, IndexError, TypeError):
        return 0
    if not prefix.startswith("Rendered image of"):
        return 0
    kept: list[dict] = []
    removed = 0
    for m in messages:
        content = m.get("content")
        if (
            m.get("role") == "user"
            and isinstance(content, list)
            and content
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
            and str(content[0].get("text", "")).startswith(prefix + ".")
        ):
            removed += 1
            continue
        kept.append(m)
    if removed:
        messages[:] = kept
    return removed


def _attachment_for_rendered_png(args: dict, func_name: str | None = None) -> dict | None:
    """If a diagram tool call produced a PNG next to its .drawio file, build
    an attachment dict for the eventual assistant message's `attachments_json`.

    Mirrors the path resolution in `_build_render_review_message` (via the shared
    `_resolve_rendered_png` helper) but produces a frontend-friendly attachment
    record (served via `GET /api/output/<file>`) instead of an OpenAI vision
    message. Returns None when no PNG exists on disk yet, so iterations that fail
    validation don't attach stale images.
    """
    image_path, _display_name, fmt = _resolve_rendered_png(args, func_name)
    if image_path is None:
        return None

    try:
        stat = image_path.stat()
    except OSError:
        return None
    if not image_path.is_file() or stat.st_size == 0:
        return None
    if _render_is_stale(image_path):
        # Failed export this iteration — don't show the user an old render
        # under the new description.
        logger.warning("Skipping stale render attachment for %s", image_path)
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


def _masked_tool_calls_json(tool_calls: list[dict]) -> str:
    """Serialise tool_calls for persistence with secret arg values masked
    (Surface A, §5 2026-06-13).

    Builds masked COPIES — the caller's `tool_calls` is left intact because the
    dispatch/prefetch path reads it for the real args. The mask is resolved per
    tool via the duck-typed `mask_args` hook, so the az bundle owns which of its
    args are secret. Future turns rebuild history from this masked DB copy, so no
    separate read-side masking is needed. Unparseable arguments pass through."""
    masked: list[dict] = []
    for call in tool_calls:
        fn = call.get("function", {}) if isinstance(call, dict) else {}
        name = fn.get("name", "")
        raw = fn.get("arguments") or ""
        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001
            masked.append(call)
            continue
        if not isinstance(parsed, dict):
            masked.append(call)
            continue
        clean = mask_tool_call_args(name, parsed)
        if clean == parsed:
            masked.append(call)
        else:
            masked.append({**call, "function": {**fn, "arguments": json.dumps(clean)}})
    return json.dumps(masked)


def _resolve_tuning_kwargs(skill: Skill, settings) -> dict[str, str]:
    """Decoder tuning for every main-loop call of a turn: skill frontmatter
    wins, the CHAT_REASONING_EFFORT / CHAT_VERBOSITY config defaults apply
    otherwise, and empty/None omits the parameter entirely (older deployments
    reject it with a 400). Reasoning tokens bill as output tokens, so
    read-only skills run at low effort; verbosity is enforced here at the
    decoder rather than only via the "Output style" prompt block."""
    tuning: dict[str, str] = {}
    effort = skill.reasoning_effort or settings.CHAT_REASONING_EFFORT
    if effort:
        tuning["reasoning_effort"] = effort
    verbosity = skill.verbosity or settings.CHAT_VERBOSITY
    if verbosity:
        tuning["verbosity"] = verbosity
    return tuning


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
        # Older snapshots predate these keys — None falls back to config.
        reasoning_effort=data.get("reasoning_effort"),
        verbosity=data.get("verbosity"),
    )


# ── Multi-strategy retry system ──────────────────────────────────────────────

def _build_docs_query(tool_name: str, func_args: dict, error_text: str) -> str:
    """Build a search query from a failed tool call to look up docs. A tool may
    supply tool-specific phrasing via `retry_docs_query`; otherwise fall back to
    a generic query — core hardcodes no tool names (DESIGN.md §5 2026-06-05)."""
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is not None:
        query = tool.retry_docs_query(func_args, error_text)
        if query:
            return query
    return f"{tool_name} {error_text[:60]} syntax"


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
        # Strategy 2: Try a different command/approach entirely. The tool-
        # specific "what to try instead" hint comes from the tool itself
        # (retry_alt_hint); core supplies only the generic framing.
        tool = TOOL_REGISTRY.get(tool_name)
        alt_hint = (tool.retry_alt_hint() if tool is not None else None) or "Try a different tool."
        return (
            f"[RETRY STRATEGY 2/3 — Different approach] `{tool_name}` has now failed twice.\n"
            f"Error: {error_text[:300]}\n\n"
            f"**Action**: Do NOT retry the same command again. Instead:\n"
            f"1. {alt_hint}\n"
            "2. Break the problem into smaller steps — first verify prerequisites, then attempt the operation.\n"
            "3. Re-read the **Relevant agent learnings** section in your system prompt — relevant entries are already retrieved."
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
                f"{tool.rate_limit_calls} calls per {window} seconds. Call the "
                f"`sleep` tool (e.g. sleep {window}s) to wait out the window, "
                "then retry the SAME action — or answer from data you already "
                "have. Throttling is not a reason to switch tools."
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
    # Propagate the user's oid so every completions call this turn (main loop +
    # aux) can attribute its token usage to them in the spend ledger (§5 2026-06-14).
    set_user_oid(user.oid)

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
    # Two clients: `chat_client` (high-tier deployment when configured) drives
    # the agent loop; `client` (base/mini deployment) serves the cheap
    # auxiliary work — compaction summaries, tool-output compression.
    client = _get_openai_client()
    chat_client = _get_chat_client()
    # load_compacted_history can make synchronous LLM calls (scaffold
    # summarisation on a compaction turn) and DB reads — run it off the event
    # loop so it can't stall other users' SSE streams / approval POSTs.
    messages, _deferred_compaction = await asyncio.to_thread(
        load_compacted_history,
        session, conversation.id, client, settings.AZURE_OPENAI_DEPLOYMENT,
    )
    original_task = get_original_task(session, conversation.id)
    system_prompt, retrieved_learning_ids, prompt_segments = _compose_system_prompt(
        skill, user,
        original_task=original_task,
        current_user_message=user_message,
    )
    tools = resolve_tools(skill.tools)
    tool_schemas = [t.to_openai_schema() for t in tools] if tools else None

    tuning_kwargs = _resolve_tuning_kwargs(skill, settings)

    # User-correction capture (DESIGN.md §5 2026-06-05). When this turn is an
    # explicit teach-intent message AND there is a prior agent action to correct,
    # extract a generalizable lesson in the background. The marker pre-gate is a
    # cheap regex; the extractor + write run off the request path and never block
    # the turn. Read/diagram/command tools are irrelevant here — the signal is
    # the user's words, not a tool outcome.
    if settings.LEARN_FROM_USER_CORRECTIONS:
        from app.agent.learn_capture import looks_like_teach_intent
        if looks_like_teach_intent(user_message):
            # A teach turn can OPEN a conversation ("please learn that …" as
            # the first message — conv #350) with no prior agent action to
            # correct. The extractor treats prior_action as context, not a
            # requirement, so pass an explicit placeholder instead of silently
            # dropping the user's instruction.
            prior_action = _build_prior_action_context(messages) or (
                "(none — standalone teaching instruction; no prior agent "
                "action in this conversation)"
            )
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

    # Successful renders per filename this turn — drives the review-loop
    # convergence governor (_REVIEW_SOFT_CAP/_REVIEW_HARD_CAP) so visual
    # polishing can't consume the whole iteration budget.
    render_review_count: dict[str, int] = {}

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

    # Context-usage gauge payload. Computed at turn END over the resting context
    # the NEXT turn will load (this turn's saved messages, with compaction
    # applied) so the gauge reflects post-turn occupancy — recent tool outputs
    # are carried verbatim (counted), older ones compacted (the gauge "drops when
    # compacted", matching the UI). The FIRST LLM call seeds a fallback payload
    # and a tiktoken->API calibration ratio, since the turn-end recompute has no
    # authoritative API prompt_tokens of its own. See DESIGN.md §5 2026-06-06.
    resting_usage: dict | None = None
    usage_calibration: float | None = None  # authoritative prompt_tokens / raw tiktoken total

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
                "model": settings.chat_deployment,
                "messages": api_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_completion_tokens": 16384,
                **tuning_kwargs,
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
                    s = _create_stream_with_429_retry(chat_client, create_kwargs)
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
                # Spend ledger: one row per main-loop completion, on the high
                # deployment (§5 2026-06-14). Fail-soft — never breaks the turn.
                record_usage(
                    stream_usage,
                    settings.chat_deployment,
                    user_oid=user.oid,
                    conversation_id=conversation.id,
                )
                if resting_usage is None:
                    # First LLM call: seed (1) a fallback gauge payload reflecting
                    # turn-start occupancy, and (2) the tiktoken->API calibration
                    # ratio used by the turn-end recompute. On this first
                    # iteration `messages` is exactly the resting context that was
                    # sent — this turn's assistant reply and tool outputs have not
                    # been appended yet. The displayed payload is overwritten at
                    # turn end (see the `not tool_calls` branch).
                    from app.agent.token_usage import build_segments, raw_total_tokens
                    raw_total_start = raw_total_tokens(
                        system_segments=prompt_segments,
                        tool_schemas=tool_schemas,
                        messages=messages,
                        model=settings.chat_deployment,
                    )
                    if raw_total_start > 0 and prompt_tokens > 0:
                        usage_calibration = prompt_tokens / raw_total_start
                    segments = build_segments(
                        system_segments=prompt_segments,
                        tool_schemas=tool_schemas,
                        messages=messages,
                        model=settings.chat_deployment,
                        prompt_tokens=prompt_tokens,
                    )
                    resting_usage = {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cached_tokens": cached,
                        # Explicit config wins over the substring table — see
                        # Settings.chat_context_window.
                        "context_window": settings.chat_context_window,
                        "model": settings.chat_deployment,
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
            # Surface A persistence (§5 2026-06-13): the STORED tool_calls have
            # secret arg values masked (e.g. `keyvault secret set --value ***`).
            # `tool_calls` itself is untouched — dispatch/prefetch below read it
            # for the real args; future turns rebuild from this masked DB copy.
            tc_json = _masked_tool_calls_json(tool_calls) if tool_calls else None
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
                # B5 — Recompute the gauge over the resting context the NEXT turn
                # will load, so it reflects post-turn occupancy (this turn's saved
                # messages, compacted) instead of turn-start. load_compacted_history
                # is cheap here (DB reads + cached summaries); the LLM-summary
                # callables it returns are discarded — the turn-start set scheduled
                # below still covers this turn's cache misses. No authoritative API
                # prompt_tokens is available, so scale the tiktoken total by the
                # calibration ratio captured on the first call.
                try:
                    from app.agent.token_usage import build_segments, raw_total_tokens
                    # Off the event loop: a fresh compaction pass here can make
                    # synchronous LLM summarisation calls (same reason as the
                    # turn-start load).
                    resting_messages, _discard = await asyncio.to_thread(
                        load_compacted_history,
                        session, conversation.id, client, settings.AZURE_OPENAI_DEPLOYMENT,
                    )
                    raw_resting = raw_total_tokens(
                        system_segments=prompt_segments,
                        tool_schemas=tool_schemas,
                        messages=resting_messages,
                        model=settings.chat_deployment,
                    )
                    est_prompt_tokens = (
                        round(raw_resting * usage_calibration)
                        if usage_calibration else raw_resting
                    )
                    if est_prompt_tokens > 0:
                        resting_usage = {
                            "prompt_tokens": est_prompt_tokens,
                            "completion_tokens": 0,
                            "cached_tokens": 0,
                            "context_window": settings.chat_context_window,
                            "model": settings.chat_deployment,
                            "segments": build_segments(
                                system_segments=prompt_segments,
                                tool_schemas=tool_schemas,
                                messages=resting_messages,
                                model=settings.chat_deployment,
                                prompt_tokens=est_prompt_tokens,
                            ),
                        }
                except Exception as e:
                    # Keep the turn-start fallback payload on any failure.
                    logger.warning("Turn-end resting-usage recompute failed: %s", e)

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
                # Per-call flag: set when the #20 approve→execute integrity check
                # trips (approved bytes changed before they ran). Also terminal.
                integrity_failed = False

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
                        # Deterministic, LLM-free render of the exact command for
                        # the human card — resolved once and reused across both
                        # emits so the human sees the full payload immediately,
                        # independent of the review LLM (§5 2026-06-12).
                        rendered_command, command_truncated = render_for_human(func_name, func_args)
                        # #20 approve→execute integrity: snapshot a fingerprint of
                        # any external mutable state the review resolved (the script
                        # body under output/scripts/). Captured here, in this same
                        # coroutine frame, alongside the human-visible render; the
                        # approve action only signals an event — execution resumes
                        # below with these locals intact, so no DB column is needed.
                        # None ⇒ tool exposes no such state (plain az_cli) ⇒ no check.
                        approved_fingerprint = review_fingerprint(func_name, func_args)
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
                            rendered_command=rendered_command,
                            command_truncated=command_truncated,
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
                            rendered_command=rendered_command,
                            command_truncated=command_truncated,
                        )

                        # Wait for approval
                        status = await wait_for_approval(approval.id)

                        if status == "approved":
                            # #20: re-fingerprint immediately before executing and
                            # abort if the approved bytes changed in the window (a
                            # later generate_file overwrite, a concurrent turn, or an
                            # injection swapping the script). Only enforced when an
                            # approval-time fingerprint existed; a None there leaves
                            # the execute path's own resolution to surface a clean
                            # 'not found' rather than a false abort.
                            if (
                                approved_fingerprint is not None
                                and review_fingerprint(func_name, func_args) != approved_fingerprint
                            ):
                                logger.warning(
                                    "Integrity check failed for %s (conv=%s): approved "
                                    "content changed before execution — aborting.",
                                    func_name, conversation.id,
                                )
                                integrity_failed = True
                                tool_result = _INTEGRITY_FEEDBACK
                            else:
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

                # Standardise output envelope. A user denial and a #20 integrity
                # abort are each terminal — never "error" — so neither can feed the
                # multi-strategy retry (which routes around errors by trying other
                # tools/paths, or here would just re-race the changed file).
                envelope_status, is_error = _tool_control_outcome(
                    approval_denied, tool_result, integrity_failed=integrity_failed
                )

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

                # Surface B redaction (§5 2026-06-13): the PERSISTED + future-turn
                # replayed copy has any credential-read output replaced with a
                # marker. The live SSE stream (below) and this turn's in-memory
                # history keep the real value, so the user receives the secret
                # they asked for and the agent can chain within the turn.
                persisted_result = redact_tool_output(func_name, func_args, tool_result)
                if persisted_result == tool_result:
                    persisted_content = enveloped_result
                else:
                    persisted_content = json.dumps(
                        {"status": envelope_status, "tool": func_name, "data": persisted_result},
                        indent=2,
                    )

                # Save tool result to DB as the standardised envelope (redacted
                # copy for credential-reads).
                tool_msg = _save_message(
                    session,
                    conversation.id,
                    role="tool",
                    content=persisted_content,
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

                # Whenever a diagram has been (re)written or rendered, queue a
                # synthetic user message that inlines the PNG so the model can
                # visually review the result on the next iteration. Driven by
                # the `attaches_render` capability on the tool — the previous
                # hardcoded name tuple silently missed generate_structured_diagram,
                # so structured renders never reached the model or the user.
                # Defer the append until all tool_call_ids are answered to keep
                # API ordering valid.
                if not is_error and _tool_has(func_name, "attaches_render"):
                    review_key = f"{func_name}:{func_args.get('filename', '')}"
                    render_review_count[review_key] = render_review_count.get(review_key, 0) + 1
                    review_msg = _build_render_review_message(
                        func_args, func_name,
                        render_count=render_review_count[review_key],
                    )
                    if review_msg is not None:
                        # Only the latest render of a file deserves review —
                        # purge superseded review images from earlier
                        # iterations (in-memory only) before queueing this one.
                        _drop_stale_render_reviews(messages, review_msg)
                        _drop_stale_render_reviews(post_iteration_messages, review_msg)
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
                    attachment = _attachment_for_rendered_png(func_args, func_name)
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
                if approval_denied or integrity_failed:
                    # A user refusal — or a #20 integrity abort — is terminal:
                    # not a failure to retry (retrying would route around the
                    # denial / re-race the changed file), and NOT a success to
                    # learn from (nothing ran). Skip both paths entirely; the
                    # is_error=False outcome must not reach the success-learning
                    # branch below and schedule a bogus learning.
                    pass
                elif _tool_has(func_name, "learning_eligible") and is_error:
                    # Let each enabled bundle react to the tool error (e.g. the
                    # Azure bundle clears its cached az-login state on an auth
                    # error) — core loops the registry, names no bundle.
                    dispatch_tool_error(tool_result)

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
                    if _tool_has(func_name, "retry_eligible"):
                        # Off the event loop: strategy 1 auto-fetches MS Learn
                        # docs (a synchronous HTTP call) to enrich the hint.
                        strategy = await asyncio.to_thread(
                            _get_retry_strategy, count, func_name, func_args, tool_result,
                        )
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
                elif _tool_has(func_name, "learning_eligible"):
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
                if retrieved_learning_ids and _tool_has(func_name, "learning_eligible"):
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

    # Exceeded max iterations — graceful wrap-up, not a dead error. The turn's
    # tool results are already persisted; what a hard error loses is (a) the
    # latest diagram render (attachments only ship on a final assistant
    # message, which never happened) and (b) a model-written checkpoint that a
    # follow-up "continue" turn can resume from. One last call with tools
    # disabled produces both. (Conv #355: two consecutive turns died at the
    # cap with a red banner and no render shown.)
    logger.warning(
        "Iteration budget (%d) exhausted for conversation %s — wrapping up",
        MAX_TOOL_ITERATIONS, conversation.id,
    )
    messages.append({
        "role": "system",
        "content": (
            "[iteration budget exhausted] You have used every tool iteration "
            "available this turn; tools are now disabled. In a few sentences, "
            "tell the user: what was accomplished, what (if anything) is still "
            "wrong or unfinished, and the exact next step you would take. If "
            "you produced a diagram, its latest render is attached to your "
            "reply automatically — do not apologize for it. Close by telling "
            "the user they can say 'continue' to let you resume."
        ),
    })
    wrap_content = ""
    try:
        def _wrap_call():
            # Text-only summary of the turn — no tool use, no deep reasoning
            # needed, so force low effort regardless of the skill's setting.
            wrap_tuning = dict(tuning_kwargs)
            if "reasoning_effort" in wrap_tuning:
                wrap_tuning["reasoning_effort"] = "low"
            return chat_client.chat.completions.create(
                model=settings.chat_deployment,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                max_completion_tokens=2048,
                **wrap_tuning,
            )
        resp = await asyncio.to_thread(_wrap_call)
        record_usage(getattr(resp, "usage", None), settings.chat_deployment)  # wrap-up spend → ledger
        wrap_content = (resp.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Iteration-cap wrap-up call failed")
    if not wrap_content:
        # The wrap-up call itself failed — still end the turn usable.
        wrap_content = (
            "I ran out of tool iterations for this turn before finishing. "
            "Everything done so far is saved — say 'continue' and I'll pick "
            "up where I left off."
        )
    yield sse_token(wrap_content)
    attachments_json = None
    if pending_render_attachments:
        attachments_json = json.dumps(list(pending_render_attachments.values()))
        pending_render_attachments.clear()
    assistant_msg = _save_message(
        session,
        conversation.id,
        role="assistant",
        content=wrap_content,
        attachments_json=attachments_json,
    )
    yield sse_message_saved(assistant_msg.id, "assistant")
    yield sse_iteration_limit(conversation.id, MAX_TOOL_ITERATIONS)
    yield sse_done(conversation.id, usage=resting_usage)
    for _work_fn in _deferred_compaction:
        asyncio.ensure_future(asyncio.to_thread(_work_fn))
    _deferred_compaction.clear()
    _clear_lease(session, conversation.id)
    clear_arm_token_override(conversation.id)
