"""Azure bundle — Azure platform tools (DESIGN.md §5 2026-06-05).

Registers the bundle manifest so core discovers it by directory scan without
naming it. Tool modules in this package are loaded by ``init_tools()`` only when
``TOOL_BUNDLE_AZURE_ENABLED`` is true; the hooks below let the orchestrator pull
this bundle's prompt contributions and react to its auth errors generically.
"""

from app.tools.bundle import Bundle, register_bundle


def _prompt_fragment() -> str:
    """Static Azure tool-hierarchy guidance (cache-prefix safe — no per-request
    data). Moved here from the orchestrator's hardcoded system prompt so core
    carries no Azure tool names."""
    return (
        "## Tool hierarchy\n"
        "Always prefer tools in this order when querying Azure resources:\n"
        "1. **`az_resource_graph`** (KQL) — fastest, read-only, no approval. Use first for resource queries.\n"
        "2. **`az_cost_query`** — cost/usage data. No approval.\n"
        "3. **`az_monitor_logs`** — Log Analytics KQL queries. No approval.\n"
        "4. **`az_advisor`** / **`az_policy_check`** — recommendations and compliance. No approval.\n"
        "5. **`az_cli`** — general Azure operations. Requires approval for mutations.\n"
        "6. **`az_rest_api`** — direct ARM REST calls. GET=no approval, mutations=approval.\n"
        "7. **`az_devops`** — Azure DevOps pipelines/PRs/builds. Read=no approval, mutations=approval.\n"
        "8. **`execute_script`** — run a .ps1/.sh script that already exists under output/scripts/. Always requires approval. Write the script with `generate_file` first.\n\n"
        "Other tools:\n"
        "- **`network_test`** — DNS/port checks, NSG rules. No approval.\n"
        "- **`generate_file`** — Write files to output/ sandbox. No approval.\n"
        "- **`web_fetch`** — Fetch web page content. No approval.\n"
        "Before running any command, call `fetch_ms_docs` to verify the correct syntax.\n\n"
    )


def _context_prompt() -> str:
    """Dynamic per-turn Azure CLI login state for the system prompt."""
    from bundles.azure.az_login_check import get_az_context_prompt

    return get_az_context_prompt()


def _on_tool_error(result: str) -> None:
    """On an az auth error, clear the cached login state so the next attempt
    re-checks instead of trusting a stale 'logged in' cache."""
    if "az login" in result or "not logged in" in result.lower():
        from bundles.azure.az_login_check import clear_login_cache

        clear_login_cache()


register_bundle(
    Bundle(
        name="azure",
        config_flag="TOOL_BUNDLE_AZURE_ENABLED",
        prompt_fragment=_prompt_fragment,
        context_prompt=_context_prompt,
        on_tool_error=_on_tool_error,
    )
)
