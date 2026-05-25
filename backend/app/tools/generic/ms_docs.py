"""
Microsoft Docs search tool.
"""

import json
import logging
import re

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Strip `site:...` operators from the query before sending to MS Learn API.
# Agents sometimes include `site:learn.microsoft.com` thinking it helps; it
# actually pollutes the index's own relevance ranking (the API only searches
# learn.microsoft.com anyway).
_SITE_OPERATOR_RE = re.compile(r"\bsite:\S+\s*", re.IGNORECASE)


def _is_landing_page(url: str) -> bool:
    """A URL is landing-style if it has <=2 path segments after the locale.

    Examples:
      /en-us/azure/                       -> 2 segments (landing)
      /en-us/azure/architecture/          -> 2 segments (Architecture Center landing)
      /en-us/intune/                      -> 1 segment  (landing)
      /en-us/azure/storage/blobs/intro    -> 4 segments (article)
    """
    m = re.search(r"learn\.microsoft\.com/[a-z]{2}-[a-z]{2}/(.*)$", url, re.IGNORECASE)
    if not m:
        return False
    tail = m.group(1).strip("/")
    if not tail:
        return True
    segments = tail.split("/")
    return len(segments) <= 2


class FetchMsDocsTool(Tool):
    name = "fetch_ms_docs"
    description = (
        "Search Microsoft Learn documentation. Returns top 5 results with title, URL, and description. "
        "Article-level results are ranked above landing/hub pages. "
        "Use web_fetch on a returned URL to read the full content of a specific article."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query for Microsoft Learn documentation. "
                    "Do not prefix with 'site:learn.microsoft.com' — the API only indexes Learn already."
                ),
            }
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        raw_query = args.get("query", "").strip()
        if not raw_query:
            return "Error: query is required"

        cleaned_query = _SITE_OPERATOR_RE.sub("", raw_query).strip()
        if not cleaned_query:
            cleaned_query = raw_query  # all-site-operator query — keep original

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    "https://learn.microsoft.com/api/search",
                    params={
                        "search": cleaned_query,
                        "locale": "en-us",
                        "$top": 15,
                        "scope": "Azure",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            raw_items = data.get("results", [])

            articles: list[dict] = []
            landings: list[dict] = []
            for item in raw_items:
                entry = {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                if _is_landing_page(entry["url"]):
                    landings.append(entry)
                else:
                    articles.append(entry)

            ranked = (articles + landings)[:5]
            return json.dumps(ranked, indent=2)

        except httpx.HTTPStatusError as e:
            logger.warning("MS Docs API error: %s", str(e))
            return f"Error: MS Docs API returned {e.response.status_code}"
        except Exception as e:
            logger.warning("MS Docs fetch error: %s", str(e))
            return f"Error: {str(e)}"
