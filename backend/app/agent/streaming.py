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


def sse_approval_required(approval_id: str, tool_name: str, args: dict, reason: str) -> str:
    return sse_event(
        "approval_required",
        {"approval_id": approval_id, "tool_name": tool_name, "args": args, "reason": reason},
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
    frontend can render a context-window indicator. Shape:
        {
          "prompt_tokens": int,
          "completion_tokens": int,
          "cached_tokens": int,        # subset of prompt_tokens
          "context_window": int,       # model's total window
          "model": str,                # deployment name
        }
    Omitted by the resume endpoint (no fresh LLM call happened).
    """
    payload: dict = {"conversation_id": conversation_id}
    if usage is not None:
        payload["usage"] = usage
    return sse_event("done", payload)


def sse_error(message: str) -> str:
    return sse_event("error", {"message": message})
