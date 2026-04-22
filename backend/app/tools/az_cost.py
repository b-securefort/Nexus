"""
Azure Cost Management tool — queries the Cost Management REST API via az rest.
Read-only, no approval needed.
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login
from app.tools.az_cli import _find_az

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 16384

# Timeframe strings accepted by the Cost Management REST API
_TIMEFRAME_MAP = {
    "last_7_days": "Custom",
    "last_30_days": "Custom",
    "last_month": "TheLastMonth",
    "this_month": "MonthToDate",
    "last_3_months": "Custom",
}

_CUSTOM_DAYS = {
    "last_7_days": 7,
    "last_30_days": 30,
    "last_3_months": 90,
}


class AzCostQueryTool(Tool):
    name = "az_cost_query"
    description = (
        "Query Azure Cost Management for cost and usage data via the REST API. "
        "Read-only — no approval needed. "
        "Use this instead of az_cli for cost queries. Supports usage summaries, "
        "cost breakdowns by resource group/type/service/location, and budget status."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": ["usage", "forecast", "budget_status"],
                "description": (
                    "Type of cost query:\n"
                    "- usage: Actual cost/usage data for a time period\n"
                    "- forecast: Cost forecast for the current billing period\n"
                    "- budget_status: List budgets and their current spend vs limit"
                ),
            },
            "time_period": {
                "type": "string",
                "enum": ["last_7_days", "last_30_days", "last_month", "this_month", "last_3_months"],
                "description": "Time period for the query. Default: this_month",
            },
            "group_by": {
                "type": "string",
                "enum": ["ResourceGroup", "ResourceType", "ServiceName", "ResourceLocation", "none"],
                "description": "Optional grouping dimension for cost breakdown. Default: none (total only)",
            },
            "filter_resource_group": {
                "type": "string",
                "description": "Optional: filter costs to a specific resource group name",
            },
        },
        "required": ["query_type"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        login_err = require_az_login()
        if login_err:
            return login_err

        query_type = args.get("query_type", "usage")
        time_period = args.get("time_period", "this_month")
        group_by = args.get("group_by", "none")
        filter_rg = args.get("filter_resource_group", "")

        if query_type == "budget_status":
            return self._query_budgets()
        elif query_type == "forecast":
            return self._query_forecast()
        else:
            return self._query_usage(time_period, group_by, filter_rg)

    # ── Subscription discovery ───────────────────────────────────────────

    def _get_subscription_id(self) -> str | None:
        """Get the current subscription ID from az account show."""
        try:
            result = subprocess.run(
                [_find_az(), "account", "show", "--query", "id", "-o", "tsv"],
                capture_output=True, text=True, timeout=15,
                shell=(sys.platform == "win32"), **SUBPROCESS_FLAGS,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None

    # ── Usage query via REST API ─────────────────────────────────────────

    def _query_usage(self, time_period: str, group_by: str, filter_rg: str) -> str:
        """Query actual usage costs via the Cost Management REST API."""
        sub_id = self._get_subscription_id()
        if not sub_id:
            return "Error: Could not determine the current subscription ID. Run 'az account show' to check."

        # Build the REST request body
        timeframe = _TIMEFRAME_MAP.get(time_period, "MonthToDate")
        dataset: dict = {
            "granularity": "Daily",
            "aggregation": {
                "totalCost": {"name": "PreTaxCost", "function": "Sum"},
            },
        }

        # Add grouping
        if group_by and group_by != "none":
            dataset["grouping"] = [{"type": "Dimension", "name": group_by}]

        # Add filter
        if filter_rg:
            dataset["filter"] = {
                "dimensions": {
                    "name": "ResourceGroup",
                    "operator": "In",
                    "values": [filter_rg],
                }
            }

        body: dict = {"type": "Usage", "timeframe": timeframe, "dataset": dataset}

        # Add custom date range if needed
        if timeframe == "Custom":
            now = datetime.now(timezone.utc)
            days = _CUSTOM_DAYS.get(time_period, 30)
            start = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+00:00")
            end = now.strftime("%Y-%m-%dT23:59:59+00:00")
            body["timePeriod"] = {"from": start, "to": end}

        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )

        raw = self._az_rest("POST", url, body, "usage cost query")
        if raw.startswith("Error"):
            return raw

        return self._format_cost_response(raw, group_by)

    def _query_forecast(self) -> str:
        """Query cost forecast via the REST API."""
        sub_id = self._get_subscription_id()
        if not sub_id:
            return "Error: Could not determine the current subscription ID."

        body = {
            "type": "Usage",
            "timeframe": "MonthToDate",
            "dataset": {
                "granularity": "Daily",
                "aggregation": {
                    "totalCost": {"name": "PreTaxCost", "function": "Sum"},
                },
            },
        }

        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )

        raw = self._az_rest("POST", url, body, "cost forecast")
        if raw.startswith("Error"):
            return raw

        return self._format_cost_response(raw, "none")

    def _query_budgets(self) -> str:
        """List budgets and their current spend."""
        sub_id = self._get_subscription_id()
        if not sub_id:
            return "Error: Could not determine the current subscription ID."

        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/providers/Microsoft.Consumption/budgets?api-version=2023-11-01"
        )

        raw = self._az_rest("GET", url, None, "budget listing")
        if raw.startswith("Error"):
            return raw

        try:
            data = json.loads(raw)
            budgets = data.get("value", [])
            if not budgets:
                return "No budgets found for this subscription."

            lines = ["Budget Status:"]
            for b in budgets:
                name = b.get("name", "?")
                props = b.get("properties", {})
                amount = props.get("amount", "?")
                current = props.get("currentSpend", {}).get("amount", "?")
                currency = props.get("currentSpend", {}).get("unit", "USD")
                lines.append(f"  - {name}: {current} / {amount} {currency}")
            return "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            return raw

    # ── REST call helper ─────────────────────────────────────────────────

    def _az_rest(self, method: str, url: str, body: dict | None, label: str) -> str:
        """Execute an az rest call and return the raw output."""
        cmd = [_find_az(), "rest", "--method", method, "--url", url, "--output", "json"]
        if body is not None:
            cmd.extend(["--body", json.dumps(body), "--headers", "Content-Type=application/json"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=60,
                shell=(sys.platform == "win32"), **SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                # Handle 429 rate limits with a retry
                if "429" in error or "Too Many Requests" in error:
                    import time
                    time.sleep(3)
                    result = subprocess.run(
                        cmd,
                        capture_output=True, text=True, timeout=60,
                        shell=(sys.platform == "win32"), **SUBPROCESS_FLAGS,
                    )
                    if result.returncode != 0:
                        error = result.stderr.strip() if result.stderr else "Unknown error"
                        return f"Error running {label} (exit {result.returncode}): {error}"
                else:
                    return f"Error running {label} (exit {result.returncode}): {error}"

            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            return output

        except subprocess.TimeoutExpired:
            return f"Error: {label} timed out after 60 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found. Is it installed?"
        except Exception as e:
            logger.error("Cost query error: %s", str(e))
            return f"Error: {str(e)}"

    # ── Response formatting ──────────────────────────────────────────────

    def _format_cost_response(self, raw: str, group_by: str) -> str:
        """Parse Cost Management REST response into readable summary."""
        try:
            data = json.loads(raw)
            props = data.get("properties", data)
            columns = props.get("columns", [])
            rows = props.get("rows", [])

            if not rows:
                return "No cost data returned for this period."

            col_names = [c.get("name", f"col{i}") for i, c in enumerate(columns)]

            # Find the cost column index
            cost_idx = None
            for i, c in enumerate(columns):
                if c.get("name") in ("PreTaxCost", "Cost"):
                    cost_idx = i
                    break

            if cost_idx is None:
                # Return raw tabular data
                return json.dumps({"columns": col_names, "rows": rows[:50]}, indent=2)

            # Sum up the total cost
            total = sum(float(r[cost_idx]) for r in rows if r[cost_idx] is not None)

            # Find currency column
            currency = "USD"
            for i, c in enumerate(columns):
                if c.get("name") == "Currency" and rows:
                    currency = rows[0][i] or "USD"
                    break

            lines = [f"Total cost: {total:.2f} {currency}"]

            # If grouped, aggregate by group
            if group_by and group_by != "none":
                group_idx = None
                for i, c in enumerate(columns):
                    if c.get("name") == group_by:
                        group_idx = i
                        break

                if group_idx is not None:
                    # Aggregate daily rows by group key
                    group_totals: dict[str, float] = {}
                    for r in rows:
                        key = str(r[group_idx]) if r[group_idx] else "(none)"
                        group_totals[key] = group_totals.get(key, 0) + float(r[cost_idx] or 0)

                    # Sort descending by cost
                    sorted_groups = sorted(group_totals.items(), key=lambda x: x[1], reverse=True)
                    lines.append(f"\nBreakdown by {group_by}:")
                    for name, cost in sorted_groups[:30]:
                        pct = (cost / total * 100) if total > 0 else 0
                        lines.append(f"  {name}: {cost:.2f} {currency} ({pct:.1f}%)")
            else:
                # Show daily breakdown for ungrouped queries
                date_idx = None
                for i, c in enumerate(columns):
                    if c.get("name") == "UsageDate":
                        date_idx = i
                        break

                if date_idx is not None and len(rows) <= 31:
                    lines.append("\nDaily breakdown:")
                    for r in rows:
                        date = str(r[date_idx])
                        cost = float(r[cost_idx] or 0)
                        lines.append(f"  {date}: {cost:.2f} {currency}")

            return "\n".join(lines)

        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("Failed to format cost response: %s", e)
            # Return raw truncated
            return raw[:_MAX_OUTPUT_SIZE]
