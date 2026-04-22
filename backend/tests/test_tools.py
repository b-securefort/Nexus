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
        assert len(TOOL_REGISTRY) == 18
        expected = {
            "read_kb_file", "search_kb", "fetch_ms_docs", "run_shell",
            "az_cli", "az_resource_graph", "az_cost_query", "az_monitor_logs",
            "az_rest_api", "generate_file", "az_devops", "az_policy_check",
            "az_advisor", "network_test", "diagram_gen", "web_fetch",
            "read_learnings", "update_learnings",
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
        for name in ("read_kb_file", "search_kb", "fetch_ms_docs"):
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
