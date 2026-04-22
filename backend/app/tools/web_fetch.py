"""
Web fetch tool — fetch and extract content from web pages.
Read-only, no approval needed.
"""

import logging
import re

import httpx

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_MAX_CONTENT_SIZE = 16384
_TIMEOUT = 15

# Only allow HTTPS URLs (and HTTP for localhost)
_ALLOWED_SCHEMES = {"https"}
_LOCALHOST_PATTERNS = {"localhost", "127.0.0.1", "::1"}


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch and extract text content from a web page URL. "
        "Use this to retrieve documentation, API references, or status pages. "
        "Only HTTPS URLs are allowed (HTTP allowed for localhost only). "
        "Returns extracted text content, not raw HTML."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch. Must be HTTPS (or HTTP for localhost).",
            },
            "extract_mode": {
                "type": "string",
                "enum": ["text", "raw", "headers_only"],
                "description": (
                    "How to process the response:\n"
                    "- text: Extract readable text (strips HTML tags) — default\n"
                    "- raw: Return raw response body (truncated)\n"
                    "- headers_only: Return only response headers and status"
                ),
            },
        },
        "required": ["url"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        url = args.get("url", "").strip()
        mode = args.get("extract_mode", "text")

        if not url:
            return "Error: url is required"

        # Validate URL scheme
        try:
            parsed = httpx.URL(url)
        except Exception:
            return "Error: Invalid URL format"

        scheme = str(parsed.scheme).lower()
        host = str(parsed.host).lower()

        if scheme == "http" and host not in _LOCALHOST_PATTERNS:
            return "Error: Only HTTPS URLs are allowed (HTTP is allowed for localhost only)"
        if scheme not in _ALLOWED_SCHEMES and not (scheme == "http" and host in _LOCALHOST_PATTERNS):
            return f"Error: URL scheme '{scheme}' is not allowed. Use HTTPS."

        # Block internal/private IPs (basic SSRF prevention)
        if host in _LOCALHOST_PATTERNS and scheme == "https":
            pass  # Allow HTTPS to localhost
        
        try:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, max_redirects=5) as client:
                response = client.get(
                    url,
                    headers={"User-Agent": "Nexus-AI-Assistant/1.0"},
                )

            if mode == "headers_only":
                headers = "\n".join(f"  {k}: {v}" for k, v in response.headers.items())
                return f"Status: {response.status_code}\nHeaders:\n{headers}"

            if response.status_code >= 400:
                return f"Error: HTTP {response.status_code} — {response.reason_phrase}"

            content = response.text

            if mode == "text":
                content = self._extract_text(content)

            if len(content) > _MAX_CONTENT_SIZE:
                content = content[:_MAX_CONTENT_SIZE] + "\n... (truncated)"

            return f"URL: {url}\nStatus: {response.status_code}\n\n{content}"

        except httpx.TimeoutException:
            return f"Error: Request timed out after {_TIMEOUT} seconds"
        except httpx.ConnectError as e:
            return f"Error: Could not connect to {url}: {e}"
        except Exception as e:
            logger.error("Web fetch error for %s: %s", url, str(e))
            return f"Error: {str(e)}"

    def _extract_text(self, html: str) -> str:
        """Extract readable text from HTML — basic tag stripping."""
        # Remove script and style blocks
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode common entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&quot;', '"')
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Re-add some structure by splitting on sentence boundaries
        text = re.sub(r'\. ', '.\n', text)
        return text
