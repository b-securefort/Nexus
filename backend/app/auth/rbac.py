"""
Role-based access control for shared skills and tools.

Source of truth at runtime is a process-local dict (`_ACCESS_MAP`) that maps
each Entra App Role name to the set of skill slugs and tool names that role
can use. The dict starts as `DEFAULT_ACCESS_MAP` baked into this file and is
optionally overridden at startup by `init_rbac()`, which reads a JSON blob
from Azure App Configuration.

Failure mode: if App Configuration is unreachable, malformed, or simply not
configured (`AZURE_APPCONFIG_ENDPOINT` empty), the hardcoded defaults stand
and a WARNING is logged. The defaults are deliberately conservative — a
config outage can only restrict access, never escalate it.

Dev bypass: when `DEV_AUTH_BYPASS=true` the filter functions short-circuit
and return everything, so local development keeps the pre-RBAC experience
described in CLAUDE.md.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)


class RoleAccess(TypedDict):
    skills: list[str]
    tools: list[str]


# Skill slugs match the directory names under backend/kb_data/skills/shared/.
# Tool names match the `name` attribute of each Tool subclass (see TOOL_REGISTRY).
#
# Tier intent (see DESIGN.md §5 entry 2026-05-17 "Consolidate shared skills"):
#   __default__  → Default skill only, read-only tools
#   engineer     → Default + Azure Engineer, full Azure execute access
#   architect    → all 5 skills including both drawio specialists
DEFAULT_ACCESS_MAP: dict[str, RoleAccess] = {
    "__default__": {
        "skills": ["kb-searcher"],
        "tools": [
            "read_kb_file",
            "search_kb",
            "search_kb_hybrid",
            "search_kb_semantic",
            "fetch_ms_docs",
            "search_stack_overflow",
            "search_github",
            "search_azure_updates",
            "web_search",
            "web_fetch",
            "az_resource_graph",
            "az_cost_query",
            "az_monitor_logs",
            "az_advisor",
            "az_policy_check",
            "network_test",
        ],
    },
    "engineer": {
        "skills": ["kb-searcher", "chat-with-kb"],
        "tools": [
            # Default tier
            "read_kb_file",
            "search_kb",
            "search_kb_hybrid",
            "search_kb_semantic",
            "fetch_ms_docs",
            "search_stack_overflow",
            "search_github",
            "search_azure_updates",
            "web_search",
            "web_fetch",
            "az_resource_graph",
            "az_cost_query",
            "az_monitor_logs",
            "az_advisor",
            "az_policy_check",
            "network_test",
            # Engineer additions (write / execute, approval-gated)
            "az_cli",
            "az_rest_api",
            "execute_script",
            "generate_file",
            "read_file",
            "az_devops",
        ],
    },
    "architect": {
        "skills": [
            "kb-searcher",
            "chat-with-kb",
            "architect",
            "drawio-diagrammer",
        ],
        "tools": [
            # Default + Engineer tiers
            "read_kb_file",
            "search_kb",
            "search_kb_hybrid",
            "search_kb_semantic",
            "fetch_ms_docs",
            "search_stack_overflow",
            "search_github",
            "search_azure_updates",
            "web_search",
            "web_fetch",
            "az_resource_graph",
            "az_cost_query",
            "az_monitor_logs",
            "az_advisor",
            "az_policy_check",
            "network_test",
            "az_cli",
            "az_rest_api",
            "execute_script",
            "generate_file",
            "read_file",
            "az_devops",
            # Architect diagram tools (inline drawio-from-python flow)
            "validate_drawio",
            "ask_user",
            "render_drawio",
            "generate_drawio_from_python",
            # drawio-diagrammer specialist (hand-written XML + per-cell patch)
            "patch_drawio_cell",
        ],
    },
}

# Sentinel key for users with no Entra App Role assigned.
NO_ROLE_KEY = "__default__"

# Mutable process-local copy of the access map. init_rbac() may replace it
# with a version loaded from Azure App Configuration.
_ACCESS_MAP: dict[str, RoleAccess] = {k: dict(v) for k, v in DEFAULT_ACCESS_MAP.items()}  # type: ignore[misc]


def _is_dev_bypass() -> bool:
    return get_settings().DEV_AUTH_BYPASS


def _resolve_roles(user: User) -> list[str]:
    """Return the role keys that apply to `user` for lookup in the access map.

    If the user has no Entra App Roles assigned, fall back to the
    `__default__` sentinel. Unknown roles (present in the JWT but not in the
    access map) are silently ignored — only mapped roles contribute access.
    """
    if not user.roles:
        return [NO_ROLE_KEY]
    return user.roles


def allowed_skills_for(user: User) -> set[str] | None:
    """Skill slugs visible to `user`.

    Returns None to signal *unrestricted* (dev bypass). Callers should treat
    None as "no filtering — return everything".
    """
    if _is_dev_bypass():
        return None
    allowed: set[str] = set()
    for role in _resolve_roles(user):
        entry = _ACCESS_MAP.get(role)
        if entry:
            allowed.update(entry.get("skills", []))
    return allowed


def allowed_tools_for(user: User) -> set[str] | None:
    """Tool names `user` may invoke or include in a personal skill.

    Returns None to signal *unrestricted* (dev bypass).
    """
    if _is_dev_bypass():
        return None
    allowed: set[str] = set()
    for role in _resolve_roles(user):
        entry = _ACCESS_MAP.get(role)
        if entry:
            allowed.update(entry.get("tools", []))
    return allowed


def is_skill_allowed(user: User, skill_slug: str) -> bool:
    allowed = allowed_skills_for(user)
    return allowed is None or skill_slug in allowed


def is_tool_allowed(user: User, tool_name: str) -> bool:
    allowed = allowed_tools_for(user)
    return allowed is None or tool_name in allowed


def reset_access_map_for_tests() -> None:
    """Restore _ACCESS_MAP to the hardcoded defaults.

    Used by test fixtures that mutate the map.
    """
    global _ACCESS_MAP
    _ACCESS_MAP = {k: dict(v) for k, v in DEFAULT_ACCESS_MAP.items()}  # type: ignore[misc]


def _validate_map_shape(parsed: object) -> dict[str, RoleAccess]:
    """Validate that `parsed` is the expected role-access shape.

    Expected shape:
        {
          "<role>": {"skills": ["<slug>", ...], "tools": ["<name>", ...]},
          ...
        }

    Raises ValueError on anything else.
    """
    if not isinstance(parsed, dict):
        raise ValueError("Top-level must be a dict of role -> access")
    out: dict[str, RoleAccess] = {}
    for role, value in parsed.items():
        if not isinstance(role, str) or not role:
            raise ValueError("Role names must be non-empty strings")
        if not isinstance(value, dict):
            raise ValueError(f"Role {role!r}: value must be a dict with skills and tools")
        skills = value.get("skills")
        tools = value.get("tools")
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            raise ValueError(f"Role {role!r}: skills must be a list of strings")
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError(f"Role {role!r}: tools must be a list of strings")
        out[role] = {"skills": list(skills), "tools": list(tools)}  # type: ignore[typeddict-item]
    if NO_ROLE_KEY not in out:
        # Without a __default__ entry, no-role users would see nothing.
        # That's a valid choice but we want it to be explicit, not accidental.
        raise ValueError(f"Missing required role key {NO_ROLE_KEY!r}")
    return out


async def init_rbac() -> None:
    """Load the role access map from Azure App Configuration if configured.

    Called from main.py's lifespan handler at startup. On any failure (no
    endpoint set, unreachable, malformed JSON, wrong shape), the hardcoded
    `DEFAULT_ACCESS_MAP` remains in effect and a WARNING is logged.
    """
    global _ACCESS_MAP
    settings = get_settings()
    endpoint = settings.AZURE_APPCONFIG_ENDPOINT.strip()
    key = settings.AZURE_APPCONFIG_ROLE_KEY.strip()

    if not endpoint:
        logger.info(
            "AZURE_APPCONFIG_ENDPOINT not set; using hardcoded role access defaults "
            "(%d roles)", len(DEFAULT_ACCESS_MAP),
        )
        return

    try:
        # Import here so the sync path and tests can avoid the SDK dependency
        # if App Config is not configured.
        from azure.appconfiguration.aio import AzureAppConfigurationClient
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as e:
        logger.warning(
            "azure-appconfiguration / azure-identity not installed; using defaults: %s",
            e,
        )
        return

    try:
        async with DefaultAzureCredential() as credential:
            async with AzureAppConfigurationClient(
                base_url=endpoint, credential=credential
            ) as client:
                setting = await client.get_configuration_setting(key=key)
                raw = setting.value or ""
        parsed = json.loads(raw)
        validated = _validate_map_shape(parsed)
        _ACCESS_MAP = validated
        logger.info(
            "Loaded role access map from App Config (endpoint=%s, key=%s, roles=%s)",
            endpoint, key, sorted(validated.keys()),
        )
    except Exception as e:
        logger.warning(
            "Failed to load role access map from App Config (endpoint=%s, key=%s); "
            "using hardcoded defaults. Reason: %s",
            endpoint, key, str(e).split("\n")[0],
        )
