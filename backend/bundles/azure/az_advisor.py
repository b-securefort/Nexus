"""
Azure Advisor recommendations tool — read-only, no approval.
"""

import json
import logging

from app.auth.models import User
from bundles.azure._az_base import AzureToolBase, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)

# Impact ordering for "most important first" sorting.
_IMPACT_RANK = {"High": 0, "Medium": 1, "Low": 2}
_MAX_LISTED = 20


class AzAdvisorTool(AzureToolBase):
    name = "az_advisor"
    config_flag = "TOOL_AZ_ADVISOR_ENABLED"
    # Backstop in case the summary itself runs long; the orchestrator head/tail
    # trims above this (B4).
    result_limit = 6_000
    description = (
        "Retrieve Azure Advisor recommendations for cost, security, reliability, "
        "performance, and operational excellence. Read-only — no approval needed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["Cost", "Security", "HighAvailability", "Performance", "OperationalExcellence"],
                "description": "Filter by recommendation category. If omitted, returns all categories.",
            },
            "resource_group": {
                "type": "string",
                "description": "Optional: filter to a specific resource group.",
            },
        },
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        login_err = require_az_login()
        if login_err:
            return login_err

        category = args.get("category", "")
        rg = args.get("resource_group", "")

        cmd = [_find_az(), "advisor", "recommendation", "list", "--output", "json"]
        if category:
            cmd.extend(["--category", category])
        if rg:
            cmd.extend(["--resource-group", rg])

        # truncate=False: we need the full JSON to summarize; char-truncating it
        # mid-structure would make it unparseable (B4).
        result_str = self._run_az(cmd, label="Advisor query", timeout=60, truncate=False)
        if not result_str:
            return "No recommendations found."
        if result_str.startswith("Error"):
            return result_str
        return self._summarize(result_str)

    # ------------------------------------------------------------------

    @staticmethod
    def _field(item: dict, key: str):
        """Read a field whether az surfaced it at the top level or under
        `properties` (ARM shape varies by az version)."""
        if key in item:
            return item[key]
        props = item.get("properties")
        if isinstance(props, dict):
            return props.get(key)
        return None

    def _summarize(self, raw: str) -> str:
        """Condense the raw Advisor JSON array into a tidy text summary.

        Mirrors az_cost_query._format_cost_response: on any parse miss, fall
        back to the raw output (capped) so nothing is ever silently lost (B4).
        """
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("expected a JSON array of recommendations")
            if not data:
                return "No Advisor recommendations found."

            by_category: dict[str, int] = {}
            by_impact: dict[str, int] = {}
            for item in data:
                cat = str(self._field(item, "category") or "Uncategorized")
                imp = str(self._field(item, "impact") or "Unknown")
                by_category[cat] = by_category.get(cat, 0) + 1
                by_impact[imp] = by_impact.get(imp, 0) + 1

            def _impact_key(item: dict) -> int:
                return _IMPACT_RANK.get(str(self._field(item, "impact")), 3)

            ranked = sorted(data, key=_impact_key)

            lines = [f"Azure Advisor: {len(data)} recommendation(s)"]
            lines.append(
                "By category: "
                + ", ".join(f"{c} {n}" for c, n in sorted(by_category.items()))
            )
            lines.append(
                "By impact: "
                + ", ".join(
                    f"{i} {by_impact[i]}"
                    for i in sorted(by_impact, key=lambda x: _IMPACT_RANK.get(x, 3))
                )
            )
            lines.append("")
            lines.append(f"Top {min(len(ranked), _MAX_LISTED)} (most impactful first):")

            for n, item in enumerate(ranked[:_MAX_LISTED], 1):
                cat = self._field(item, "category") or "?"
                imp = self._field(item, "impact") or "?"
                short = self._field(item, "shortDescription") or {}
                problem = (short.get("problem") if isinstance(short, dict) else None) or "?"
                solution = (short.get("solution") if isinstance(short, dict) else None) or ""
                # Prefer the concrete impacted resource name; fall back to type.
                resource = (
                    self._field(item, "impactedValue")
                    or self._field(item, "impactedField")
                    or ""
                )
                line = f"{n}. [{imp} | {cat}] {problem}"
                if solution and solution != problem:
                    line += f" -> {solution}"
                if resource:
                    line += f" (resource: {resource})"
                lines.append(line)

            if len(ranked) > _MAX_LISTED:
                lines.append(f"... and {len(ranked) - _MAX_LISTED} more.")

            return "\n".join(lines)

        except (json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Failed to summarize Advisor response: %s", e)
            return raw[: self.max_output_size]
