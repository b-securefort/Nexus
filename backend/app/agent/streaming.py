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


def sse_done(conversation_id: int) -> str:
    return sse_event("done", {"conversation_id": conversation_id})


def sse_error(message: str) -> str:
    return sse_event("error", {"message": message})
