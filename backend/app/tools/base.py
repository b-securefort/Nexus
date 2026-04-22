"""
Tool base class and registry.
"""

import logging
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Generator

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)

# Suppress the black console window that subprocess spawns on Windows.
# Spread this into every subprocess.run() / subprocess.Popen() call:
#   subprocess.run([...], **SUBPROCESS_FLAGS)
SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


class Tool(ABC):
    """Base class for all tools the LLM can call."""

    name: str
    description: str
    parameters_schema: dict
    requires_approval: bool = False
    enabled_by_config: bool = True

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    @abstractmethod
    def execute(self, args: dict, user: User) -> str:
        """Execute the tool and return a string result."""
        ...

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        """Execute the tool, yielding output chunks as they arrive.

        Returns the full combined output. Subclasses that run subprocesses
        should override this. Default implementation falls back to execute().
        """
        result = self.execute(args, user)
        yield result
        return result


# Global tool registry
TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    """Register a tool in the global registry."""
    TOOL_REGISTRY[tool.name] = tool
    logger.debug("Registered tool: %s", tool.name)


def get_tool(name: str) -> Tool | None:
    """Get a tool by name."""
    return TOOL_REGISTRY.get(name)


def list_tools() -> list[Tool]:
    """List all enabled tools."""
    return [t for t in TOOL_REGISTRY.values() if t.enabled_by_config]


def resolve_tools(tool_names: list[str]) -> list[Tool]:
    """Resolve a list of tool names to Tool instances, filtering disabled ones."""
    settings = get_settings()
    tools = []
    for name in tool_names:
        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            logger.warning("Tool not found in registry: %s", name)
            continue
        if not tool.enabled_by_config:
            logger.warning("Tool %s is disabled by config, skipping", name)
            continue
        tools.append(tool)
    return tools


def init_tools() -> None:
    """Initialize and register all tools. Called on startup."""
    settings = get_settings()

    from app.tools.kb_tools import ReadKBFileTool, SearchKBTool
    from app.tools.ms_docs import FetchMsDocsTool
    from app.tools.shell import RunShellTool
    from app.tools.az_cli import AzCliTool
    from app.tools.az_resource_graph import AzResourceGraphTool
    from app.tools.learn_tool import ReadLearningsTool, UpdateLearningsTool
    from app.tools.az_cost import AzCostQueryTool
    from app.tools.az_monitor import AzMonitorLogsTool
    from app.tools.az_rest import AzRestApiTool
    from app.tools.generate_file import GenerateFileTool
    from app.tools.az_devops import AzDevOpsTool
    from app.tools.az_policy import AzPolicyCheckTool
    from app.tools.az_advisor import AzAdvisorTool
    from app.tools.network_test import NetworkTestTool
    from app.tools.diagram_gen import DiagramGenTool
    from app.tools.web_fetch import WebFetchTool

    register_tool(ReadKBFileTool())
    register_tool(SearchKBTool())
    register_tool(ReadLearningsTool())
    register_tool(UpdateLearningsTool())

    ms_docs = FetchMsDocsTool()
    ms_docs.enabled_by_config = settings.TOOL_MS_DOCS_ENABLED
    register_tool(ms_docs)

    shell = RunShellTool()
    shell.enabled_by_config = settings.TOOL_SHELL_ENABLED
    register_tool(shell)

    az_cli = AzCliTool()
    az_cli.enabled_by_config = settings.TOOL_AZ_CLI_ENABLED
    register_tool(az_cli)

    az_graph = AzResourceGraphTool()
    az_graph.enabled_by_config = settings.TOOL_AZ_CLI_ENABLED  # shares az_cli config
    register_tool(az_graph)

    az_cost = AzCostQueryTool()
    az_cost.enabled_by_config = settings.TOOL_AZ_COST_ENABLED
    register_tool(az_cost)

    az_monitor = AzMonitorLogsTool()
    az_monitor.enabled_by_config = settings.TOOL_AZ_MONITOR_ENABLED
    register_tool(az_monitor)

    az_rest = AzRestApiTool()
    az_rest.enabled_by_config = settings.TOOL_AZ_REST_ENABLED
    register_tool(az_rest)

    gen_file = GenerateFileTool()
    gen_file.enabled_by_config = settings.TOOL_GENERATE_FILE_ENABLED
    register_tool(gen_file)

    az_devops = AzDevOpsTool()
    az_devops.enabled_by_config = settings.TOOL_AZ_DEVOPS_ENABLED
    register_tool(az_devops)

    az_policy = AzPolicyCheckTool()
    az_policy.enabled_by_config = settings.TOOL_AZ_POLICY_ENABLED
    register_tool(az_policy)

    az_advisor = AzAdvisorTool()
    az_advisor.enabled_by_config = settings.TOOL_AZ_ADVISOR_ENABLED
    register_tool(az_advisor)

    net_test = NetworkTestTool()
    net_test.enabled_by_config = settings.TOOL_NETWORK_TEST_ENABLED
    register_tool(net_test)

    diagram = DiagramGenTool()
    diagram.enabled_by_config = settings.TOOL_DIAGRAM_GEN_ENABLED
    register_tool(diagram)

    web = WebFetchTool()
    web.enabled_by_config = settings.TOOL_WEB_FETCH_ENABLED
    register_tool(web)

    logger.info(
        "Initialized %d tools (%d enabled)",
        len(TOOL_REGISTRY),
        len([t for t in TOOL_REGISTRY.values() if t.enabled_by_config]),
    )
