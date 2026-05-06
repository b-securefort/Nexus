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


class TestToolRegistry:
    def test_init_tools_registers_all(self):
        init_tools()
        assert len(TOOL_REGISTRY) == 24
        expected = {
            "read_kb_file", "search_kb", "search_kb_semantic", "fetch_ms_docs", "run_shell",
            "az_cli", "az_resource_graph", "az_cost_query", "az_monitor_logs",
            "az_rest_api", "generate_file", "az_devops", "az_policy_check",
            "az_advisor", "network_test", "diagram_gen", "web_fetch",
            "read_learnings", "update_learnings", "validate_drawio",
            "search_stack_overflow", "search_github", "search_azure_updates", "web_search",
        }
        assert set(TOOL_REGISTRY.keys()) == expected

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
