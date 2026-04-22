"""
Azure Policy Compliance check tool — read-only, no approval.
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


class AzPolicyCheckTool(Tool):
    name = "az_policy_check"
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

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                shell=(sys.platform == "win32"), **SUBPROCESS_FLAGS,
            )
            if result.returncode != 0:
                return f"Error: {result.stderr.strip() if result.stderr else 'Unknown error'}"
            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            return output if output else "No results."
        except subprocess.TimeoutExpired:
            return "Error: Policy query timed out after 60 seconds"
        except Exception as e:
            return f"Error: {e}"
