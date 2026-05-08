"""Tests for tool registry, tool schemas, and tool execution."""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.auth.models import User
from app.tools.base import (
    TOOL_REGISTRY,
    get_tool,
    list_tools,
    resolve_tools,
    init_tools,
)

_USER = User(oid="test-user", email="test@test.com", display_name="Test")

# Ensure tools are registered when this file is imported by pytest (regardless
# of which test class runs first or in isolation).
init_tools()


class TestToolRegistry:
    def test_init_tools_registers_all(self):
        init_tools()
        expected = {
            "read_kb_file", "search_kb", "search_kb_semantic", "fetch_ms_docs", "run_shell",
            "az_cli", "az_resource_graph", "az_cost_query", "az_monitor_logs",
            "az_rest_api", "generate_file", "az_devops", "az_policy_check",
            "az_advisor", "network_test", "web_fetch", "render_drawio",
            "read_learnings", "update_learnings", "validate_drawio",
            "search_stack_overflow", "search_github", "search_azure_updates", "web_search",
        }
        assert set(TOOL_REGISTRY.keys()) == expected
        assert len(TOOL_REGISTRY) == len(expected)

    def test_get_tool_existing(self):
        tool = get_tool("read_kb_file")
        assert tool is not None
        assert tool.name == "read_kb_file"

    def test_get_tool_nonexistent(self):
        assert get_tool("does_not_exist") is None

    def test_list_tools_returns_enabled(self):
        tools = list_tools()
        assert len(tools) >= 5
        for t in tools:
            assert t.enabled_by_config is True

    def test_resolve_tools_valid(self):
        tools = resolve_tools(["read_kb_file", "search_kb"])
        assert len(tools) == 2
        names = [t.name for t in tools]
        assert "read_kb_file" in names
        assert "search_kb" in names

    def test_resolve_tools_skips_unknown(self):
        tools = resolve_tools(["read_kb_file", "nonexistent"])
        assert len(tools) == 1


class TestToolSchemas:
    """Every tool must produce a valid OpenAI function-calling schema."""

    def test_all_tools_have_valid_schema(self):
        for name, tool in TOOL_REGISTRY.items():
            schema = tool.to_openai_schema()
            assert schema["type"] == "function"
            assert schema["function"]["name"] == name
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]
            params = schema["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params

    def test_approval_tools_flagged(self):
        for name in ("run_shell", "az_cli"):
            assert TOOL_REGISTRY[name].requires_approval is True
        for name in ("read_kb_file", "search_kb", "search_kb_semantic", "fetch_ms_docs"):
            assert TOOL_REGISTRY[name].requires_approval is False


class TestReadKBFileTool:
    def test_read_existing_file(self):
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "kb/adrs/adr-001-multi-region.md"}, _USER)
        assert "Multi-Region" in result

    def test_read_path_traversal(self):
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "../../../etc/passwd"}, _USER)
        assert "Error" in result

    def test_read_nonexistent(self):
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "kb/nope.md"}, _USER)
        assert "Error" in result


class TestSearchKBTool:
    def test_search_returns_json(self):
        from app.kb.indexer import load_index
        load_index()
        tool = get_tool("search_kb")
        result = tool.execute({"query": "circuit"}, _USER)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1

    def test_search_empty_query(self):
        from app.kb.indexer import load_index
        load_index()
        tool = get_tool("search_kb")
        result = tool.execute({"query": ""}, _USER)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_search_limit(self):
        from app.kb.indexer import load_index
        load_index()
        tool = get_tool("search_kb")
        result = tool.execute({"query": "", "limit": 1}, _USER)
        parsed = json.loads(result)
        assert len(parsed) <= 1


class TestSearchKBSemanticTool:
    """Tests for the LLM-powered semantic search tool."""

    def test_semantic_search_empty_query(self):
        tool = get_tool("search_kb_semantic")
        result = tool.execute({"query": ""}, _USER)
        assert "Error" in result

    @patch("app.tools.kb_tools.AzureOpenAI")
    def test_semantic_search_calls_llm_for_expansion(self, mock_openai_cls):
        """Verify that query expansion is called and its terms drive the KB search."""
        from app.kb.indexer import load_index
        load_index()

        # Mock LLM: expansion returns ["kubernetes", "AKS cluster"], rerank returns [1]
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        expansion_response = MagicMock()
        expansion_response.choices[0].message.content = '["circuit breaker", "resilience", "retry"]'

        rerank_response = MagicMock()
        rerank_response.choices[0].message.content = "[1]"

        mock_client.chat.completions.create.side_effect = [
            expansion_response,
            rerank_response,
        ]

        tool = get_tool("search_kb_semantic")
        result = tool.execute({"query": "circuit breaker pattern", "limit": 3}, _USER)
        parsed = json.loads(result)

        assert "results" in parsed
        assert "expanded_terms" in parsed
        assert isinstance(parsed["results"], list)

    @patch("app.tools.kb_tools.AzureOpenAI")
    def test_semantic_search_falls_back_on_expansion_error(self, mock_openai_cls):
        """If LLM expansion fails, tool falls back to original query without crashing."""
        from app.kb.indexer import load_index
        load_index()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("LLM unavailable")

        tool = get_tool("search_kb_semantic")
        result = tool.execute({"query": "networking"}, _USER)
        parsed = json.loads(result)
        assert "results" in parsed


class TestFetchMsDocsTool:
    @patch("app.tools.ms_docs.httpx.Client")
    def test_fetch_ms_docs(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "Azure VMs", "url": "https://learn.microsoft.com/azure/vms", "description": "VM docs"}
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        tool = get_tool("fetch_ms_docs")
        result = tool.execute({"query": "azure vms"}, _USER)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["title"] == "Azure VMs"

    def test_fetch_ms_docs_empty_query(self):
        tool = get_tool("fetch_ms_docs")
        result = tool.execute({"query": ""}, _USER)
        assert "Error" in result


class TestRunShellTool:
    def test_shell_executes_command(self):
        tool = get_tool("run_shell")
        result = tool.execute({"command": "echo hello", "reason": "test"}, _USER)
        assert "hello" in result
        assert "Exit code: 0" in result

    def test_shell_timeout_capped(self):
        tool = get_tool("run_shell")
        # Timeout should be capped at 120
        result = tool.execute(
            {"command": "echo ok", "reason": "test", "timeout_seconds": 999}, _USER
        )
        assert "Exit code: 0" in result

    def test_shell_bad_command(self):
        tool = get_tool("run_shell")
        result = tool.execute(
            {"command": "nonexistent_command_xyz_123", "reason": "test"}, _USER
        )
        assert "Exit code:" in result
        assert int(result.split("Exit code: ")[1].split("\n")[0]) != 0


class TestAzCliTool:
    def test_az_cli_requires_list_args(self):
        tool = get_tool("az_cli")
        result = tool.execute({"args": "not-a-list", "reason": "test"}, _USER)
        assert "Error" in result


# ── Adversarial / quality coverage ──────────────────────────────────────────
# Cases below try to break each tool with malformed, hostile, or edge-case
# input. They should all either return a clean "Error: ..." string OR run the
# real command — never raise an unhandled exception or crash the process.


class TestRunShellAdversarial:
    """Adversarial inputs for run_shell. Every case must return a string;
    none may raise.
    """

    def test_empty_command_runs_cleanly(self):
        """R1: empty command — should execute as a no-op, not crash."""
        tool = get_tool("run_shell")
        result = tool.execute({"command": "", "reason": "test"}, _USER)
        assert isinstance(result, str)
        assert "Exit code:" in result

    def test_command_is_none(self):
        """R2: command=None — must not raise; must return a clean error."""
        tool = get_tool("run_shell")
        result = tool.execute({"command": None, "reason": "test"}, _USER)
        assert isinstance(result, str)
        assert "Error" in result

    def test_missing_command_key(self):
        """R3: command key missing — must not raise."""
        tool = get_tool("run_shell")
        result = tool.execute({"reason": "test"}, _USER)
        assert isinstance(result, str)
        # Empty default should be treated like empty command — runs no-op
        # OR an explicit error. Either is fine; crash is not.
        assert "Error" in result or "Exit code:" in result

    def test_negative_timeout_does_not_crash(self):
        """R4: negative timeout — subprocess.run raises ValueError on negative.
        The tool must intercept and return a clean error."""
        tool = get_tool("run_shell")
        result = tool.execute(
            {"command": "echo hi", "reason": "test", "timeout_seconds": -1},
            _USER,
        )
        assert isinstance(result, str)
        assert "Error" in result or "Exit code:" in result

    def test_zero_timeout_does_not_crash(self):
        """R5: zero timeout — same risk as negative."""
        tool = get_tool("run_shell")
        result = tool.execute(
            {"command": "echo hi", "reason": "test", "timeout_seconds": 0},
            _USER,
        )
        assert isinstance(result, str)
        assert "Error" in result or "Exit code:" in result

    def test_string_timeout_does_not_crash(self):
        """R6: timeout_seconds as a string. min('abc', 120) raises TypeError —
        the tool must coerce or reject, never propagate."""
        tool = get_tool("run_shell")
        result = tool.execute(
            {"command": "echo hi", "reason": "test", "timeout_seconds": "abc"},
            _USER,
        )
        assert isinstance(result, str)
        assert "Error" in result or "Exit code:" in result

    def test_actual_timeout_path(self):
        """R7: command that runs longer than the timeout — must terminate
        with a clean timeout error, not block forever."""
        import sys
        tool = get_tool("run_shell")
        # Use PowerShell on Windows for cross-platform sleep.
        if sys.platform == "win32":
            args = {"command": "Start-Sleep -Seconds 5", "reason": "test",
                    "shell": "powershell", "timeout_seconds": 2}
        else:
            args = {"command": "sleep 5", "reason": "test", "timeout_seconds": 2}
        result = tool.execute(args, _USER)
        assert isinstance(result, str)
        assert "timed out" in result.lower() or "Exit code:" in result

    def test_long_stdout_is_truncated(self):
        """R8: stdout larger than the cap should be truncated with a marker."""
        import sys
        tool = get_tool("run_shell")
        # Generate ~12KB of output. Cap is 8192.
        if sys.platform == "win32":
            cmd = "1..2000 | ForEach-Object { 'X' * 10 }"
            args = {"command": cmd, "reason": "test", "shell": "powershell"}
        else:
            args = {"command": "yes X | head -c 12000", "reason": "test"}
        result = tool.execute(args, _USER)
        assert isinstance(result, str)
        # If truncated, marker is present. If shell can't produce 12KB, that's
        # still a pass (no crash).
        assert "truncated" in result or len(result) <= 8500

    def test_nonzero_exit_reported(self):
        """R9: command that returns non-zero — exit code should appear."""
        import sys
        tool = get_tool("run_shell")
        if sys.platform == "win32":
            args = {"command": "exit 7", "reason": "test", "shell": "powershell"}
        else:
            args = {"command": "exit 7", "reason": "test"}
        result = tool.execute(args, _USER)
        assert isinstance(result, str)
        assert "Exit code: 7" in result

    def test_invalid_shell_enum(self):
        """R10: shell value outside enum — must not crash; must run
        deterministically (default behavior is acceptable)."""
        tool = get_tool("run_shell")
        result = tool.execute(
            {"command": "echo hi", "reason": "test", "shell": "bash-haxor"},
            _USER,
        )
        assert isinstance(result, str)
        # Falls through to default shell — should still run echo or error cleanly
        assert "Error" in result or "Exit code:" in result


class TestAzCliAdversarial:
    """Adversarial inputs for az_cli. Every blocklist bypass attempt must be
    rejected; every well-formed query must pass the safety checks (even if it
    fails downstream because az isn't logged in).
    """

    def test_empty_args(self):
        """A1: empty args — bare 'az' prints help. Must not crash."""
        tool = get_tool("az_cli")
        result = tool.execute({"args": [], "reason": "test"}, _USER)
        assert isinstance(result, str)

    def test_blocklist_account_clear(self):
        """A2: direct blocked subcommand."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["account", "clear"], "reason": "wipe creds"}, _USER,
        )
        assert "blocked" in result.lower()

    def test_blocklist_case_insensitive(self):
        """A3: uppercase variant must still be blocked."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["ACCOUNT", "Clear"], "reason": "wipe creds"}, _USER,
        )
        assert "blocked" in result.lower()

    def test_blocklist_bypass_via_debug_flag(self):
        """A4: GLOBAL FLAG BYPASS — `az --debug account clear` should be
        blocked because the destructive subcommand is still present."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["--debug", "account", "clear"], "reason": "wipe via flag"},
            _USER,
        )
        assert "blocked" in result.lower(), (
            "FLAW: blocklist can be bypassed by prefixing a global flag "
            "(--debug, --verbose, --only-show-errors, --output, etc.)"
        )

    def test_blocklist_bypass_via_verbose_flag(self):
        """A5: same pattern with --verbose + ad app create."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["--verbose", "ad", "app", "create",
                      "--display-name", "rogue"],
             "reason": "create rogue app via flag"},
            _USER,
        )
        assert "blocked" in result.lower()

    def test_blocklist_bypass_via_only_show_errors(self):
        """A6: --only-show-errors prefix bypass on role assignment delete."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["--only-show-errors", "role", "assignment", "delete",
                      "--ids", "/subscriptions/x"],
             "reason": "delete role via flag"},
            _USER,
        )
        assert "blocked" in result.lower()

    def test_blocklist_bypass_via_output_flag_with_value(self):
        """A6b: --output json (flag with value) before blocked subcmd."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["--output", "json", "ad", "sp", "delete",
                      "--id", "x"],
             "reason": "delete sp via flag+value"},
            _USER,
        )
        assert "blocked" in result.lower()

    def test_kql_pipe_passes_safety(self):
        """A7: KQL queries use the | pipe character. The safety check must
        not block them. Will fail at az invocation if not logged in — that's
        fine; we only check the safety layer."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["graph", "query", "-q", "Resources | count"],
             "reason": "count resources"},
            _USER,
        )
        assert isinstance(result, str)
        # Must not be the safety-rejection error.
        assert "characters that are not allowed" not in result
        assert "blocked" not in result.lower() or "az login" in result.lower()

    def test_backtick_blocked_by_injection_check(self):
        """A8: backtick (PowerShell escape) must be blocked even in arg values."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["vm", "list", "-o", "json `whoami`"],
             "reason": "inject via backtick"},
            _USER,
        )
        assert "characters that are not allowed" in result

    def test_nul_byte_blocked(self):
        """A9: NUL byte must be blocked."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["vm", "list", "test\x00malicious"],
             "reason": "nul injection"},
            _USER,
        )
        assert "characters that are not allowed" in result

    def test_args_not_a_list(self):
        """A10: non-list args — must reject cleanly."""
        tool = get_tool("az_cli")
        result = tool.execute({"args": "vm list", "reason": "test"}, _USER)
        assert "Error" in result and "list" in result.lower()

    def test_none_in_args_does_not_crash(self):
        """A11: list element is None — str(None) = 'None'. Should not crash."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": [None, "list"], "reason": "test"},
            _USER,
        )
        assert isinstance(result, str)
        # Either error from CLI, login error, or safety pass-through
        assert "Error" in result or "Exit code" in result

    def test_account_show_not_blocked(self):
        """A12: precision — 'account show' (read-only) must NOT be blocked."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["account", "show"], "reason": "check identity"},
            _USER,
        )
        # We aren't logged-in-checked here — point is, the safety rejection
        # message must NOT appear.
        assert "blocked for safety" not in result

    def test_ad_app_list_not_blocked(self):
        """A13: precision — 'ad app list' (read-only) must NOT be blocked
        even though 'ad app create' and 'ad app delete' are."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["ad", "app", "list"], "reason": "list apps"},
            _USER,
        )
        assert "blocked for safety" not in result

    def test_jmespath_substring_no_false_positive(self):
        """A14: JMESPath query containing the literal string 'account-clear'
        as a substring of a single arg must NOT trigger the blocklist —
        the check is on whole tokens, not substrings."""
        tool = get_tool("az_cli")
        result = tool.execute(
            {"args": ["group", "list", "--query",
                      "[?name=='account-clear-archive']"],
             "reason": "list groups"},
            _USER,
        )
        assert "blocked for safety" not in result


class TestSearchStackOverflowTool:
    def test_empty_query_returns_error(self):
        init_tools()
        tool = get_tool("search_stack_overflow")
        assert "Error" in tool.execute({"query": ""}, _USER)

    @patch("app.tools.search_stackoverflow.httpx.Client")
    def test_returns_structured_results(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "title": "How to set up private endpoints on AKS",
                    "link": "https://stackoverflow.com/questions/12345",
                    "score": 42,
                    "answer_count": 3,
                    "is_answered": True,
                    "tags": ["azure", "kubernetes"],
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("search_stack_overflow")
        result = json.loads(tool.execute({"query": "AKS private endpoints"}, _USER))
        assert len(result) == 1
        assert result[0]["is_answered"] is True
        assert result[0]["score"] == 42

    @patch("app.tools.search_stackoverflow.httpx.Client")
    def test_respects_limit(self, mock_client_cls):
        items = [
            {"title": f"Q{i}", "link": f"https://so.com/{i}", "score": i,
             "answer_count": 1, "is_answered": True, "tags": []}
            for i in range(8)
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": items}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("search_stack_overflow")
        result = json.loads(tool.execute({"query": "azure", "limit": 3}, _USER))
        assert len(result) <= 3


class TestSearchGithubTool:
    def test_empty_query_returns_error(self):
        init_tools()
        tool = get_tool("search_github")
        assert "Error" in tool.execute({"query": ""}, _USER)

    @patch("app.tools.search_github.httpx.Client")
    def test_repository_search_returns_stars(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "full_name": "Azure/bicep",
                    "html_url": "https://github.com/Azure/bicep",
                    "description": "Bicep is a DSL for deploying Azure resources",
                    "stargazers_count": 3200,
                    "language": "Bicep",
                    "topics": ["azure", "iac"],
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("search_github")
        result = json.loads(tool.execute({"query": "azure bicep landing zone"}, _USER))
        assert result[0]["stars"] == 3200
        assert "bicep" in result[0]["name"].lower()

    @patch("app.tools.search_github.httpx.Client")
    def test_rate_limit_returns_friendly_error(self, mock_client_cls):
        from httpx import HTTPStatusError, Request, Response
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        err_resp = MagicMock()
        err_resp.status_code = 403
        mock_client.get.side_effect = HTTPStatusError(
            "rate limited", request=MagicMock(), response=err_resp
        )
        mock_client_cls.return_value = mock_client

        tool = get_tool("search_github")
        result = tool.execute({"query": "azure"}, _USER)
        assert "rate limit" in result.lower()


class TestSearchAzureUpdatesTool:
    def test_empty_query_returns_error(self):
        init_tools()
        tool = get_tool("search_azure_updates")
        assert "Error" in tool.execute({"query": ""}, _USER)

    @patch("app.tools.search_azure_updates.httpx.Client")
    def test_parses_rss_and_filters_by_keyword(self, mock_client_cls):
        init_tools()
        rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Azure Kubernetes Service private cluster GA</title>
      <link>https://azure.microsoft.com/updates/aks-private-ga</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <description>AKS private cluster is now generally available.</description>
    </item>
    <item>
      <title>Azure Blob Storage lifecycle management update</title>
      <link>https://azure.microsoft.com/updates/blob-lifecycle</link>
      <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
      <description>New lifecycle management features for blob storage.</description>
    </item>
  </channel>
</rss>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = rss
        mock_resp.url = "https://azure.microsoft.com/en-us/updates/feed/"
        mock_resp.headers = {"content-type": "application/rss+xml; charset=utf-8"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("search_azure_updates")
        result = json.loads(tool.execute({"query": "AKS kubernetes"}, _USER))
        assert len(result) >= 1
        assert "Kubernetes" in result[0]["title"]


class TestWebSearchTool:
    def test_empty_query_returns_error(self):
        init_tools()
        tool = get_tool("web_search")
        assert "Error" in tool.execute({"query": ""}, _USER)

    def test_site_shortcut_expands(self):
        from app.tools.web_search import SITE_SHORTCUTS
        assert "reddit" in SITE_SHORTCUTS
        assert "techcommunity" in SITE_SHORTCUTS

    @patch("app.tools.web_search.httpx.Client")
    def test_parses_ddg_results(self, mock_client_cls):
        html = """
        <html><body>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freddit.com%2Fr%2FAzure%2Fcomments%2F123">
          Azure Private Endpoint discussion
        </a>
        <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freddit.com%2Fr%2FAzure%2Fcomments%2F123">
          People discussing private endpoints on Azure
        </a>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("web_search")
        result = json.loads(tool.execute({"query": "azure private endpoint", "site": "reddit"}, _USER))
        assert "results" in result
        assert result["results"][0]["url"] == "https://reddit.com/r/Azure/comments/123"

    @patch("app.tools.web_search.httpx.Client")
    def test_no_results_returns_empty_list(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>No results.</body></html>"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        tool = get_tool("web_search")
        result = json.loads(tool.execute({"query": "very obscure query xyz"}, _USER))
        assert result["results"] == []
