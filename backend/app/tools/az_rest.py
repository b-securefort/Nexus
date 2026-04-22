"""
Azure REST API tool — direct ARM/management API calls via az rest.
GET requests are read-only (no approval). Mutations require approval.
"""

import json
import logging
import subprocess
import sys

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login
from app.tools.az_cli import _find_az

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 16384

# HTTP methods that are read-only
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AzRestApiTool(Tool):
    name = "az_rest_api"
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

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error ({method} {url}): {error}"

            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return output if output else f"{method} {url} — success (no response body)"

        except subprocess.TimeoutExpired:
            return f"Error: {method} {url} timed out after 60 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found"
        except Exception as e:
            logger.error("az rest error: %s", str(e))
            return f"Error: {str(e)}"
