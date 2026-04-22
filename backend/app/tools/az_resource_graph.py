"""
Azure Resource Graph tool — runs read-only resource graph queries.
Does NOT require approval since Resource Graph is strictly read-only.
"""

import json
import logging
import shutil
import subprocess
import sys

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 16384


def _find_az() -> str:
    """Resolve the full path to az CLI. On Windows it's az.cmd."""
    path = shutil.which("az")
    if path:
        return path
    if sys.platform == "win32":
        path = shutil.which("az.cmd")
        if path:
            return path
    return "az"


class AzResourceGraphTool(Tool):
    name = "az_resource_graph"
    description = (
        "Execute a read-only Azure Resource Graph (ARG) query using Kusto Query Language (KQL). "
        "Use this to explore, count, or list Azure resources across subscriptions. "
        "Examples: count VMs, list storage accounts, find resources by tag, check RBAC assignments. "
        "This is read-only and does NOT require user approval.\n\n"
        "IMPORTANT KQL syntax rules for Resource Graph:\n"
        "- Do NOT use 'let' variables or 'datatable()' — they cause ParserFailure.\n"
        "- For ID filtering, use inline literals: where id in~ ('id1','id2',...)\n"
        "- For subscriptions/RGs, query 'ResourceContainers' (not 'Resources').\n"
        "- Use 'isnotempty(resourceGroup)' to filter out subscription-level resources.\n"
        "- Use tostring() for nested properties: tostring(properties.encryption.services.blob.enabled)\n"
        "- 'dynamic()' is not supported — use literal values only."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "KQL query for Azure Resource Graph. Examples:\n"
                    "- 'Resources | summarize count() by type | order by count_ desc'\n"
                    "- 'ResourceContainers | where type == \"microsoft.resources/subscriptions\"'\n"
                    "- 'Resources | where type =~ \"microsoft.compute/virtualmachines\" | project name, resourceGroup, location'\n"
                    "- 'Resources | where isnotempty(resourceGroup) | summarize count() by resourceGroup'"
                ),
            },
            "subscriptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of subscription IDs to scope the query. If empty, queries all accessible subscriptions.",
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        # Pre-check Azure login state
        login_err = require_az_login()
        if login_err:
            return login_err

        query = args.get("query", "")
        if not query:
            return "Error: query is required"

        subscriptions = args.get("subscriptions", [])

        cmd = [
            _find_az(), "graph", "query",
            "-q", query,
            "--output", "json",
            "--first", "100",
        ]

        if subscriptions:
            cmd.extend(["--subscriptions"] + subscriptions)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error (exit {result.returncode}): {error_msg}"

            # Parse and format the output
            try:
                data = json.loads(result.stdout)
                records = data.get("data", data)
                count = data.get("totalRecords", len(records) if isinstance(records, list) else "unknown")
                output = json.dumps({"totalRecords": count, "data": records}, indent=2)
            except json.JSONDecodeError:
                output = result.stdout

            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return output

        except subprocess.TimeoutExpired:
            return "Error: Resource Graph query timed out after 30 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found. Is it installed? Run 'az extension add --name resource-graph' if the extension is missing."
        except Exception as e:
            logger.error("Resource Graph error: %s", str(e))
            return f"Error: {str(e)}"
