"""
Stack Overflow search via the Stack Exchange API.
Free tier: 300 requests/day. Set STACKOVERFLOW_API_KEY for 10,000/day.
"""

import json
import logging

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_API_BASE = "https://api.stackexchange.com/2.3"


class SearchStackOverflowTool(Tool):
    name = "search_stack_overflow"
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
                "description": "Search query, e.g. 'azure private endpoint DNS resolution'",
            },
            "tags": {
                "type": "string",
                "description": "Semicolon-separated tag filter, e.g. 'azure;kubernetes'. Defaults to 'azure'.",
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

        tags = args.get("tags", "azure")
        limit = min(args.get("limit", 5), 10)

        from app.config import get_settings
        settings = get_settings()

        params: dict = {
            "q": query,
            "tagged": tags,
            "site": "stackoverflow",
            "order": "desc",
            "sort": "relevance",
            "pagesize": limit,
        }
        if settings.STACKOVERFLOW_API_KEY:
            params["key"] = settings.STACKOVERFLOW_API_KEY

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(f"{_API_BASE}/search/advanced", params=params)
                resp.raise_for_status()
                data = resp.json()

            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "score": item.get("score", 0),
                    "answer_count": item.get("answer_count", 0),
                    "is_answered": item.get("is_answered", False),
                    "tags": item.get("tags", []),
                }
                for item in data.get("items", [])[:limit]
            ]

            if data.get("quota_remaining") is not None:
                logger.debug("SO API quota remaining: %d", data["quota_remaining"])

            return json.dumps(results, indent=2)

        except httpx.HTTPStatusError as e:
            logger.warning("Stack Overflow API error %s", e)
            return f"Error: Stack Overflow API returned {e.response.status_code}"
        except Exception as e:
            logger.warning("Stack Overflow search error: %s", e)
            return f"Error: {e}"
