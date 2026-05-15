"""
Tool base class and registry.
"""

import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Generator

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)

# Per-request ARM token for user-identity Azure calls.
# Set by the orchestrator at the start of each chat turn; read by AzureToolBase._run_az()
# so every az subprocess authenticates as the current user rather than the server identity.
_current_arm_token: ContextVar[str | None] = ContextVar("arm_token", default=None)


def set_arm_token(token: str | None) -> None:
    """Store the user's ARM token for the current request context. Called by the orchestrator."""
    _current_arm_token.set(token)


def get_arm_token() -> str | None:
    return _current_arm_token.get()


_az_executable_path: str | None = None
_az_circuit_breaker_tripped: bool = False

def _find_az() -> str | None:
    """Resolve the full path to the Azure CLI executable.

    Implements a Circuit Breaker: if 'az' is missing, it trips the breaker
    and returns None, preventing expensive subprocess failures.
    """
    global _az_executable_path, _az_circuit_breaker_tripped
    
    if _az_circuit_breaker_tripped:
        return None
        
    if _az_executable_path:
        return _az_executable_path
        
    path = shutil.which("az")
    if not path and sys.platform == "win32":
        path = shutil.which("az.cmd")
        
    if path:
        _az_executable_path = path
        return path
        
    logger.error("Azure CLI Circuit Breaker TRIPPED: 'az' executable not found on system.")
    _az_circuit_breaker_tripped = True
    return None

# Suppress the black console window that subprocess spawns on Windows.
# Spread this into every subprocess.run() / subprocess.Popen() call:
#   subprocess.run([...], **SUBPROCESS_FLAGS)
SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# Characters that cmd.exe interprets as shell metacharacters when an
# unquoted command STRING is passed to it. Python's subprocess quotes each
# list element via list2cmdline() before handing it to cmd.exe, so these
# chars in arg *values* are not actually shell-interpreted. We therefore
# screen for the null byte (which truncates C strings) and the backtick
# (PowerShell escape). Pipe `|`, ampersand `&`, semicolon `;`, redirects
# `<>`, and `$` are all valid inside KQL queries, file paths, and JSON
# bodies, so blocking them broke az_resource_graph and az_monitor_logs.
_SHELL_METACHAR_PATTERN = r'[`\x00]'

import re as _re


def check_shell_injection(value: str, field_name: str = "argument") -> str | None:
    """Return an error string if *value* contains characters that survive
    subprocess quoting and could enable command injection.

    Defence-in-depth only — the primary protection is that subprocess uses
    list2cmdline() to quote each arg before invoking cmd.exe, so most shell
    metacharacters in arg values are inert. We block only:
      - NUL (\\x00): truncates the command string in cmd.exe
      - backtick (`): PowerShell escape character

    Returns None if safe, or an error message string if blocked.
    """
    if _re.search(_SHELL_METACHAR_PATTERN, value):
        logger.warning(
            "Shell injection blocked in %s: %s", field_name, value[:120],
        )
        bad = ', '.join(repr(c) for c in '`\x00' if c in value)
        return (
            f"Error: {field_name} contains characters that are not allowed "
            f"for security reasons ({bad}). Remove and retry."
        )
    return None


class Tool(ABC):
    """Base class for all tools the LLM can call."""

    name: str
    description: str
    parameters_schema: dict
    requires_approval: bool = False
    enabled_by_config: bool = True
    rate_limit_calls: int | None = None
    rate_limit_window: int = 60  # seconds

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

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Auto-register concrete tools that define a name
        if hasattr(cls, "name") and not getattr(cls, "__abstractmethods__", None):
            TOOL_REGISTRY[cls.name] = cls()

    def execute_streaming(self, args: dict, user: User) -> Generator[str, None, str]:
        """Execute the tool, yielding output chunks as they arrive.

        Returns the full combined output. Subclasses that run subprocesses
        should override this. Default implementation falls back to execute().
        """
        result = self.execute(args, user)
        yield result
        return result


def retry_with_backoff(
    func, max_retries: int = 3, base_delay: float = 2.0, retryable_errors: tuple = ("429", "500", "502", "503", "504", "Too Many Requests")
) -> subprocess.CompletedProcess:
    """Execute a subprocess call with exponential backoff for retryable errors."""
    import time
    import random
    
    for attempt in range(max_retries + 1):
        result = func()
        if result.returncode == 0 or attempt == max_retries:
            return result
        
        error_output = (result.stderr or "") + (result.stdout or "")
        if not any(err in error_output for err in retryable_errors):
            return result
            
        logger.warning(
            "Retryable error encountered (attempt %d/%d). Retrying in backoff...",
            attempt + 1,
            max_retries,
        )
        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
        time.sleep(delay)
    return result


class AzureToolBase(Tool):
    """Base class for tools that call the az CLI under the hood."""
    
    max_output_size = 12288  # Default 12KB token-budget friendly limit

    def _run_az(self, cmd: list[str], label: str, timeout: int = 60, use_retry: bool = True, truncate: bool = True) -> str:
        # Callers (each tool's execute()) are responsible for the login check.
        az_path = _find_az()
        if not az_path:
            return "Error: Azure CLI is not installed on this server. Circuit breaker is open. Tool disabled."

        cmd[0] = az_path

        # Defence-in-depth: Block shell injection in arguments
        for i, arg in enumerate(cmd[1:]):  # skip 'az' command itself
            injection_err = check_shell_injection(str(arg), f"cmd[{i+1}]")
            if injection_err:
                return injection_err

        # Build subprocess env: inherit everything, then overlay the user's ARM
        # token so az authenticates as the current user rather than the server identity.
        env = os.environ.copy()
        arm_token = _current_arm_token.get()
        if arm_token:
            env["AZURE_ACCESS_TOKEN"] = arm_token

        try:
            def _run():
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=(sys.platform == "win32"),
                    env=env,
                    **SUBPROCESS_FLAGS,
                )
            
            if use_retry:
                result = retry_with_backoff(_run)
            else:
                result = _run()

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error ({label}) [exit {result.returncode}]: {error}"
            
            output = result.stdout.strip()
            if truncate and len(output) > self.max_output_size:
                output = output[:self.max_output_size] + "\n... (truncated)"
            # Return empty string on empty success — callers decide how to phrase
            # "no results" so they can match each tool's original CLI semantics.
            return output
            
        except subprocess.TimeoutExpired:
            return f"Error: {label} timed out after {timeout} seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found. Is it installed?"
        except Exception as e:
            logger.error("%s error: %s", label, str(e), exc_info=True)
            return f"Error: {str(e)}"


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
    """Initialize and register all tools via auto-discovery. Called on startup.

    Generic tools (app/tools/generic/) are always loaded.
    Bundle tools (app/tools/azure/, etc.) are loaded only when their
    TOOL_BUNDLE_*_ENABLED flag is true in config. Teams that fork Nexus
    add their own bundle directory here and a matching config flag.
    """
    import pkgutil
    import importlib
    import app.tools.generic

    settings = get_settings()

    # 1a. Always load generic tools
    for _, module_name, _ in pkgutil.iter_modules(app.tools.generic.__path__):
        if not module_name.startswith("_"):
            try:
                importlib.import_module(f"app.tools.generic.{module_name}")
            except Exception as e:
                logger.error("Failed to load generic tool %s: %s", module_name, e)

    # 1b. Load Azure bundle if enabled
    if settings.TOOL_BUNDLE_AZURE_ENABLED:
        import app.tools.azure
        for _, module_name, _ in pkgutil.iter_modules(app.tools.azure.__path__):
            if not module_name.startswith("_"):
                try:
                    importlib.import_module(f"app.tools.azure.{module_name}")
                except Exception as e:
                    logger.error("Failed to load azure tool %s: %s", module_name, e)

    # 2. Apply config flags to enable/disable tools
    config_mapping = {
        "search_kb_semantic": settings.TOOL_SEARCH_SEMANTIC_ENABLED,
        "ms_docs": settings.TOOL_MS_DOCS_ENABLED,
        "run_shell": settings.TOOL_SHELL_ENABLED,
        "az_cli": settings.TOOL_AZ_CLI_ENABLED,
        "az_resource_graph": settings.TOOL_AZ_CLI_ENABLED,  # shares az_cli config
        "az_cost_query": settings.TOOL_AZ_COST_ENABLED,
        "az_monitor_logs": settings.TOOL_AZ_MONITOR_ENABLED,
        "az_rest_api": settings.TOOL_AZ_REST_ENABLED,
        "generate_file": settings.TOOL_GENERATE_FILE_ENABLED,
        "validate_drawio": settings.TOOL_VALIDATE_DRAWIO_ENABLED,
        "render_drawio": settings.TOOL_RENDER_DRAWIO_ENABLED,
        "generate_python_diagram": settings.TOOL_PYTHON_DIAGRAM_ENABLED,
        "generate_drawio_from_python": settings.TOOL_DRAWIO_FROM_PYTHON_ENABLED,
        "az_devops": settings.TOOL_AZ_DEVOPS_ENABLED,
        "az_policy_check": settings.TOOL_AZ_POLICY_ENABLED,
        "az_advisor": settings.TOOL_AZ_ADVISOR_ENABLED,
        "network_test": settings.TOOL_NETWORK_TEST_ENABLED,
        "web_fetch": settings.TOOL_WEB_FETCH_ENABLED,
        "search_stackoverflow": settings.TOOL_SEARCH_STACKOVERFLOW_ENABLED,
        "search_github": settings.TOOL_SEARCH_GITHUB_ENABLED,
        "search_azure_updates": settings.TOOL_SEARCH_AZURE_UPDATES_ENABLED,
        "web_search": settings.TOOL_WEB_SEARCH_ENABLED,
    }

    for name, tool in TOOL_REGISTRY.items():
        if name in config_mapping:
            tool.enabled_by_config = config_mapping[name]

    logger.info(
        "Initialized %d tools (%d enabled)",
        len(TOOL_REGISTRY),
        len([t for t in TOOL_REGISTRY.values() if t.enabled_by_config]),
    )
