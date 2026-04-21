"""
Tool base class and registry.
"""

import logging
from abc import ABC, abstractmethod
from typing import Generator

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)


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

    logger.info(
        "Initialized %d tools (%d enabled)",
        len(TOOL_REGISTRY),
        len([t for t in TOOL_REGISTRY.values() if t.enabled_by_config]),
    )
