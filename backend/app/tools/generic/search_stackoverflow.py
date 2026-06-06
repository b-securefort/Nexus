"""
Stack Overflow search via the Stack Exchange API.
Free tier: 300 requests/day. Set STACKOVERFLOW_API_KEY for 10,000/day.
"""

import json
import logging

import httpx

from app.auth.models import User
from app.tools.base import Tool
from app.tools.generic.search_relax import relaxed_queries

logger = logging.getLogger(__name__)

_API_BASE = "https://api.stackexchange.com/2.3"


class SearchStackOverflowTool(Tool):
    name = "search_stack_overflow"
    # FIX (DESIGN.md §5 2026-06-05): the old init_tools config_mapping keyed this
    # under "search_stackoverflow" — not the registered name "search_stack_overflow"
    # — so TOOL_SEARCH_STACKOVERFLOW_ENABLED was a dead flag (tool always on).
    config_flag = "TOOL_SEARCH_STACKOVERFLOW_ENABLED"
    description = (
        "Search Stack Overflow for technical questions and answers. "
        "Returns score, accepted-answer status, and answer count — "
        "high-score accepted answers are the most reliable. "
        "Use web_fetch on a result URL to read the full accepted answer."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Use 2-3 broad keywords, not a sentence — the "
                    "Stack Exchange API matches ALL terms, so long "
                    "natural-language phrases often return nothing. E.g. "
                    "'private endpoint DNS resolution'."
                ),
            },
            "tags": {
                "type": "string",
                "description": (
                    "Optional semicolon-separated tag filter, e.g. "
                    "'azure;kubernetes'. No tag filter is applied by default — "
                    "pass a tag only to narrow a broad query."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5, max 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        query = args.get("query", "").strip()
        if not query:
            return "Error: query is required"

        # No forced tag filter: this is a generic tool, not Azure-specific
        # (B3 / bundle decoupling). Callers narrow with `tags` only when needed.
        tags = args.get("tags", "")
        limit = min(args.get("limit", 5), 10)

        from app.config import get_settings
        settings = get_settings()

        base_params: dict = {
            "site": "stackoverflow",
            "order": "desc",
            "sort": "relevance",
            "pagesize": limit,
        }
        if tags:
            base_params["tagged"] = tags
        if settings.STACKOVERFLOW_API_KEY:
            base_params["key"] = settings.STACKOVERFLOW_API_KEY

        try:
            with httpx.Client(timeout=15) as client:
                # The Stack Exchange API ANDs every term, so verbose queries can
                # match nothing; on zero results, retry with shorter prefixes
                # before giving up (B3).
                items: list = []
                for candidate in relaxed_queries(query):
                    resp = client.get(
                        f"{_API_BASE}/search/advanced",
                        params={**base_params, "q": candidate},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("quota_remaining") is not None:
                        logger.debug("SO API quota remaining: %d", data["quota_remaining"])
                    items = data.get("items", [])
                    if items:
                        if candidate != query:
                            logger.info("search_stack_overflow relaxed %r -> %r", query, candidate)
                        break

            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "score": item.get("score", 0),
                    "answer_count": item.get("answer_count", 0),
                    "is_answered": item.get("is_answered", False),
                    "tags": item.get("tags", []),
                }
                for item in items[:limit]
            ]

            return json.dumps(results, indent=2)

        except httpx.HTTPStatusError as e:
            logger.warning("Stack Overflow API error %s", e)
            return f"Error: Stack Overflow API returned {e.response.status_code}"
        except Exception as e:
            logger.warning("Stack Overflow search error: %s", e)
            return f"Error: {e}"
