"""
Azure Monitor Log Analytics tool — KQL queries against Log Analytics workspaces.
Read-only, no approval needed.
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


class AzMonitorLogsTool(Tool):
    name = "az_monitor_logs"
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

        workspace_id = args.get("workspace_id", "")
        timespan = args.get("timespan", "PT24H")

        # Auto-discover workspace if not provided
        if not workspace_id:
            workspace_id = self._discover_workspace()
            if workspace_id.startswith("Error"):
                return workspace_id

        return self._run_query(query, workspace_id, timespan)

    def _discover_workspace(self) -> str:
        """Auto-discover the first Log Analytics workspace in the subscription."""
        try:
            result = subprocess.run(
                [
                    _find_az(), "monitor", "log-analytics", "workspace", "list",
                    "--query", "[0].customerId",
                    "--output", "tsv",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error: Could not discover Log Analytics workspace: {error}"

            workspace_id = result.stdout.strip()
            if not workspace_id:
                return (
                    "Error: No Log Analytics workspace found in the current subscription. "
                    "Create one or provide workspace_id explicitly."
                )

            logger.info("Auto-discovered Log Analytics workspace: %s", workspace_id)
            return workspace_id

        except subprocess.TimeoutExpired:
            return "Error: Workspace discovery timed out after 30 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found"

    def _run_query(self, query: str, workspace_id: str, timespan: str) -> str:
        """Run a KQL query against Log Analytics."""
        try:
            cmd = [
                _find_az(), "monitor", "log-analytics", "query",
                "--workspace", workspace_id,
                "--analytics-query", query,
                "--timespan", timespan,
                "--output", "json",
            ]

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
                if "log-analytics" in error.lower() and "not" in error.lower():
                    return (
                        "Error: The log-analytics CLI extension may not be installed. "
                        "Try: az extension add --name log-analytics\n"
                        f"Original error: {error}"
                    )
                return f"Error running Log Analytics query (exit {result.returncode}): {error}"

            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"

            # Try to format nicely
            try:
                data = json.loads(output)
                if isinstance(data, list):
                    count = len(data)
                    if count == 0:
                        return "Query returned 0 results."
                    # Return formatted JSON with row count header
                    formatted = json.dumps(data, indent=2)
                    if len(formatted) > _MAX_OUTPUT_SIZE:
                        formatted = formatted[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
                    return f"Query returned {count} row(s):\n{formatted}"
            except json.JSONDecodeError:
                pass

            return output

        except subprocess.TimeoutExpired:
            return "Error: Log Analytics query timed out after 60 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found"
        except Exception as e:
            logger.error("Log Analytics query error: %s", str(e))
            return f"Error: {str(e)}"
