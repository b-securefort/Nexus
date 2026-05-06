"""
Azure Updates search via the official Release Communications API.

The API powering azure.microsoft.com/en-us/updates is publicly accessible at:
  https://www.microsoft.com/releasecommunications/api/v2/azure

It returns OData JSON with full update metadata including:
  status       — "Launched" (GA), "In Development" (preview), "Retired" (deprecated)
  tags         — ["Retirement", "Features", ...] etc.
  products     — specific Azure service names
  productCategories — broad categories (Networking, Storage, Security, ...)
  availabilities — ring ("General Availability", "Public Preview"), year, month
  description  — full HTML body of the announcement
  title, created, modified, id (used to build the URL)

No API key required.
"""

import json
import logging
import re

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_API_BASE = "https://www.microsoft.com/releasecommunications/api/v2/azure"
_ITEM_URL_BASE = "https://azure.microsoft.com/en-us/updates"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}

# Tokens that signal a retirement/deprecation intent
_RETIREMENT_TOKENS = {
    "deprecated", "deprecation", "deprecate", "deprecating",
    "retired", "retirement", "retire", "retiring",
    "decommission", "decommissioned", "eol", "end-of-life", "sunset",
}

# Tokens that signal a GA / release intent
_GA_TOKENS = {"ga", "launched", "launch", "released", "stable", "production"}

# Stopwords that carry no search signal in this corpus
_STOPWORDS = {
    "the", "is", "in", "at", "on", "a", "an", "and", "or", "for",
    "to", "of", "with", "latest", "recent", "new", "feature",
    "announcement", "what", "which", "when", "did", "has", "have",
    "been", "azure", "microsoft",
    # Too generic in an Azure-specific feed
    "service", "services", "cloud", "platform", "update", "updates",
    "support", "product", "products",
}


class SearchAzureUpdatesTool(Tool):
    name = "search_azure_updates"
    description = (
        "Search the official Azure Updates API (azure.microsoft.com/en-us/updates) "
        "for GA releases, previews, retirements, and service announcements. "
        "Supports filtering by service name (e.g. 'AKS', 'storage', 'Firewall'), "
        "status ('GA', 'retired', 'preview'), and free-text keywords. "
        "This is the authoritative Microsoft source — not a blog feed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keywords, service name, or intent. Examples: "
                    "'AKS GA', 'storage retirement', 'Firewall preview', "
                    "'latest updates', 'deprecated networking services'."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5, max 20)",
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

        limit = min(args.get("limit", 5), 20)
        raw_tokens = [t.lower() for t in query.split() if len(t) > 1]
        tokens = [t for t in raw_tokens if t not in _STOPWORDS]

        is_retirement = any(t in _RETIREMENT_TOKENS for t in raw_tokens)
        is_ga = any(t in _GA_TOKENS for t in raw_tokens)

        try:
            items = self._fetch_items(is_retirement=is_retirement, is_ga=is_ga)
        except Exception as e:
            logger.warning("Azure Updates API error: %s", e)
            return f"Error fetching Azure Updates: {e}"

        if not items:
            return json.dumps({"results": [], "note": "No items returned from Azure Updates API."}, indent=2)

        # Remove status tokens from search terms — they were used for API filtering
        search_tokens = [t for t in tokens if t not in _RETIREMENT_TOKENS and t not in _GA_TOKENS]

        if not search_tokens:
            # No content-specific tokens — return most recent items
            return json.dumps(self._format(items[:limit]), indent=2)

        # Score by token matches across title, description, products, categories
        scored = []
        for item in items:
            searchable = self._searchable_text(item)
            score = sum(3 if t in item.get("title", "").lower() else 1
                        for t in search_tokens if t in searchable)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:limit]]

        if not results:
            return json.dumps(
                {
                    "results": self._format(items[:limit]),
                    "note": f"No items matched '{query}'. Showing most recent instead.",
                },
                indent=2,
            )

        return json.dumps(self._format(results), indent=2)

    # ------------------------------------------------------------------
    # API fetcher
    # ------------------------------------------------------------------

    def _fetch_items(self, is_retirement: bool, is_ga: bool) -> list[dict]:
        """
        Fetch items from the Release Communications OData API, paginating up to 200 items.

        Server-side $filter is not reliably supported — we fetch all and filter client-side.
        Real status values from the API:
          "Launched"       — GA
          "In preview"     — public/private preview
          "In development" — announced but not yet available
          None             — retirement items (identified by tags=["Retirements"])
        """
        all_items: list[dict] = []
        url: str | None = _API_BASE

        with httpx.Client(timeout=20, follow_redirects=True) as client:
            while url and len(all_items) < 200:
                resp = client.get(url, headers=_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                page = data.get("value", [])
                all_items.extend(page)
                url = data.get("@odata.nextLink")
                logger.debug("Fetched %d items, total so far %d", len(page), len(all_items))

        # Sort newest-first by modified date (API default order is not guaranteed)
        all_items.sort(key=lambda i: i.get("modified") or i.get("created") or "", reverse=True)

        # Client-side status filtering
        if is_retirement and not is_ga:
            return [i for i in all_items if "Retirements" in i.get("tags", [])]
        if is_ga and not is_retirement:
            return [i for i in all_items if i.get("status") == "Launched"]

        return all_items

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _searchable_text(self, item: dict) -> str:
        parts = [
            item.get("title", ""),
            " ".join(item.get("products", [])),
            " ".join(item.get("productCategories", [])),
            " ".join(item.get("tags", [])),
            _strip_html(item.get("description", ""))[:400],
        ]
        return " ".join(parts).lower()

    def _format(self, items: list[dict]) -> list[dict]:
        """Shape raw API items into clean result dicts."""
        results = []
        for item in items:
            slug = item.get("id", "")
            url = f"{_ITEM_URL_BASE}/{slug}/" if slug else ""

            # Pick the most informative availability ring
            rings = [a.get("ring", "") for a in item.get("availabilities", [])]
            ring = rings[0] if rings else item.get("status", "")

            results.append({
                "title": item.get("title", ""),
                "url": url,
                "status": item.get("status", ""),
                "ring": ring,
                "products": item.get("products", [])[:5],
                "categories": item.get("productCategories", [])[:3],
                "published": item.get("modified", item.get("created", ""))[:10],
                "summary": _strip_html(item.get("description", ""))[:250],
            })
        return results


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
