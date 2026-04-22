"""
Azure DevOps tool — query pipelines, builds, PRs, and work items.
Read-only queries have no approval. Triggering actions requires approval.
"""

import json
import logging
import subprocess
import sys

from app.auth.models import User
from app.config import get_settings
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_cli import _find_az

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 16384

# Actions that are read-only
_SAFE_ACTIONS = {
    "list_projects", "list_pipelines", "list_builds", "list_prs",
    "list_work_items", "show_pipeline", "show_build", "show_pr",
    "show_work_item", "list_wikis", "show_wiki_page",
}


class AzDevOpsTool(Tool):
    name = "az_devops"
    description = (
        "Query Azure DevOps for projects, pipelines, builds, pull requests, and work items. "
        "Read-only queries (list/show) do not require approval. "
        "Mutation actions (trigger_build, create_pr) require approval. "
        "Requires the azure-devops CLI extension. "
        "The default organization is pre-configured — you do NOT need to supply it unless querying a different org. "
        "Use list_projects first to discover available projects, then drill into pipelines/builds/work items. "
        "Use show_work_item with a work item ID to get full details including description, acceptance criteria, and child tasks. "
        "Use list_wikis to discover wikis in a project, then show_wiki_page to read wiki page content. "
        "Use path '/' with show_wiki_page to get the root page and see all sub-pages."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_projects",
                    "list_pipelines", "show_pipeline",
                    "list_builds", "show_build", "trigger_build",
                    "list_prs", "show_pr", "create_pr",
                    "list_work_items", "show_work_item",
                    "list_wikis", "show_wiki_page",
                ],
                "description": "The DevOps action to perform.",
            },
            "organization": {
                "type": "string",
                "description": "Azure DevOps organization URL. Only needed to override the default.",
            },
            "project": {
                "type": "string",
                "description": "Azure DevOps project name. Required for most operations (not needed for list_projects).",
            },
            "pipeline_id": {
                "type": "integer",
                "description": "Pipeline ID for show_pipeline or trigger_build.",
            },
            "build_id": {
                "type": "integer",
                "description": "Build ID for show_build.",
            },
            "pr_id": {
                "type": "integer",
                "description": "Pull request ID for show_pr.",
            },
            "work_item_id": {
                "type": "integer",
                "description": "Work item ID for show_work_item. Returns full details including description, acceptance criteria, history, and child links.",
            },
            "wiki_name": {
                "type": "string",
                "description": "Wiki name or ID for show_wiki_page. Use list_wikis to discover available wikis.",
            },
            "wiki_path": {
                "type": "string",
                "description": "Wiki page path for show_wiki_page. Use '/' for root page (shows table of contents). Example: '/Architecture/Overview'.",
            },
            "branch": {
                "type": "string",
                "description": "Branch name for trigger_build or create_pr.",
            },
            "target_branch": {
                "type": "string",
                "description": "Target branch for create_pr. Default: main.",
            },
            "title": {
                "type": "string",
                "description": "Title for create_pr.",
            },
            "wiql": {
                "type": "string",
                "description": (
                    "Custom WIQL query for list_work_items. If omitted, uses a default query "
                    "that lists recent user stories and tasks. Example: "
                    "\"SELECT [System.Id],[System.Title],[System.State],[System.WorkItemType] "
                    "FROM workitems WHERE [System.WorkItemType] IN ('User Story','Task','Bug') "
                    "AND [System.State] <> 'Closed' ORDER BY [System.ChangedDate] DESC\""
                ),
            },
            "top": {
                "type": "integer",
                "description": "Number of results to return for list operations. Default: 10.",
            },
        },
        "required": ["action"],
    }

    @property
    def requires_approval(self) -> bool:  # type: ignore[override]
        return False

    def _needs_approval(self, method_or_action: str) -> bool:
        """Dynamic approval: safe actions don't need it."""
        return method_or_action not in _SAFE_ACTIONS

    def _get_org_project(self, args: dict) -> tuple[list[str], list[str]]:
        """Resolve org and project from args, falling back to config defaults."""
        settings = get_settings()
        org = args.get("organization", "") or settings.AZ_DEVOPS_ORG
        project = args.get("project", "") or settings.AZ_DEVOPS_PROJECT
        org_args = ["--org", org] if org else []
        project_args = ["-p", project] if project else []
        return org_args, project_args

    def execute(self, args: dict, user: User) -> str:
        action = args.get("action", "")
        top = args.get("top", 10)

        org_args, project_args = self._get_org_project(args)

        if action == "list_projects":
            cmd = [_find_az(), "devops", "project", "list", "--top", str(top), "--output", "json"] + org_args

        elif action == "list_pipelines":
            cmd = [_find_az(), "pipelines", "list", "--top", str(top), "--output", "json"] + org_args + project_args
        elif action == "show_pipeline":
            pid = args.get("pipeline_id")
            if not pid:
                return "Error: pipeline_id is required for show_pipeline"
            cmd = [_find_az(), "pipelines", "show", "--id", str(pid), "--output", "json"] + org_args + project_args
        elif action == "list_builds":
            cmd = [_find_az(), "pipelines", "build", "list", "--top", str(top), "--output", "json"] + org_args + project_args
        elif action == "show_build":
            bid = args.get("build_id")
            if not bid:
                return "Error: build_id is required for show_build"
            cmd = [_find_az(), "pipelines", "build", "show", "--id", str(bid), "--output", "json"] + org_args + project_args
        elif action == "trigger_build":
            pid = args.get("pipeline_id")
            if not pid:
                return "Error: pipeline_id is required for trigger_build"
            cmd = [_find_az(), "pipelines", "run", "--id", str(pid), "--output", "json"] + org_args + project_args
            branch = args.get("branch")
            if branch:
                cmd.extend(["--branch", branch])
        elif action == "list_prs":
            cmd = [_find_az(), "repos", "pr", "list", "--top", str(top), "--output", "json"] + org_args + project_args
        elif action == "show_pr":
            pr_id = args.get("pr_id")
            if not pr_id:
                return "Error: pr_id is required for show_pr"
            cmd = [_find_az(), "repos", "pr", "show", "--id", str(pr_id), "--output", "json"] + org_args + project_args
        elif action == "create_pr":
            branch = args.get("branch")
            title = args.get("title")
            target = args.get("target_branch", "main")
            if not branch or not title:
                return "Error: branch and title are required for create_pr"
            cmd = [
                _find_az(), "repos", "pr", "create",
                "--source-branch", branch,
                "--target-branch", target,
                "--title", title,
                "--output", "json",
            ] + org_args + project_args
        elif action == "list_work_items":
            wiql = args.get("wiql") or (
                "SELECT [System.Id],[System.Title],[System.State],[System.WorkItemType],"
                "[System.AssignedTo],[System.IterationPath] "
                "FROM workitems "
                "WHERE [System.TeamProject] = @project "
                "AND [System.WorkItemType] IN ('User Story','Task','Bug','Feature','Epic') "
                "AND [System.State] <> 'Removed' "
                "ORDER BY [System.ChangedDate] DESC"
            )
            cmd = [
                _find_az(), "boards", "query",
                "--wiql", wiql,
                "--output", "json",
            ] + org_args + project_args
        elif action == "show_work_item":
            wid = args.get("work_item_id")
            if not wid:
                return "Error: work_item_id is required for show_work_item"
            cmd = [
                _find_az(), "boards", "work-item", "show",
                "--id", str(wid),
                "--expand", "all",
                "--output", "json",
            ] + org_args
        elif action == "list_wikis":
            cmd = [_find_az(), "devops", "wiki", "list", "--output", "json"] + org_args + project_args
        elif action == "show_wiki_page":
            wiki_name = args.get("wiki_name")
            wiki_path = args.get("wiki_path", "/")
            if not wiki_name:
                return "Error: wiki_name is required for show_wiki_page. Use list_wikis first to find available wikis."
            cmd = [
                _find_az(), "devops", "wiki", "page", "show",
                "--wiki", wiki_name,
                "--path", wiki_path,
                "--include-content",
                "--output", "json",
            ] + org_args + project_args
        else:
            return f"Error: Unknown action '{action}'"

        return self._run_cmd(cmd, action)

    def _run_cmd(self, cmd: list[str], label: str) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                shell=(sys.platform == "win32"),
                **SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                if "azure-devops" in error.lower() or "not found" in error.lower():
                    return (
                        f"Error: Azure DevOps CLI extension may not be installed. "
                        "Try: az extension add --name azure-devops\n"
                        f"Original error: {error}"
                    )
                return f"Error running {label}: {error}"

            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            return output if output else f"{label} completed (no output)"

        except subprocess.TimeoutExpired:
            return f"Error: {label} timed out after 30 seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found"
        except Exception as e:
            logger.error("DevOps tool error: %s", str(e))
            return f"Error: {str(e)}"
