"""
Azure Advisor recommendations tool — read-only, no approval.
"""

import logging

from app.auth.models import User
from app.tools.base import AzureToolBase, _find_az
from app.tools.azure.az_login_check import require_az_login

logger = logging.getLogger(__name__)


class AzAdvisorTool(AzureToolBase):
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

        result_str = self._run_az(cmd, label="Advisor query", timeout=60)
        return result_str if result_str else "No recommendations found."
