"""
Azure REST API tool — direct ARM/management API calls via az rest.
GET requests are read-only (no approval). Mutations require approval.
"""

import json
import logging

from app.auth.models import User
from app.tools.base import AzureToolBase, _find_az
from app.tools.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)

# HTTP methods that are read-only
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AzRestApiTool(AzureToolBase):
    name = "az_rest_api"
    rate_limit_calls = 10
    description = (
        "Call any Azure Resource Manager REST API directly using 'az rest'. "
        "Use this as a last resort when az_resource_graph and az_cli don't support the operation. "
        "GET requests do not require approval; PUT/POST/PATCH/DELETE require approval. "
        "Provide the full API URL or a relative path starting with /subscriptions/."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "PUT", "POST", "PATCH", "DELETE"],
                "description": "HTTP method. GET is read-only, others require approval.",
            },
            "url": {
                "type": "string",
                "description": (
                    "Azure REST API URL. Can be:\n"
                    "- Full: https://management.azure.com/subscriptions/{sub}/...\n"
                    "- Relative: /subscriptions/{sub}/resourceGroups/{rg}/...\n"
                    "Include api-version as a query parameter."
                ),
            },
            "body": {
                "type": "string",
                "description": "JSON request body for PUT/POST/PATCH. Must be valid JSON.",
            },
        },
        "required": ["method", "url"],
    }

    @property
    def requires_approval(self) -> bool:  # type: ignore[override]
        # Dynamic — actual check happens in execute()
        return False

    def _needs_approval(self, method: str) -> bool:
        return method.upper() not in _SAFE_METHODS

    def execute(self, args: dict, user: User) -> str:
        login_err = require_az_login()
        if login_err:
            return login_err

        method = args.get("method", "GET").upper()
        url = args.get("url", "")
        body = args.get("body", "")

        if not url:
            return "Error: url is required"

        # Validate URL doesn't point outside Azure management
        if url.startswith("http") and "management.azure.com" not in url and "graph.microsoft.com" not in url:
            return (
                "Error: URL must be an Azure management API URL "
                "(management.azure.com or graph.microsoft.com) or a relative path."
            )

        # Validate body is valid JSON if provided
        if body:
            try:
                json.loads(body)
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON body: {e}"

        cmd = [_find_az(), "rest", "--method", method, "--url", url, "--output", "json"]

        if body and method not in _SAFE_METHODS:
            cmd.extend(["--body", body])

        result_str = self._run_az(cmd, label=f"{method} {url}", timeout=60)
        return result_str if result_str else f"{method} {url} — success (no response body)"
