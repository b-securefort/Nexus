"""
Microsoft Docs search tool.
"""

import json
import logging

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)


class FetchMsDocsTool(Tool):
    name = "fetch_ms_docs"
    description = (
        "Search Microsoft Learn documentation. Returns top 5 results with title, URL, and description. "
        "Use web_fetch on a returned URL to read the full content of a specific article."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for Microsoft Learn documentation",
            }
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        query = args.get("query", "").strip()
        if not query:
            return "Error: query is required"

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    "https://learn.microsoft.com/api/search",
                    params={"search": query, "locale": "en-us", "$top": 5},
                )
                resp.raise_for_status()
                data = resp.json()

            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in data.get("results", [])[:5]
            ]

            return json.dumps(results, indent=2)

        except httpx.HTTPStatusError as e:
            logger.warning("MS Docs API error: %s", str(e))
            return f"Error: MS Docs API returned {e.response.status_code}"
        except Exception as e:
            logger.warning("MS Docs fetch error: %s", str(e))
            return f"Error: {str(e)}"
