"""
Azure Resource Graph tool — runs read-only resource graph queries.
Does NOT require approval since Resource Graph is strictly read-only.
"""

import json
import logging

from app.auth.models import User
from app.tools.base import AzureToolBase, check_shell_injection, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)


class AzResourceGraphTool(AzureToolBase):
    name = "az_resource_graph"
    max_output_size = 16384
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

        # Defence-in-depth: block shell metacharacters in the KQL query
        injection_err = check_shell_injection(query, "query")
        if injection_err:
            return injection_err

        subscriptions = args.get("subscriptions", [])

        cmd = [
            _find_az(), "graph", "query",
            "-q", query,
            "--output", "json",
            "--first", "100",
        ]

        if subscriptions:
            cmd.extend(["--subscriptions"] + subscriptions)

        # We use truncate=False because we need to parse the JSON first
        result_str = self._run_az(cmd, label="Resource Graph query", timeout=30, truncate=False)
        
        if result_str.startswith("Error"):
            return result_str

        # Parse and format the output
        try:
            data = json.loads(result_str)
            records = data.get("data", data)
            count = data.get("totalRecords", len(records) if isinstance(records, list) else "unknown")
            output = json.dumps({"totalRecords": count, "data": records}, indent=2)
        except json.JSONDecodeError:
            output = result_str

        if len(output) > self.max_output_size:
            output = output[:self.max_output_size] + "\n... (truncated)"

        return output
