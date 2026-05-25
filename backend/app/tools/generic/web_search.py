"""
General web search via DuckDuckGo (using the `ddgs` library).
No API key required. Used as a catch-all for Reddit, Tech Community, blogs, etc.
Supports optional site: scoping (e.g. site:reddit.com).
"""

import json
import logging
import re

from ddgs import DDGS

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_MAX_RESULTS = 10

# Well-known site shortcuts the agent can use by name
SITE_SHORTCUTS: dict[str, str] = {
    "reddit": "reddit.com",
    "techcommunity": "techcommunity.microsoft.com",
    "stackoverflow": "stackoverflow.com",
    "github": "github.com",
    "mslearn": "learn.microsoft.com",
    "azureblog": "azure.microsoft.com/blog",
    "devblog": "devblogs.microsoft.com",
}


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "General web search via DuckDuckGo. Use this to search Reddit, Microsoft Tech Community, "
        "Azure blogs, or any site not covered by the other search tools. "
        "Tip: set 'site' to a shortcut like 'reddit', 'techcommunity', or 'azureblog' "
        "to scope results to that site."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'azure front door vs application gateway reddit'",
            },
            "site": {
                "type": "string",
                "description": (
                    "Optional site scope. Use a shortcut (reddit, techcommunity, azureblog, devblog) "
                    "or any domain (e.g. 'techcommunity.microsoft.com')."
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

        site = args.get("site", "").strip()
        limit = min(args.get("limit", 5), _MAX_RESULTS)

        # If the agent already embedded `site:` in the query, do not append
        # another one — two `site:` filters return zero hits.
        query_has_site = bool(re.search(r"\bsite:\S+", query, re.IGNORECASE))

        if site and query_has_site:
            logger.info(
                "web_search: ignoring site=%r because query already contains a site: operator",
                site,
            )
            full_query = query
        elif site:
            domain = SITE_SHORTCUTS.get(site.lower(), site)
            # site: filters by domain only — path segments are ignored. If the
            # caller passed `azure.microsoft.com/blog`, split the path off and
            # treat it as an extra keyword so we still bias toward that section.
            if "/" in domain:
                bare_domain, _, path_tail = domain.partition("/")
                path_keyword = path_tail.replace("/", " ").strip()
                full_query = (
                    f"{query} {path_keyword} site:{bare_domain}"
                    if path_keyword
                    else f"{query} site:{bare_domain}"
                )
            else:
                full_query = f"{query} site:{domain}"
        else:
            full_query = query

        try:
            with DDGS(timeout=_TIMEOUT) as ddgs:
                raw = list(ddgs.text(full_query, max_results=limit))
        except Exception as e:
            logger.warning("Web search error: %s", e)
            return f"Error: {e}"

        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", "") or r.get("url", ""),
                "snippet": r.get("body", "") or r.get("snippet", ""),
            }
            for r in raw
            if r.get("title") and (r.get("href") or r.get("url"))
        ]

        if not results:
            return json.dumps(
                {"results": [], "note": "No results found. Try a broader query."},
                indent=2,
            )

        return json.dumps({"results": results, "query": full_query}, indent=2)
