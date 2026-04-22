"""
Azure CLI login state checker — caches auth state with TTL.

Used by az_cli, az_resource_graph, and other az-based tools to
pre-check login status before executing commands.
"""

import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from app.tools.base import SUBPROCESS_FLAGS

logger = logging.getLogger(__name__)

# Cache TTL in seconds
_LOGIN_CACHE_TTL = 300  # 5 minutes


@dataclass
class AzLoginState:
    """Cached Azure CLI login state."""
    logged_in: bool
    user: str = ""
    subscription_name: str = ""
    subscription_id: str = ""
    tenant_id: str = ""
    error: str = ""
    checked_at: float = 0.0


# Module-level cache
_cached_state: AzLoginState | None = None


def _find_az() -> str:
    """Resolve the full path to az CLI."""
    path = shutil.which("az")
    if path:
        return path
    if sys.platform == "win32":
        path = shutil.which("az.cmd")
        if path:
            return path
    return "az"


def check_az_login(force_refresh: bool = False) -> AzLoginState:
    """Check Azure CLI login state, using cache if available.
    
    Returns an AzLoginState with login details or error info.
    Results are cached for _LOGIN_CACHE_TTL seconds.
    """
    global _cached_state

    now = time.time()
    if (
        not force_refresh
        and _cached_state is not None
        and (now - _cached_state.checked_at) < _LOGIN_CACHE_TTL
    ):
        return _cached_state

    state = _do_check()
    _cached_state = state
    return state


def clear_login_cache() -> None:
    """Clear the cached login state (e.g., after an auth error)."""
    global _cached_state
    _cached_state = None


def get_az_context_prompt() -> str:
    """Get a system prompt section describing the current Azure CLI state.
    
    Returns a short string suitable for injection into the system prompt.
    Does NOT trigger a fresh check — uses cached state only.
    """
    if _cached_state is None:
        return (
            "## Azure Context\n"
            "- Logged in: Unknown (not yet checked)\n"
            "- Azure CLI auth will be verified on first Azure tool call."
        )

    if _cached_state.logged_in:
        return (
            "## Azure Context\n"
            f"- Logged in: Yes\n"
            f"- Account: {_cached_state.user}\n"
            f"- Subscription: {_cached_state.subscription_name} ({_cached_state.subscription_id})\n"
            f"- Tenant: {_cached_state.tenant_id}"
        )

    return (
        "## Azure Context\n"
        "- Logged in: **No**\n"
        f"- Error: {_cached_state.error}\n"
        "- NOTE: Azure CLI is not authenticated. Before running any Azure commands,\n"
        "  instruct the user to run: `az login --use-device-code`\n"
        "  Do NOT attempt to run Azure commands until the user confirms they've logged in."
    )


def require_az_login() -> str | None:
    """Check Azure login and return an error string if not logged in, or None if OK.
    
    This should be called at the start of any Azure tool's execute() method.
    If it returns a string, the tool should return that string immediately.
    """
    state = check_az_login()

    if not state.logged_in:
        if "not found" in state.error.lower():
            return (
                "Error: Azure CLI (az) is not installed on this machine.\n"
                "Install it from: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
            )
        return (
            "Error: Azure CLI is not logged in.\n"
            "Please run the following command to authenticate:\n\n"
            "  az login --use-device-code\n\n"
            "This will show a code and a URL. Open the URL in a browser, "
            "enter the code, and sign in with your Azure account.\n"
            f"Details: {state.error}"
        )

    return None  # All good


def _do_check() -> AzLoginState:
    """Actually run az account show and parse the result."""
    try:
        result = subprocess.run(
            [_find_az(), "account", "show", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=(sys.platform == "win32"),
            **SUBPROCESS_FLAGS,
        )

        if result.returncode != 0:
            error = result.stderr.strip() if result.stderr else "Unknown error"
            logger.warning("Azure CLI not logged in: %s", error)
            return AzLoginState(
                logged_in=False,
                error=error,
                checked_at=time.time(),
            )

        data = json.loads(result.stdout)
        state = AzLoginState(
            logged_in=True,
            user=data.get("user", {}).get("name", "unknown"),
            subscription_name=data.get("name", "unknown"),
            subscription_id=data.get("id", ""),
            tenant_id=data.get("tenantId", ""),
            checked_at=time.time(),
        )
        logger.info(
            "Azure CLI logged in: %s (sub: %s)",
            state.user, state.subscription_name,
        )
        return state

    except FileNotFoundError:
        return AzLoginState(
            logged_in=False,
            error="Azure CLI (az) not found. Is it installed?",
            checked_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return AzLoginState(
            logged_in=False,
            error="az account show timed out after 15 seconds",
            checked_at=time.time(),
        )
    except json.JSONDecodeError as e:
        return AzLoginState(
            logged_in=False,
            error=f"Failed to parse az account show output: {e}",
            checked_at=time.time(),
        )
    except Exception as e:
        return AzLoginState(
            logged_in=False,
            error=str(e),
            checked_at=time.time(),
        )
