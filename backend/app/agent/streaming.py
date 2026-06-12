"""
SSE (Server-Sent Events) emission helpers.
"""

import json
from typing import AsyncGenerator


def sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def sse_token(text: str) -> str:
    return sse_event("token", {"text": text})


def sse_tool_call_start(call_id: str, name: str, args: dict) -> str:
    return sse_event("tool_call_start", {"call_id": call_id, "name": name, "args": args})


def sse_approval_required(
    approval_id: str,
    tool_name: str,
    args: dict,
    reason: str,
    risk_level: str | None = None,
    risk_description: str | None = None,
    rendered_command: str | None = None,
    command_truncated: bool = False,
) -> str:
    """Approval card payload.

    Emitted twice per approval (§5 2026-06-04): first with
    `risk_level="pending"` so the card renders immediately, then again with the
    resolved advisory verdict. Same `approval_id` both times — the frontend
    updates the existing card in place rather than stacking a second one.

    `rendered_command` is the deterministic, LLM-free resolved command shown to
    the human (the script/`body_file` content inlined, up to 64 KB); when the
    full command exceeds that, `command_truncated` is True and the card offers a
    download via GET /api/approvals/{id}/command (§5 2026-06-12). Both ride on the
    same value across the two emits — they don't depend on the risk verdict.
    """
    return sse_event(
        "approval_required",
        {
            "approval_id": approval_id,
            "tool_name": tool_name,
            "args": args,
            "reason": reason,
            "risk_level": risk_level,
            "risk_description": risk_description,
            "rendered_command": rendered_command,
            "command_truncated": command_truncated,
        },
    )


def sse_question_required(
    question_id: str, call_id: str, questions: list[dict]
) -> str:
    """Signal the frontend that the agent is waiting for the user's answer.

    `questions` is the validated list passed to ask_user (1-4 entries, each
    with question/header/options/multi_select). The frontend renders an
    adaptive-card-style form and POSTs the selections to resolve the wait.
    """
    return sse_event(
        "question_required",
        {"question_id": question_id, "call_id": call_id, "questions": questions},
    )


def sse_question_answered(question_id: str, call_id: str, answers: list[dict]) -> str:
    """Optional companion event so live UIs can reflect the resolved answer
    (or expiry) for the live card without re-fetching state."""
    return sse_event(
        "question_answered",
        {"question_id": question_id, "call_id": call_id, "answers": answers},
    )


def sse_tool_result(call_id: str, name: str, content: str) -> str:
    return sse_event("tool_result", {"call_id": call_id, "name": name, "content": content})


def sse_tool_executing(call_id: str, name: str) -> str:
    """Signal that a tool has been approved and execution has started."""
    return sse_event("tool_executing", {"call_id": call_id, "name": name})


def sse_tool_output_chunk(call_id: str, chunk: str) -> str:
    """Stream a chunk of tool output as it becomes available."""
    return sse_event("tool_output_chunk", {"call_id": call_id, "chunk": chunk})


def sse_message_saved(message_id: int, role: str) -> str:
    return sse_event("message_saved", {"message_id": message_id, "role": role})


def sse_done(conversation_id: int, usage: dict | None = None) -> str:
    """End-of-turn event.

    `usage` (optional) carries the last LLM call's token accounting so the
    frontend can render a context-window *occupancy* indicator (how full the
    window is now — not cumulative spend). Shape:
        {
          "prompt_tokens": int,        # authoritative window occupancy (headline)
          "completion_tokens": int,    # output — NOT counted in the gauge headline
          "cached_tokens": int,        # subset of prompt_tokens
          "context_window": int,       # model-derived total window
          "model": str,                # deployment name
          "segments": [                # input-side breakdown, sums to prompt_tokens
            {"label": str, "tokens": int}, ...
          ],
        }
    Omitted by the resume endpoint (no fresh LLM call happened).
    """
    payload: dict = {"conversation_id": conversation_id}
    if usage is not None:
        payload["usage"] = usage
    return sse_event("done", payload)


def sse_error(message: str) -> str:
    return sse_event("error", {"message": message})


def sse_iteration_limit(conversation_id: int, max_iterations: int) -> str:
    """The turn used its whole tool-iteration budget and ended with a wrap-up
    summary instead of completing naturally. Emitted BEFORE `done` so the UI
    can offer a one-click "continue" affordance — a follow-up user message of
    "continue" resumes from the persisted history. Not an error: the turn's
    tool results and final summary are all saved."""
    return sse_event(
        "iteration_limit",
        {"conversation_id": conversation_id, "max_iterations": max_iterations},
    )


def sse_token_refresh_required(
    *, conversation_id: int, tool_name: str, status: str
) -> str:
    """Signal the frontend that the user's ARM token has expired (or is about
    to) and the next Azure tool call cannot proceed. The frontend listens for
    this, calls MSAL silently to acquire a fresh token, and POSTs it back via
    `/api/chat/refresh-token` so the next turn picks it up.

    `status` is one of "missing" | "expired" | "near_expiry" so the UI can
    word the prompt appropriately ("please sign in" vs "refreshing token…").
    """
    return sse_event(
        "token_refresh_required",
        {
            "conversation_id": conversation_id,
            "tool_name": tool_name,
            "status": status,
        },
    )
