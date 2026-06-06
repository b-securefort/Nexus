"""Capability-matrix guard for the bundle-decoupling migration (DESIGN.md §5
2026-06-05).

Phase A moved the orchestrator's hardcoded Azure tool name-sets onto per-tool
capability attributes. The orchestrator now reads those attributes (via
`_tool_has` / `_tool_result_limit`) and the old constants are deleted, so this
test pins the expected capability matrix directly as the durable source of
truth: if a tool's capability flags drift, this fails.
"""

from app.tools.base import TOOL_REGISTRY, init_tools
from bundles.azure._az_base import AzureToolBase

# Ensure the full registry (generic + azure bundle) is populated.
init_tools()


def _names_where(predicate) -> set[str]:
    return {name for name, tool in TOOL_REGISTRY.items() if predicate(tool)}


def test_retry_eligible_tools():
    # Multi-strategy retry escalation (was orchestrator _COMMAND_TOOLS).
    assert _names_where(lambda t: t.retry_eligible) == {
        "az_cli", "execute_script", "az_resource_graph",
    }


def test_learning_eligible_tools():
    # Success-after-failure learning capture (was _LEARNING_ELIGIBLE_TOOLS) —
    # a superset of retry-eligible.
    assert _names_where(lambda t: t.learning_eligible) == {
        "az_cli", "execute_script", "az_resource_graph",
        "az_rest_api", "az_devops",
        "generate_drawio_from_python", "generate_python_diagram",
    }
    # Invariant: every retry-eligible tool is also learning-eligible.
    assert _names_where(lambda t: t.retry_eligible) <= _names_where(
        lambda t: t.learning_eligible
    )


def test_result_limit_tools():
    # In-prompt size caps (was _TOOL_RESULT_LIMITS).
    derived = {
        name: tool.result_limit
        for name, tool in TOOL_REGISTRY.items()
        if tool.result_limit is not None
    }
    assert derived == {
        "az_cli": 4_000,
        "az_resource_graph": 4_000,
        "execute_script": 4_000,
        "read_kb_file": 6_000,
        "read_file": 6_000,
        "search_kb_hybrid": 4_000,
    }


def test_is_diagram_tool_tools():
    # Drawio-family tools (was _DRAWIO_TOOLS).
    assert _names_where(lambda t: t.is_diagram_tool) == {
        "render_drawio", "validate_drawio", "generate_file", "patch_drawio_cell",
    }


def test_config_flag_matrix():
    # Each tool declares the Settings attribute that toggles it (was the
    # init_tools `config_mapping` table). Pins the mapping, including the two
    # fixes for the previously-dead `ms_docs` / `search_stackoverflow` keys.
    derived = {
        name: tool.config_flag
        for name, tool in TOOL_REGISTRY.items()
        if tool.config_flag is not None
    }
    assert derived == {
        "search_kb_semantic": "TOOL_SEARCH_SEMANTIC_ENABLED",
        "fetch_ms_docs": "TOOL_MS_DOCS_ENABLED",
        "execute_script": "TOOL_SHELL_ENABLED",
        "az_cli": "TOOL_AZ_CLI_ENABLED",
        "az_resource_graph": "TOOL_AZ_CLI_ENABLED",
        "az_cost_query": "TOOL_AZ_COST_ENABLED",
        "az_monitor_logs": "TOOL_AZ_MONITOR_ENABLED",
        "az_rest_api": "TOOL_AZ_REST_ENABLED",
        "generate_file": "TOOL_GENERATE_FILE_ENABLED",
        "validate_drawio": "TOOL_VALIDATE_DRAWIO_ENABLED",
        "render_drawio": "TOOL_RENDER_DRAWIO_ENABLED",
        "generate_python_diagram": "TOOL_PYTHON_DIAGRAM_ENABLED",
        "generate_drawio_from_python": "TOOL_DRAWIO_FROM_PYTHON_ENABLED",
        "az_devops": "TOOL_AZ_DEVOPS_ENABLED",
        "az_policy_check": "TOOL_AZ_POLICY_ENABLED",
        "az_advisor": "TOOL_AZ_ADVISOR_ENABLED",
        "network_test": "TOOL_NETWORK_TEST_ENABLED",
        "web_fetch": "TOOL_WEB_FETCH_ENABLED",
        "search_stack_overflow": "TOOL_SEARCH_STACKOVERFLOW_ENABLED",
        "search_github": "TOOL_SEARCH_GITHUB_ENABLED",
        "search_azure_updates": "TOOL_SEARCH_AZURE_UPDATES_ENABLED",
        "web_search": "TOOL_WEB_SEARCH_ENABLED",
    }


def test_every_config_flag_is_a_real_setting():
    # Guards against the dead-key class of bug (a config_flag naming a Settings
    # attribute that doesn't exist would silently fail-open to enabled).
    from app.config import get_settings

    settings = get_settings()
    for name, tool in TOOL_REGISTRY.items():
        if tool.config_flag is not None:
            assert hasattr(settings, tool.config_flag), f"{name}: {tool.config_flag}"


def test_requires_credentials_matches_azuretoolbase_isinstance():
    # Reproduces the orchestrator's old `isinstance(tool, AzureToolBase)` check.
    # NB AzCliTool does NOT inherit AzureToolBase, so az_cli is intentionally
    # False — matching the prior ARM-preflight behaviour exactly.
    for name, tool in TOOL_REGISTRY.items():
        assert tool.requires_credentials == isinstance(tool, AzureToolBase), name


# ── Phase C: directory-scan loader + bundle manifest hooks ───────────────────


def test_azure_bundle_registered_via_directory_scan():
    from app.tools.bundle import BUNDLE_REGISTRY

    assert "azure" in BUNDLE_REGISTRY
    assert BUNDLE_REGISTRY["azure"].config_flag == "TOOL_BUNDLE_AZURE_ENABLED"


def test_core_does_not_import_bundles_by_name():
    # The whole point of Phase C: core composes prompts / handles auth errors by
    # looping the registry, never `from bundles.azure import ...`.
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        p for p in core.rglob("*.py")
        if "bundles.azure" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"core modules still name the azure bundle: {offenders}"


def test_bundle_prompt_fragment_carries_tool_hierarchy():
    from app.tools.bundle import bundle_prompt_fragments

    frag = bundle_prompt_fragments()
    assert "## Tool hierarchy" in frag
    assert "az_resource_graph" in frag


def test_bundle_context_prompt_carries_azure_state():
    from app.tools.bundle import bundle_context_prompts

    assert "Azure Context" in bundle_context_prompts()


def test_on_tool_error_clears_login_cache_only_on_auth_error():
    import bundles.azure.az_login_check as alc
    from app.tools.bundle import dispatch_tool_error

    # Non-auth error must NOT clear the cache.
    alc._cached_state = "sentinel"
    dispatch_tool_error("Error: resource not found")
    assert alc._cached_state == "sentinel"

    # Auth error must clear it so the next call re-checks.
    alc._cached_state = "sentinel"
    dispatch_tool_error("Error: please run az login --use-device-code")
    assert alc._cached_state is None
