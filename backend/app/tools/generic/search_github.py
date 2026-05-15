"""
GitHub search via the GitHub REST API.
Unauthenticated: 10 req/min. Set GITHUB_TOKEN for 30 req/min.
"""

import json
import logging

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"


class SearchGithubTool(Tool):
    name = "search_github"
    description = (
        "Search GitHub for repositories and code. "
        "Useful for finding IaC templates (Bicep, Terraform, ARM), Azure SDK samples, "
        "and reference implementations. Returns star count and description."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'azure landing zone bicep' or 'aks private cluster terraform'",
            },
            "search_type": {
                "type": "string",
                "enum": ["repositories", "code"],
                "description": "Search repositories (default) or code files within repos.",
                "default": "repositories",
            },
            "language": {
                "type": "string",
                "description": "Filter by programming/config language, e.g. 'Bicep', 'HCL', 'Python'",
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

        search_type = args.get("search_type", "repositories")
        language = args.get("language", "")
        limit = min(args.get("limit", 5), 10)

        q = query
        if language:
            q += f" language:{language}"

        from app.config import get_settings
        settings = get_settings()

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

        params: dict = {"q": q, "per_page": limit}
        if search_type == "repositories":
            params["sort"] = "stars"
            params["order"] = "desc"

        try:
            with httpx.Client(timeout=15, headers=headers) as client:
                resp = client.get(
                    f"{_API_BASE}/search/{search_type}",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])[:limit]

            if search_type == "repositories":
                results = [
                    {
                        "name": item.get("full_name", ""),
                        "url": item.get("html_url", ""),
                        "description": item.get("description", ""),
                        "stars": item.get("stargazers_count", 0),
                        "language": item.get("language", ""),
                        "topics": item.get("topics", []),
                    }
                    for item in items
                ]
            else:
                results = [
                    {
                        "name": item.get("name", ""),
                        "url": item.get("html_url", ""),
                        "repository": item.get("repository", {}).get("full_name", ""),
                        "path": item.get("path", ""),
                    }
                    for item in items
                ]

            return json.dumps(results, indent=2)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env for higher limits."
            logger.warning("GitHub API error %s", e)
            return f"Error: GitHub API returned {e.response.status_code}"
        except Exception as e:
            logger.warning("GitHub search error: %s", e)
            return f"Error: {e}"
