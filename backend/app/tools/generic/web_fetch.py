"""
Web fetch tool — fetch and extract content from web pages.
Read-only, no approval needed.
"""

import ipaddress
import logging
import re
import socket

import httpx
import trafilatura

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

_MAX_CONTENT_SIZE = 16384
_TIMEOUT = 15

# Below this many characters of extracted text, a documentation fetch has almost
# certainly failed (JS-rendered shell, auth wall, or near-empty page) rather than
# returned real content.
_MIN_USEFUL_TEXT = 200

# Phrases that mark an extracted page as a JS/auth shell rather than content.
# Seen in production: learn.microsoft.com SPA pages return an "Access to this
# page requires authorization" interstitial to non-browser fetchers.
_EXTRACTION_FAILURE_MARKERS = (
    "access to this page requires authorization",
    "you need to enable javascript",
    "please enable javascript",
    "enable javascript to run this app",
)

# Connection pool for HTTP requests
_shared_client = httpx.Client(timeout=_TIMEOUT, follow_redirects=True, max_redirects=5)

# Only allow HTTPS URLs (and HTTP for localhost)
_ALLOWED_SCHEMES = {"https"}
_LOCALHOST_PATTERNS = {"localhost", "127.0.0.1", "::1"}

# Azure IMDS and other metadata endpoints that must always be blocked
_BLOCKED_IPS = {
    "169.254.169.254",  # Azure IMDS / AWS EC2 metadata
    "metadata.google.internal",  # GCP metadata
}


def _is_private_or_internal_host(hostname: str) -> bool:
    """Resolve hostname and check if ANY resolved IP is private, loopback,
    link-local, or reserved. Fail-closed: returns True on DNS failure.

    This prevents SSRF attacks where the LLM is tricked into fetching
    internal resources, cloud metadata endpoints (IMDS), or private
    network services.
    """
    # Direct check for known metadata hostnames
    if hostname.lower() in _BLOCKED_IPS:
        return True

    try:
        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        # DNS failure — fail closed to prevent DNS rebinding attacks
        logger.warning("SSRF check: DNS resolution failed for %s — blocking", hostname)
        return True

    if not addr_infos:
        return True

    for _, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]

        # Explicit IMDS check (in case it resolves via a hostname alias)
        if ip_str in _BLOCKED_IPS:
            return True

        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return True  # unparseable — fail closed

        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            logger.warning(
                "SSRF check: %s resolved to blocked IP %s", hostname, ip_str,
            )
            return True

    return False


class WebFetchTool(Tool):
    name = "web_fetch"
    config_flag = "TOOL_WEB_FETCH_ENABLED"
    rate_limit_calls = 15
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

        # SSRF prevention: block requests to private/internal/metadata IPs.
        # Allow localhost only for HTTP (dev servers); all other private ranges blocked.
        if host not in _LOCALHOST_PATTERNS and _is_private_or_internal_host(host):
            return (
                "Error: URL resolves to a private, internal, or cloud metadata IP address. "
                "Requests to internal networks and metadata services (e.g. 169.254.169.254) "
                "are blocked for security."
            )

        # Microsoft Learn is a JavaScript-rendered SPA: a static fetch only gets
        # back an auth/JS shell ("Access to this page requires authorization"),
        # never the doc body. fetch_ms_docs queries the Learn API and returns the
        # real content, so route the agent there instead of wasting the fetch.
        if host == "learn.microsoft.com" or host.endswith(".learn.microsoft.com"):
            return (
                "Error: learn.microsoft.com pages are JavaScript-rendered, so web_fetch "
                "cannot extract their content (it only sees an authorization/JS shell). "
                "Use `fetch_ms_docs` to search Microsoft Learn for this topic instead."
            )
        try:
            response = _shared_client.get(
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
                # Surface a content-level failure instead of returning a JS/auth
                # shell as success. Without this the agent (and the orchestrator's
                # is_error check) treats a useless stub as a real doc and never
                # retries or learns to pivot to a better tool.
                reason = self._extraction_failed(content)
                if reason:
                    return (
                        f"Error: fetched {url} (HTTP {response.status_code}) but could not "
                        f"extract usable content ({reason}). The page is likely "
                        "JavaScript-rendered or behind an authorization wall — try a "
                        "different source, or fetch_ms_docs for Microsoft Learn topics."
                    )

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

    def _extraction_failed(self, content: str) -> str | None:
        """Return a short reason if the extracted text is a JS/auth shell or
        otherwise unusable, else None. Only applied in 'text' mode."""
        stripped = (content or "").strip()
        if not stripped:
            return "empty after extraction"
        low = stripped.lower()
        for marker in _EXTRACTION_FAILURE_MARKERS:
            if marker in low:
                return f"content wall: {marker!r}"
        if len(stripped) < _MIN_USEFUL_TEXT:
            return f"only {len(stripped)} chars extracted"
        return None

    def _extract_text(self, html: str) -> str:
        """Extract readable text from HTML.

        Tries trafilatura first — it drops nav/header/footer/banner chrome
        that regex tag-stripping can't distinguish from real content. Falls
        back to the legacy regex stripper if trafilatura returns nothing
        (some pages are too JS-heavy or non-article for its heuristics).
        """
        try:
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            if extracted and extracted.strip():
                return extracted
        except Exception as e:
            logger.warning("trafilatura extract failed, falling back to regex: %s", e)
        return self._extract_text_fallback(html)

    def _extract_text_fallback(self, html: str) -> str:
        """Regex tag-stripper. Last resort when trafilatura can't extract."""
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&quot;', '"')
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\. ', '.\n', text)
        return text
