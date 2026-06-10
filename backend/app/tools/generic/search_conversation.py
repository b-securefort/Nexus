"""search_conversation — recall tool over the CURRENT conversation's full history.

Compaction is an in-context cache eviction: older scaffolding is collapsed to
outcome bullets and long pastes to summaries, but every original message stays
verbatim in the `messages` table. This tool is the recovery path — when the
agent needs an exact value (resource name, ID, error text, a user's earlier
instruction) that has been compacted out of its window or truncated from a tool
result, it searches the durable record instead of guessing or re-asking.

The conversation id comes from the per-request ContextVar set by the
orchestrator (same mechanism as the ARM token / skill slug), so the tool can
never read another conversation.
"""

import json
import logging
import re

from sqlmodel import select

from app.auth.models import User
from app.tools.base import Tool, get_conversation_id

logger = logging.getLogger(__name__)

_SNIPPET_RADIUS = 400          # chars of context kept around the first hit
_MAX_RESULTS_CAP = 10
_DEFAULT_MAX_RESULTS = 5


def _snippet(content: str, term: str) -> str:
    """Window of text around the first case-insensitive hit of `term`."""
    idx = content.lower().find(term.lower())
    if idx < 0:
        idx = 0
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(term) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


class SearchConversationTool(Tool):
    name = "search_conversation"
    description = (
        "Search the FULL stored history of the CURRENT conversation — including "
        "older messages that were compacted/summarized out of your context and "
        "full tool outputs that were truncated before being shown to you. Use "
        "this when the user refers to something from earlier that you can no "
        "longer see, or when you need an exact value (resource name, ID, path, "
        "error text, a number) from a past tool result instead of guessing or "
        "re-asking. Multiple words are ANDed (each must appear in the message). "
        "Returns the most recent matches with a snippet around the hit."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keywords to find (case-insensitive substring match; "
                    "multiple words must all appear in a message)."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": f"Max matches to return (default {_DEFAULT_MAX_RESULTS}, cap {_MAX_RESULTS_CAP}).",
            },
        },
        "required": ["query"],
    }
    requires_approval = False
    result_limit = 24_000

    def execute(self, args: dict, user: User) -> str:
        from app.db.engine import get_session
        from app.db.models import Message

        query = (args.get("query") or "").strip()
        if not query:
            return "Error: query is required"
        conversation_id = get_conversation_id()
        if conversation_id is None:
            return "Error: no active conversation context for search_conversation"

        try:
            max_results = int(args.get("max_results") or _DEFAULT_MAX_RESULTS)
        except (TypeError, ValueError):
            max_results = _DEFAULT_MAX_RESULTS
        max_results = max(1, min(max_results, _MAX_RESULTS_CAP))

        terms = [t for t in re.split(r"\s+", query) if t]

        with get_session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())  # type: ignore[union-attr]
            )
            rows = session.exec(stmt).all()

            matches = []
            for row in rows:
                content = row.content or ""
                if not content:
                    continue
                lowered = content.lower()
                if all(t.lower() in lowered for t in terms):
                    matches.append({
                        "message_id": row.id,
                        "role": row.role,
                        **({"tool": row.tool_name} if row.tool_name else {}),
                        "when": row.created_at.isoformat() if row.created_at else None,
                        "snippet": _snippet(content, terms[0]),
                    })
                    if len(matches) >= max_results:
                        break

        if not matches:
            return json.dumps({
                "results": [],
                "message": (
                    f"No stored message in this conversation contains all of: "
                    f"{', '.join(terms)}. Try fewer or different keywords."
                ),
            }, ensure_ascii=False)

        return json.dumps(
            {"results": matches, "note": "Most recent matches first."},
            ensure_ascii=False, indent=2,
        )
