"""Capability-matrix guard for the bundle-decoupling migration (DESIGN.md §5
2026-06-05).

Phase A moved the orchestrator's hardcoded Azure tool name-sets onto per-tool
capability attributes. The orchestrator now reads those attributes (via
`_tool_has` / `_tool_result_limit`) and the old constants are deleted, so this
test pins the expected capability matrix directly as the durable source of
truth: if a tool's capability flags drift, this fails.
"""

from app.tools.base import TOOL_REGISTRY, AzureToolBase, init_tools

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


def test_requires_credentials_matches_azuretoolbase_isinstance():
    # Reproduces the orchestrator's old `isinstance(tool, AzureToolBase)` check.
    # NB AzCliTool does NOT inherit AzureToolBase, so az_cli is intentionally
    # False — matching the prior ARM-preflight behaviour exactly.
    for name, tool in TOOL_REGISTRY.items():
        assert tool.requires_credentials == isinstance(tool, AzureToolBase), name
