"""
Azure Policy Compliance check tool — read-only, no approval.
"""

import json
import logging

from app.auth.models import User
from bundles.azure._az_base import AzureToolBase, _find_az
from bundles.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)

_MAX_LISTED = 20


class AzPolicyCheckTool(AzureToolBase):
    name = "az_policy_check"
    config_flag = "TOOL_AZ_POLICY_ENABLED"
    # Backstop in case the summary itself runs long; the orchestrator head/tail
    # trims above this (B4).
    result_limit = 6_000
    description = (
        "Check Azure Policy compliance status. Read-only — no approval needed. "
        "List non-compliant resources, policy assignments, and compliance summaries."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["compliance_summary", "non_compliant_resources", "list_assignments"],
                "description": (
                    "Action to perform:\n"
                    "- compliance_summary: Overall compliance percentage\n"
                    "- non_compliant_resources: List non-compliant resources\n"
                    "- list_assignments: List policy assignments"
                ),
            },
            "resource_group": {
                "type": "string",
                "description": "Optional: scope to a specific resource group.",
            },
            "top": {
                "type": "integer",
                "description": "Number of results to return. Default: 20.",
            },
        },
        "required": ["action"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        login_err = require_az_login()
        if login_err:
            return login_err

        action = args.get("action", "compliance_summary")
        rg = args.get("resource_group", "")
        top = args.get("top", 20)

        if action == "compliance_summary":
            cmd = [_find_az(), "policy", "state", "summarize", "--output", "json"]
            if rg:
                cmd.extend(["--resource-group", rg])
        elif action == "non_compliant_resources":
            cmd = [
                _find_az(), "policy", "state", "list",
                "--filter", "complianceState eq 'NonCompliant'",
                "--top", str(top),
                "--output", "json",
            ]
            if rg:
                cmd.extend(["--resource-group", rg])
        elif action == "list_assignments":
            cmd = [_find_az(), "policy", "assignment", "list", "--output", "json"]
            if rg:
                cmd.extend(["--resource-group", rg])
        else:
            return f"Error: Unknown action '{action}'"

        # truncate=False: we need the full JSON to summarize; char-truncating it
        # mid-structure would make it unparseable (B4).
        result_str = self._run_az(cmd, label="Policy query", timeout=60, truncate=False)
        if not result_str:
            return "No results."
        if result_str.startswith("Error"):
            return result_str
        return self._summarize(action, result_str)

    # ------------------------------------------------------------------

    def _summarize(self, action: str, raw: str) -> str:
        """Condense the raw Policy JSON into a tidy text summary per action.

        On any parse miss, fall back to the raw output (capped) so nothing is
        silently lost (B4) — same defensive contract as az_cost_query.
        """
        try:
            data = json.loads(raw)
            if action == "compliance_summary":
                return self._summarize_compliance(data)
            if action == "non_compliant_resources":
                return self._summarize_states(data)
            if action == "list_assignments":
                return self._summarize_assignments(data)
            # Unknown action shouldn't reach here, but be safe.
            raise ValueError(f"no summarizer for action {action!r}")
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Failed to summarize Policy response (%s): %s", action, e)
            return raw[: self.max_output_size]

    @staticmethod
    def _summarize_compliance(data: dict) -> str:
        # `az policy state summarize` → {"value": [ {results, policyAssignments} ]}.
        # Some az versions (and our test fixtures) hand back the summary object
        # directly rather than wrapped in `value`; accept both.
        if isinstance(data, dict):
            summaries = data.get("value")
            if summaries is None:
                summaries = [data] if ("results" in data or "policyAssignments" in data) else []
        else:
            summaries = []
        if not summaries:
            return "No compliance data returned (no policy assignments in scope?)."
        top = summaries[0]
        results = top.get("results", {}) or {}
        nc_resources = results.get("nonCompliantResources", 0)
        nc_policies = results.get("nonCompliantPolicies", 0)

        lines = [
            "Azure Policy compliance summary:",
            f"  Non-compliant resources: {nc_resources}",
            f"  Non-compliant policies:  {nc_policies}",
        ]

        assignments = top.get("policyAssignments", []) or []
        if assignments:
            lines.append("")
            lines.append(f"Per-assignment ({min(len(assignments), _MAX_LISTED)} shown):")
            for a in assignments[:_MAX_LISTED]:
                aid = a.get("policyAssignmentId", "") or "?"
                name = aid.rsplit("/", 1)[-1] if "/" in aid else aid
                ares = a.get("results", {}) or {}
                lines.append(
                    f"  - {name}: {ares.get('nonCompliantResources', 0)} non-compliant "
                    f"resource(s), {ares.get('nonCompliantPolicies', 0)} policy(ies)"
                )
            if len(assignments) > _MAX_LISTED:
                lines.append(f"  ... and {len(assignments) - _MAX_LISTED} more.")
        return "\n".join(lines)

    @staticmethod
    def _summarize_states(data) -> str:
        # `az policy state list` → JSON array of state records.
        states = data if isinstance(data, list) else data.get("value", [])
        if not states:
            return "No non-compliant resources found."
        lines = [f"Non-compliant resources: {len(states)} found."]
        lines.append(f"Showing {min(len(states), _MAX_LISTED)}:")
        for s in states[:_MAX_LISTED]:
            rid = s.get("resourceId", "") or "?"
            res_name = rid.rsplit("/", 1)[-1] if "/" in rid else rid
            policy = (
                s.get("policyDefinitionName")
                or s.get("policyDefinitionId", "").rsplit("/", 1)[-1]
                or "?"
            )
            lines.append(f"  - {res_name} - policy: {policy}")
        if len(states) > _MAX_LISTED:
            lines.append(f"  ... and {len(states) - _MAX_LISTED} more.")
        return "\n".join(lines)

    @staticmethod
    def _summarize_assignments(data) -> str:
        # `az policy assignment list` → JSON array of assignments.
        assignments = data if isinstance(data, list) else data.get("value", [])
        if not assignments:
            return "No policy assignments found."
        lines = [f"Policy assignments: {len(assignments)} found."]
        lines.append(f"Showing {min(len(assignments), _MAX_LISTED)}:")
        for a in assignments[:_MAX_LISTED]:
            name = a.get("displayName") or a.get("name", "?")
            scope = a.get("scope", "") or ""
            scope_tail = scope.rsplit("/", 1)[-1] if "/" in scope else scope
            enforce = a.get("enforcementMode", "")
            line = f"  - {name}"
            if scope_tail:
                line += f" (scope: {scope_tail})"
            if enforce:
                line += f" [{enforce}]"
            lines.append(line)
        if len(assignments) > _MAX_LISTED:
            lines.append(f"  ... and {len(assignments) - _MAX_LISTED} more.")
        return "\n".join(lines)
