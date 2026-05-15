"""
General web search via DuckDuckGo HTML endpoint.
No API key required. Used as a catch-all for Reddit, Tech Community, blogs, etc.
Supports optional site: scoping (e.g. site:reddit.com).
"""

import json
import logging
import re
import urllib.parse

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
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

        # Resolve site shortcut to domain
        if site:
            domain = SITE_SHORTCUTS.get(site.lower(), site)
            full_query = f"{query} site:{domain}"
        else:
            full_query = query

        try:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = client.post(
                    _DDG_URL,
                    data={"q": full_query, "b": "", "kl": "us-en"},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                resp.raise_for_status()
                html = resp.text

            results = self._parse_results(html, limit)

            if not results:
                return json.dumps(
                    {"results": [], "note": "No results found. Try a broader query."},
                    indent=2,
                )

            return json.dumps({"results": results, "query": full_query}, indent=2)

        except httpx.HTTPStatusError as e:
            logger.warning("DuckDuckGo search error %s", e)
            return f"Error: DuckDuckGo returned {e.response.status_code}"
        except Exception as e:
            logger.warning("Web search error: %s", e)
            return f"Error: {e}"

    def _parse_results(self, html: str, limit: int) -> list[dict]:
        results: list[dict] = []

        # Extract result links — DDG wraps real URLs in a redirect
        # Pattern: <a class="result__a" href="//duckduckgo.com/l/?uddg=ENCODED_URL&...">Title</a>
        title_re = re.compile(
            r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        snippet_re = re.compile(
            r'<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        titles = title_re.findall(html)
        snippets = [
            re.sub(r"<[^>]+>", "", s).strip()
            for s in snippet_re.findall(html)
        ]

        for i, (href, title_html) in enumerate(titles[:limit]):
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            url = self._decode_ddg_url(href)
            snippet = snippets[i] if i < len(snippets) else ""
            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})

        return results

    def _decode_ddg_url(self, href: str) -> str:
        """Extract the real destination URL from a DDG redirect href."""
        # href is like: //duckduckgo.com/l/?uddg=https%3A%2F%2F...&rut=...
        if "uddg=" not in href:
            return href
        try:
            # Prepend scheme if missing
            if href.startswith("//"):
                href = "https:" + href
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get("uddg", [href])[0]
        except Exception:
            return href
