"""
Azure Advisor recommendations tool — read-only, no approval.
"""

import logging
import subprocess
import sys

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login
from app.tools.az_cli import _find_az

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 16384


class AzAdvisorTool(Tool):
    name = "az_advisor"
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
            return output if output else "No recommendations found."
        except subprocess.TimeoutExpired:
            return "Error: Advisor query timed out after 60 seconds"
        except Exception as e:
            return f"Error: {e}"
