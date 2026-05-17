"""
Azure Monitor Log Analytics tool — KQL queries against Log Analytics workspaces.
Read-only, no approval needed.
"""

import json
import logging

from app.auth.models import User
from app.tools.base import AzureToolBase, check_shell_injection, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)


class AzMonitorLogsTool(AzureToolBase):
    name = "az_monitor_logs"
    max_output_size = 16384
    description = (
        "Query Azure Monitor Log Analytics workspaces using KQL (Kusto Query Language). "
        "Read-only — no approval needed. Use this for querying application logs, "
        "performance metrics, security events, and resource diagnostics. "
        "If workspace_id is not provided, the tool will auto-discover the first "
        "available workspace in the current subscription."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "KQL query to run against Log Analytics. Examples:\n"
                    "- AzureActivity | summarize count() by OperationNameValue | top 10 by count_\n"
                    "- Heartbeat | summarize LastHeartbeat = max(TimeGenerated) by Computer\n"
                    "- AzureMetrics | where TimeGenerated > ago(1h)"
                ),
            },
            "workspace_id": {
                "type": "string",
                "description": (
                    "Log Analytics workspace ID (GUID). If omitted, "
                    "the tool auto-discovers the first workspace in the subscription."
                ),
            },
            "timespan": {
                "type": "string",
                "description": (
                    "ISO 8601 duration for the query time range. Default: PT24H (last 24h). "
                    "Examples: PT1H (1 hour), P7D (7 days), P30D (30 days)."
                ),
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
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

        workspace_id = args.get("workspace_id", "")
        timespan = args.get("timespan", "PT24H")

        # Auto-discover workspace if not provided
        if not workspace_id:
            workspace_id = self._discover_workspace()
            if workspace_id.startswith("Error"):
                return workspace_id

        return self._run_query(query, workspace_id, timespan)

    def _discover_workspace(self) -> str | None:
        """Find the first available Log Analytics workspace ID in the current subscription."""
        cmd = [
            _find_az(), "monitor", "log-analytics", "workspace", "list",
            "--query", "[0].customerId",
            "--output", "tsv",
        ]
        
        result_str = self._run_az(cmd, label="workspace discovery", timeout=30, use_retry=False)
        
        if result_str.startswith("Error"):
            return "Error: Could not discover Log Analytics workspace."

        workspace_id = result_str.strip()
        if not workspace_id:
            return (
                "Error: No Log Analytics workspace found in the current subscription. "
                "Create one or provide workspace_id explicitly."
            )

        logger.info("Auto-discovered Log Analytics workspace: %s", workspace_id)
        return workspace_id

    def _run_query(self, query: str, workspace_id: str, timespan: str) -> str:
        """Run a KQL query against Log Analytics."""
        cmd = [
            _find_az(), "monitor", "log-analytics", "query",
            "--workspace", workspace_id,
            "--analytics-query", query,
            "--timespan", timespan,
            "--output", "json",
        ]

        # Use truncate=False so we can parse the JSON first
        result_str = self._run_az(cmd, label="Log Analytics query", timeout=60, truncate=False)
        
        if result_str.startswith("Error"):
            if "log-analytics" in result_str.lower() and "not" in result_str.lower():
                return (
                    "Error: The log-analytics CLI extension may not be installed. "
                    "Try: az extension add --name log-analytics\n"
                    f"Original error: {result_str}"
                )
            return result_str

        # Try to format nicely
        try:
            data = json.loads(result_str)
            if isinstance(data, list):
                count = len(data)
                if count == 0:
                    return "Query returned 0 results."
                # Return formatted JSON with row count header
                formatted = json.dumps(data, indent=2)
                if len(formatted) > self.max_output_size:
                    formatted = formatted[:self.max_output_size] + "\n... (truncated)"
                return f"Query returned {count} row(s):\n{formatted}"
        except json.JSONDecodeError:
            pass

        if len(result_str) > self.max_output_size:
            result_str = result_str[:self.max_output_size] + "\n... (truncated)"

        return result_str
