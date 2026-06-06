"""
Azure Resource Graph tool — runs read-only resource graph queries.
Does NOT require approval since Resource Graph is strictly read-only.
"""

import json
import logging

from app.auth.models import User
from app.tools.base import check_shell_injection
from bundles.azure._az_base import AzureToolBase, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)


class AzResourceGraphTool(AzureToolBase):
    name = "az_resource_graph"
    config_flag = "TOOL_AZ_CLI_ENABLED"   # shares the az_cli toggle
    retry_eligible = True       # was orchestrator _COMMAND_TOOLS
    learning_eligible = True    # was orchestrator _LEARNING_ELIGIBLE_TOOLS
    result_limit = 4_000        # was orchestrator _TOOL_RESULT_LIMITS
    max_output_size = 16384

    @staticmethod
    def _trim_arg_error(error_str: str) -> str:
        """Strip the Azure CLI support boilerplate from a Resource Graph error
        (B10). `az graph query` appends a 'Please provide below info when asking
        for support: timestamp/correlationId' block on bad queries — noise the
        model can't act on. Keep the actionable InvalidQuery/ParserFailure detail
        (everything before the boilerplate marker)."""
        for marker in ("Please provide below info", "\ntimestamp:", "\ncorrelationId:"):
            idx = error_str.find(marker)
            if idx != -1:
                error_str = error_str[:idx]
        return error_str.rstrip()

    def retry_docs_query(self, func_args: dict, error_text: str) -> str | None:
        return f"Azure Resource Graph KQL query syntax {func_args.get('query', '')[:80]}"

    def retry_alt_hint(self) -> str | None:
        return (
            "Try `az_cli` with `az resource list` or similar commands. If that "
            "also fails, use `az_rest_api` to call the Azure REST API directly."
        )

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
            return self._trim_arg_error(result_str)

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
