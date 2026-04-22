"""Tests for new tools: az_login_check, az_cost_query, az_monitor_logs,
az_rest_api, generate_file, az_devops, az_policy_check, az_advisor,
network_test, diagram_gen, web_fetch.

All Azure CLI tools are tested with subprocess mocks so no real az calls are made.
"""

import json
import os
import shutil
import socket
import subprocess
import time

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from app.auth.models import User
from app.tools.base import get_tool, init_tools

_USER = User(oid="test-user", email="test@test.com", display_name="Test")

# Ensure tools are registered
init_tools()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_subprocess_success(stdout: str, stderr: str = ""):
    """Create a mock CompletedProcess with returncode 0."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = 0
    result.stdout = stdout
    result.stderr = stderr
    return result


def _mock_subprocess_failure(stderr: str, returncode: int = 1):
    """Create a mock CompletedProcess with non-zero returncode."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = ""
    result.stderr = stderr
    return result


def _patch_az_logged_in():
    """Patch require_az_login to always return None (logged in).
    Must be used as a stack of patches for each tool module that imports it.
    """
    # We can't just patch the source — each tool does 'from ... import require_az_login'
    # so we need to patch every usage site. Use a helper context manager.
    from contextlib import ExitStack
    modules = [
        "app.tools.az_cost",
        "app.tools.az_monitor",
        "app.tools.az_rest",
        "app.tools.az_policy",
        "app.tools.az_advisor",
        "app.tools.network_test",
        "app.tools.az_cli",
        "app.tools.az_resource_graph",
    ]
    stack = ExitStack()
    for mod in modules:
        stack.enter_context(patch(f"{mod}.require_az_login", return_value=None))
    return stack


def _patch_az_not_logged_in():
    """Patch require_az_login to return login error."""
    err = "Error: Azure CLI is not logged in.\nPlease run: az login --use-device-code"
    from contextlib import ExitStack
    modules = [
        "app.tools.az_cost",
        "app.tools.az_monitor",
        "app.tools.az_rest",
        "app.tools.az_policy",
        "app.tools.az_advisor",
        "app.tools.network_test",
        "app.tools.az_cli",
        "app.tools.az_resource_graph",
    ]
    stack = ExitStack()
    for mod in modules:
        stack.enter_context(patch(f"{mod}.require_az_login", return_value=err))
    return stack


# ══════════════════════════════════════════════════════════════════════════════
# az_login_check
# ══════════════════════════════════════════════════════════════════════════════

class TestAzLoginCheck:
    """Tests for the az_login_check module."""

    def setup_method(self):
        from app.tools.az_login_check import clear_login_cache
        clear_login_cache()

    @patch("app.tools.az_login_check.subprocess.run")
    def test_logged_in(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "id": "sub-123",
            "name": "My Subscription",
            "tenantId": "tenant-456",
            "user": {"name": "user@example.com", "type": "user"},
        }))

        state = check_az_login(force_refresh=True)
        assert state.logged_in is True
        assert state.user == "user@example.com"
        assert state.subscription_name == "My Subscription"
        assert state.subscription_id == "sub-123"
        assert state.tenant_id == "tenant-456"

    @patch("app.tools.az_login_check.subprocess.run")
    def test_not_logged_in(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_failure("Please run 'az login' to setup account.")
        state = check_az_login(force_refresh=True)
        assert state.logged_in is False
        assert "az login" in state.error

    @patch("app.tools.az_login_check.subprocess.run")
    def test_cache_hit(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "id": "sub-1", "name": "Sub", "tenantId": "t1",
            "user": {"name": "u@e.com", "type": "user"},
        }))

        check_az_login(force_refresh=True)
        assert mock_run.call_count == 1

        # Second call should use cache
        state2 = check_az_login()
        assert mock_run.call_count == 1  # NOT called again
        assert state2.logged_in is True

    @patch("app.tools.az_login_check.subprocess.run")
    def test_clear_cache(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "id": "s", "name": "S", "tenantId": "t",
            "user": {"name": "u", "type": "user"},
        }))

        check_az_login(force_refresh=True)
        clear_login_cache()
        check_az_login()
        assert mock_run.call_count == 2

    @patch("app.tools.az_login_check.subprocess.run")
    def test_require_az_login_ok(self, mock_run):
        from app.tools.az_login_check import require_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "id": "s", "name": "S", "tenantId": "t",
            "user": {"name": "u", "type": "user"},
        }))

        result = require_az_login()
        assert result is None

    @patch("app.tools.az_login_check.subprocess.run")
    def test_require_az_login_not_logged_in(self, mock_run):
        from app.tools.az_login_check import require_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_failure("Please run az login")
        result = require_az_login()
        assert result is not None
        assert "az login --use-device-code" in result

    @patch("app.tools.az_login_check.subprocess.run", side_effect=FileNotFoundError)
    def test_require_az_login_not_installed(self, mock_run):
        from app.tools.az_login_check import require_az_login, clear_login_cache
        clear_login_cache()

        result = require_az_login()
        assert result is not None
        assert "not installed" in result

    @patch("app.tools.az_login_check.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="az", timeout=15))
    def test_timeout(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        state = check_az_login(force_refresh=True)
        assert state.logged_in is False
        assert "timed out" in state.error

    @patch("app.tools.az_login_check.subprocess.run")
    def test_invalid_json(self, mock_run):
        from app.tools.az_login_check import check_az_login, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success("not json {{{")
        state = check_az_login(force_refresh=True)
        assert state.logged_in is False
        assert "parse" in state.error.lower()

    def test_context_prompt_unknown(self):
        from app.tools.az_login_check import get_az_context_prompt, clear_login_cache
        clear_login_cache()

        prompt = get_az_context_prompt()
        assert "Unknown" in prompt

    @patch("app.tools.az_login_check.subprocess.run")
    def test_context_prompt_logged_in(self, mock_run):
        from app.tools.az_login_check import check_az_login, get_az_context_prompt, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "id": "sub-1", "name": "MySub", "tenantId": "t1",
            "user": {"name": "user@test.com", "type": "user"},
        }))
        check_az_login(force_refresh=True)
        prompt = get_az_context_prompt()
        assert "Yes" in prompt
        assert "user@test.com" in prompt
        assert "MySub" in prompt

    @patch("app.tools.az_login_check.subprocess.run")
    def test_context_prompt_not_logged_in(self, mock_run):
        from app.tools.az_login_check import check_az_login, get_az_context_prompt, clear_login_cache
        clear_login_cache()

        mock_run.return_value = _mock_subprocess_failure("Not logged in")
        check_az_login(force_refresh=True)
        prompt = get_az_context_prompt()
        assert "**No**" in prompt
        assert "az login --use-device-code" in prompt


# ══════════════════════════════════════════════════════════════════════════════
# az_cost_query
# ══════════════════════════════════════════════════════════════════════════════

class TestAzCostQueryTool:
    def test_not_logged_in(self):
        tool = get_tool("az_cost_query")
        with _patch_az_not_logged_in():
            result = tool.execute({"query_type": "usage"}, _USER)
        assert "not logged in" in result

    @patch("app.tools.az_cost.subprocess.run")
    def test_usage_query_success(self, mock_run):
        tool = get_tool("az_cost_query")
        # First call: get subscription ID, Second call: REST query
        rest_response = json.dumps({
            "properties": {
                "columns": [
                    {"name": "PreTaxCost", "type": "Number"},
                    {"name": "UsageDate", "type": "Number"},
                    {"name": "Currency", "type": "String"},
                ],
                "rows": [
                    [100.00, 20260420, "USD"],
                    [23.45, 20260421, "USD"],
                ],
            }
        })
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(rest_response),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage", "time_period": "this_month"}, _USER)
        assert "123.45" in result
        assert "USD" in result
        # Second call should be the REST query
        rest_cmd = mock_run.call_args_list[1][0][0]
        assert "rest" in rest_cmd
        assert "MonthToDate" in str(rest_cmd)

    @patch("app.tools.az_cost.subprocess.run")
    def test_usage_query_custom_period(self, mock_run):
        tool = get_tool("az_cost_query")
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(json.dumps({"properties": {"columns": [], "rows": []}})),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage", "time_period": "last_7_days"}, _USER)
        rest_cmd = mock_run.call_args_list[1][0][0]
        body_str = str(rest_cmd)
        assert "Custom" in body_str

    @patch("app.tools.az_cost.subprocess.run")
    def test_usage_query_with_grouping(self, mock_run):
        tool = get_tool("az_cost_query")
        rest_response = json.dumps({
            "properties": {
                "columns": [
                    {"name": "PreTaxCost", "type": "Number"},
                    {"name": "ResourceGroup", "type": "String"},
                    {"name": "Currency", "type": "String"},
                ],
                "rows": [
                    [50.0, "rg-web", "USD"],
                    [30.0, "rg-db", "USD"],
                ],
            }
        })
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(rest_response),
        ]
        with _patch_az_logged_in():
            result = tool.execute({
                "query_type": "usage",
                "group_by": "ResourceGroup",
            }, _USER)
        assert "rg-web" in result
        assert "50.00" in result
        # Check body contains grouping
        rest_cmd = mock_run.call_args_list[1][0][0]
        body_arg = [a for a in rest_cmd if "ResourceGroup" in str(a)]
        assert len(body_arg) > 0

    @patch("app.tools.az_cost.subprocess.run")
    def test_usage_query_with_rg_filter(self, mock_run):
        tool = get_tool("az_cost_query")
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(json.dumps({"properties": {"columns": [], "rows": []}})),
        ]
        with _patch_az_logged_in():
            result = tool.execute({
                "query_type": "usage",
                "filter_resource_group": "my-rg",
            }, _USER)
        rest_cmd = mock_run.call_args_list[1][0][0]
        body_str = str(rest_cmd)
        assert "my-rg" in body_str

    @patch("app.tools.az_cost.subprocess.run")
    def test_forecast_query(self, mock_run):
        tool = get_tool("az_cost_query")
        rest_response = json.dumps({
            "properties": {
                "columns": [
                    {"name": "PreTaxCost", "type": "Number"},
                    {"name": "UsageDate", "type": "Number"},
                    {"name": "Currency", "type": "String"},
                ],
                "rows": [[500.0, 20260422, "USD"]],
            }
        })
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(rest_response),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "forecast"}, _USER)
        assert "500.00" in result

    @patch("app.tools.az_cost.subprocess.run")
    def test_budget_status(self, mock_run):
        tool = get_tool("az_cost_query")
        rest_response = json.dumps({
            "value": [
                {
                    "name": "monthly",
                    "properties": {
                        "amount": 1000,
                        "currentSpend": {"amount": 450, "unit": "USD"},
                    },
                },
            ]
        })
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_success(rest_response),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "budget_status"}, _USER)
        assert "monthly" in result
        assert "450" in result
        assert "1000" in result

    @patch("app.tools.az_cost.subprocess.run")
    def test_command_failure(self, mock_run):
        tool = get_tool("az_cost_query")
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_failure("BadRequest"),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage"}, _USER)
        assert "Error" in result

    @patch("app.tools.az_cost.subprocess.run")
    def test_429_retry(self, mock_run):
        """Test that 429 rate limits trigger a retry."""
        tool = get_tool("az_cost_query")
        rest_response = json.dumps({
            "properties": {
                "columns": [{"name": "PreTaxCost", "type": "Number"}, {"name": "Currency", "type": "String"}],
                "rows": [[99.0, "USD"]],
            }
        })
        mock_run.side_effect = [
            _mock_subprocess_success("sub-123\n"),
            _mock_subprocess_failure("429 Too Many Requests"),
            _mock_subprocess_success(rest_response),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage"}, _USER)
        assert "99.00" in result
        assert mock_run.call_count == 3  # sub + fail + retry

    @patch("app.tools.az_cost.subprocess.run")
    def test_no_subscription(self, mock_run):
        """Test error when subscription ID can't be determined."""
        tool = get_tool("az_cost_query")
        mock_run.return_value = _mock_subprocess_failure("not logged in")
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage"}, _USER)
        assert "Error" in result
        assert "subscription" in result.lower()

    @patch("app.tools.az_cost.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="az", timeout=60))
    def test_timeout(self, mock_run):
        tool = get_tool("az_cost_query")
        with _patch_az_logged_in():
            result = tool.execute({"query_type": "usage"}, _USER)
        assert "timed out" in result or "subscription" in result.lower()

    def test_requires_no_approval(self):
        tool = get_tool("az_cost_query")
        assert tool.requires_approval is False

    def test_schema_valid(self):
        tool = get_tool("az_cost_query")
        schema = tool.to_openai_schema()
        assert schema["function"]["name"] == "az_cost_query"
        assert "query_type" in schema["function"]["parameters"]["properties"]


# ══════════════════════════════════════════════════════════════════════════════
# az_monitor_logs
# ══════════════════════════════════════════════════════════════════════════════

class TestAzMonitorLogsTool:
    def test_not_logged_in(self):
        tool = get_tool("az_monitor_logs")
        with _patch_az_not_logged_in():
            result = tool.execute({"query": "Heartbeat"}, _USER)
        assert "not logged in" in result

    def test_empty_query(self):
        tool = get_tool("az_monitor_logs")
        with _patch_az_logged_in():
            result = tool.execute({"query": ""}, _USER)
        assert "Error" in result
        assert "required" in result

    @patch("app.tools.az_monitor.subprocess.run")
    def test_query_with_workspace(self, mock_run):
        tool = get_tool("az_monitor_logs")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"Computer": "vm1", "Heartbeat": "2024-01-01"}
        ]))
        with _patch_az_logged_in():
            result = tool.execute({
                "query": "Heartbeat | top 1",
                "workspace_id": "ws-123",
            }, _USER)
        assert "1 row" in result
        cmd = mock_run.call_args[0][0]
        assert "ws-123" in cmd

    @patch("app.tools.az_monitor.subprocess.run")
    def test_auto_discover_workspace(self, mock_run):
        tool = get_tool("az_monitor_logs")
        # First call: workspace discovery
        # Second call: actual query
        mock_run.side_effect = [
            _mock_subprocess_success("auto-ws-id\n"),
            _mock_subprocess_success(json.dumps([{"count_": 42}])),
        ]
        with _patch_az_logged_in():
            result = tool.execute({"query": "AzureActivity | count"}, _USER)
        assert "1 row" in result
        assert mock_run.call_count == 2

    @patch("app.tools.az_monitor.subprocess.run")
    def test_no_workspace_found(self, mock_run):
        tool = get_tool("az_monitor_logs")
        mock_run.return_value = _mock_subprocess_success("")  # empty output
        with _patch_az_logged_in():
            result = tool.execute({"query": "AzureActivity"}, _USER)
        assert "Error" in result
        assert "No Log Analytics workspace" in result

    @patch("app.tools.az_monitor.subprocess.run")
    def test_query_zero_results(self, mock_run):
        tool = get_tool("az_monitor_logs")
        mock_run.return_value = _mock_subprocess_success("[]")
        with _patch_az_logged_in():
            result = tool.execute({
                "query": "NonExistentTable",
                "workspace_id": "ws-1",
            }, _USER)
        assert "0 results" in result

    @patch("app.tools.az_monitor.subprocess.run")
    def test_query_failure(self, mock_run):
        tool = get_tool("az_monitor_logs")
        mock_run.return_value = _mock_subprocess_failure("Bad KQL syntax")
        with _patch_az_logged_in():
            result = tool.execute({
                "query": "invalid|||",
                "workspace_id": "ws-1",
            }, _USER)
        assert "Error" in result

    @patch("app.tools.az_monitor.subprocess.run")
    def test_custom_timespan(self, mock_run):
        tool = get_tool("az_monitor_logs")
        mock_run.return_value = _mock_subprocess_success("[]")
        with _patch_az_logged_in():
            result = tool.execute({
                "query": "Heartbeat",
                "workspace_id": "ws-1",
                "timespan": "P7D",
            }, _USER)
        cmd = mock_run.call_args[0][0]
        assert "P7D" in cmd

    def test_requires_no_approval(self):
        tool = get_tool("az_monitor_logs")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# az_rest_api
# ══════════════════════════════════════════════════════════════════════════════

class TestAzRestApiTool:
    def test_not_logged_in(self):
        tool = get_tool("az_rest_api")
        with _patch_az_not_logged_in():
            result = tool.execute({"method": "GET", "url": "/subscriptions/s"}, _USER)
        assert "not logged in" in result

    def test_url_required(self):
        tool = get_tool("az_rest_api")
        with _patch_az_logged_in():
            result = tool.execute({"method": "GET", "url": ""}, _USER)
        assert "Error" in result
        assert "url is required" in result

    def test_url_outside_azure(self):
        tool = get_tool("az_rest_api")
        with _patch_az_logged_in():
            result = tool.execute({
                "method": "GET",
                "url": "https://evil.example.com/steal-data",
            }, _USER)
        assert "Error" in result
        assert "management.azure.com" in result

    def test_relative_url_allowed(self):
        """Relative URLs starting with / should be allowed."""
        tool = get_tool("az_rest_api")
        with _patch_az_logged_in(), \
             patch("app.tools.az_rest.subprocess.run") as mock_run:
            mock_run.return_value = _mock_subprocess_success('{"value": []}')
            result = tool.execute({
                "method": "GET",
                "url": "/subscriptions/sub-1/resourceGroups?api-version=2021-04-01",
            }, _USER)
        assert "value" in result

    def test_invalid_json_body(self):
        tool = get_tool("az_rest_api")
        with _patch_az_logged_in():
            result = tool.execute({
                "method": "PUT",
                "url": "/subscriptions/s/rg",
                "body": "not json {{{",
            }, _USER)
        assert "Error" in result
        assert "Invalid JSON" in result

    @patch("app.tools.az_rest.subprocess.run")
    def test_get_success(self, mock_run):
        tool = get_tool("az_rest_api")
        mock_run.return_value = _mock_subprocess_success('{"name": "rg1"}')
        with _patch_az_logged_in():
            result = tool.execute({
                "method": "GET",
                "url": "https://management.azure.com/subscriptions/s/resourceGroups/rg1?api-version=2021-04-01",
            }, _USER)
        assert "rg1" in result

    @patch("app.tools.az_rest.subprocess.run")
    def test_put_with_body(self, mock_run):
        tool = get_tool("az_rest_api")
        mock_run.return_value = _mock_subprocess_success('{"status": "ok"}')
        with _patch_az_logged_in():
            result = tool.execute({
                "method": "PUT",
                "url": "https://management.azure.com/subscriptions/s/rg",
                "body": '{"location": "eastus"}',
            }, _USER)
        cmd = mock_run.call_args[0][0]
        assert "--body" in cmd

    @patch("app.tools.az_rest.subprocess.run")
    def test_empty_response_body(self, mock_run):
        tool = get_tool("az_rest_api")
        mock_run.return_value = _mock_subprocess_success("")
        with _patch_az_logged_in():
            result = tool.execute({
                "method": "DELETE",
                "url": "/subscriptions/s/rg",
            }, _USER)
        assert "success" in result.lower()

    def test_dynamic_approval_get(self):
        tool = get_tool("az_rest_api")
        assert tool._needs_approval("GET") is False
        assert tool._needs_approval("HEAD") is False

    def test_dynamic_approval_mutations(self):
        tool = get_tool("az_rest_api")
        assert tool._needs_approval("PUT") is True
        assert tool._needs_approval("POST") is True
        assert tool._needs_approval("PATCH") is True
        assert tool._needs_approval("DELETE") is True

    def test_graph_microsoft_url_allowed(self):
        tool = get_tool("az_rest_api")
        with _patch_az_logged_in(), \
             patch("app.tools.az_rest.subprocess.run") as mock_run:
            mock_run.return_value = _mock_subprocess_success('{}')
            result = tool.execute({
                "method": "GET",
                "url": "https://graph.microsoft.com/v1.0/me",
            }, _USER)
        assert "Error" not in result or "management.azure.com" not in result


# ══════════════════════════════════════════════════════════════════════════════
# generate_file
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateFileTool:
    """Tests for generate_file tool with real filesystem writes to a temp sandbox."""

    _OUTPUT_DIR = os.path.join(os.getcwd(), "output")

    def setup_method(self):
        """Clean output/ before each test."""
        if os.path.exists(self._OUTPUT_DIR):
            shutil.rmtree(self._OUTPUT_DIR)

    def teardown_method(self):
        """Clean output/ after each test."""
        if os.path.exists(self._OUTPUT_DIR):
            shutil.rmtree(self._OUTPUT_DIR)

    def test_write_simple_file(self):
        tool = get_tool("generate_file")
        result = tool.execute({
            "filename": "test.md",
            "content": "# Hello World",
        }, _USER)
        assert "File saved" in result
        assert os.path.exists(os.path.join(self._OUTPUT_DIR, "test.md"))

    def test_write_with_subdirectory(self):
        tool = get_tool("generate_file")
        result = tool.execute({
            "filename": "scripts/deploy.ps1",
            "content": "Write-Host 'Deploying...'",
        }, _USER)
        assert "File saved" in result
        assert os.path.exists(os.path.join(self._OUTPUT_DIR, "scripts", "deploy.ps1"))

    def test_path_traversal_blocked(self):
        tool = get_tool("generate_file")
        result = tool.execute({
            "filename": "../../../etc/passwd",
            "content": "evil",
        }, _USER)
        assert "Error" in result

    def test_double_dot_in_middle_blocked(self):
        tool = get_tool("generate_file")
        result = tool.execute({
            "filename": "scripts/../../../evil.sh",
            "content": "evil",
        }, _USER)
        assert "Error" in result

    def test_disallowed_extension(self):
        tool = get_tool("generate_file")
        result = tool.execute({
            "filename": "malware.exe",
            "content": "binary",
        }, _USER)
        assert "Error" in result
        assert "not allowed" in result

    def test_empty_filename(self):
        tool = get_tool("generate_file")
        result = tool.execute({"filename": "", "content": "stuff"}, _USER)
        assert "Error" in result

    def test_empty_content(self):
        tool = get_tool("generate_file")
        result = tool.execute({"filename": "empty.md", "content": ""}, _USER)
        assert "Error" in result

    def test_no_overwrite_by_default(self):
        tool = get_tool("generate_file")
        tool.execute({"filename": "dup.md", "content": "first"}, _USER)
        result = tool.execute({"filename": "dup.md", "content": "second"}, _USER)
        assert "Error" in result
        assert "already exists" in result

    def test_overwrite_when_flag_set(self):
        tool = get_tool("generate_file")
        tool.execute({"filename": "dup.md", "content": "first"}, _USER)
        result = tool.execute({
            "filename": "dup.md",
            "content": "second",
            "overwrite": True,
        }, _USER)
        assert "File saved" in result
        path = os.path.join(self._OUTPUT_DIR, "dup.md")
        with open(path) as f:
            assert f.read() == "second"

    def test_allowed_extensions(self):
        tool = get_tool("generate_file")
        for ext in [".bicep", ".tf", ".json", ".yaml", ".ps1", ".sh", ".py", ".md", ".txt", ".sql"]:
            result = tool.execute({
                "filename": f"test{ext}",
                "content": "content",
                "overwrite": True,
            }, _USER)
            assert "File saved" in result, f"Extension {ext} should be allowed"

    def test_size_limit(self):
        tool = get_tool("generate_file")
        big_content = "x" * (1_048_577)  # 1MB + 1
        result = tool.execute({
            "filename": "big.txt",
            "content": big_content,
        }, _USER)
        assert "Error" in result
        assert "maximum" in result.lower()

    def test_special_chars_in_filename(self):
        tool = get_tool("generate_file")
        for bad in ['file<.md', 'file>.md', 'file".md', 'file|.md', 'file?.md', 'file*.md']:
            result = tool.execute({"filename": bad, "content": "x"}, _USER)
            assert "Error" in result, f"Filename '{bad}' should be rejected"

    def test_requires_no_approval(self):
        tool = get_tool("generate_file")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# az_devops
# ══════════════════════════════════════════════════════════════════════════════

class TestAzDevOpsTool:
    @patch("app.tools.az_devops.subprocess.run")
    def test_list_pipelines(self, mock_run):
        tool = get_tool("az_devops")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"id": 1, "name": "CI Pipeline"},
        ]))
        result = tool.execute({
            "action": "list_pipelines",
            "organization": "https://dev.azure.com/myorg",
            "project": "myproject",
        }, _USER)
        assert "CI Pipeline" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_list_builds(self, mock_run):
        tool = get_tool("az_devops")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"id": 100, "result": "succeeded"},
        ]))
        result = tool.execute({
            "action": "list_builds",
            "project": "myproject",
        }, _USER)
        assert "succeeded" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_show_pipeline_requires_id(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "show_pipeline"}, _USER)
        assert "Error" in result
        assert "pipeline_id" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_show_build_requires_id(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "show_build"}, _USER)
        assert "Error" in result
        assert "build_id" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_trigger_build_requires_pipeline_id(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "trigger_build"}, _USER)
        assert "Error" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_list_prs(self, mock_run):
        tool = get_tool("az_devops")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"pullRequestId": 42, "title": "Fix bug"},
        ]))
        result = tool.execute({"action": "list_prs", "project": "p"}, _USER)
        assert "Fix bug" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_show_pr_requires_id(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "show_pr"}, _USER)
        assert "Error" in result
        assert "pr_id" in result

    @patch("app.tools.az_devops.subprocess.run")
    def test_create_pr_requires_fields(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "create_pr"}, _USER)
        assert "Error" in result
        assert "branch" in result

    def test_unknown_action(self):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "nonexistent"}, _USER)
        assert "Error" in result
        assert "Unknown action" in result

    def test_dynamic_approval_safe_actions(self):
        tool = get_tool("az_devops")
        for action in ["list_pipelines", "list_builds", "list_prs", "show_pipeline", "show_build", "show_pr", "list_work_items"]:
            assert tool._needs_approval(action) is False

    def test_dynamic_approval_mutation_actions(self):
        tool = get_tool("az_devops")
        assert tool._needs_approval("trigger_build") is True
        assert tool._needs_approval("create_pr") is True

    @patch("app.tools.az_devops.subprocess.run")
    def test_extension_not_installed_hint(self, mock_run):
        tool = get_tool("az_devops")
        mock_run.return_value = _mock_subprocess_failure("'pipelines' is not in the 'az' command group. azure-devops not found")
        result = tool.execute({"action": "list_pipelines", "project": "p"}, _USER)
        assert "azure-devops" in result.lower()

    @patch("app.tools.az_devops.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="az", timeout=30))
    def test_timeout(self, mock_run):
        tool = get_tool("az_devops")
        result = tool.execute({"action": "list_pipelines"}, _USER)
        assert "timed out" in result


# ══════════════════════════════════════════════════════════════════════════════
# az_policy_check
# ══════════════════════════════════════════════════════════════════════════════

class TestAzPolicyCheckTool:
    def test_not_logged_in(self):
        tool = get_tool("az_policy_check")
        with _patch_az_not_logged_in():
            result = tool.execute({"action": "compliance_summary"}, _USER)
        assert "not logged in" in result

    @patch("app.tools.az_policy.subprocess.run")
    def test_compliance_summary(self, mock_run):
        tool = get_tool("az_policy_check")
        mock_run.return_value = _mock_subprocess_success(json.dumps({
            "policyAssignments": [],
            "results": {"nonCompliantResources": 3},
        }))
        with _patch_az_logged_in():
            result = tool.execute({"action": "compliance_summary"}, _USER)
        assert "nonCompliantResources" in result or "3" in result

    @patch("app.tools.az_policy.subprocess.run")
    def test_non_compliant_resources(self, mock_run):
        tool = get_tool("az_policy_check")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"resourceId": "/sub/rg/res1", "complianceState": "NonCompliant"},
        ]))
        with _patch_az_logged_in():
            result = tool.execute({"action": "non_compliant_resources"}, _USER)
        assert "NonCompliant" in result

    @patch("app.tools.az_policy.subprocess.run")
    def test_list_assignments(self, mock_run):
        tool = get_tool("az_policy_check")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"name": "enforce-tags", "displayName": "Enforce Tags"},
        ]))
        with _patch_az_logged_in():
            result = tool.execute({"action": "list_assignments"}, _USER)
        assert "enforce-tags" in result

    @patch("app.tools.az_policy.subprocess.run")
    def test_with_resource_group_scope(self, mock_run):
        tool = get_tool("az_policy_check")
        mock_run.return_value = _mock_subprocess_success("[]")
        with _patch_az_logged_in():
            tool.execute({
                "action": "compliance_summary",
                "resource_group": "my-rg",
            }, _USER)
        cmd = mock_run.call_args[0][0]
        assert "--resource-group" in cmd
        assert "my-rg" in cmd

    def test_unknown_action(self):
        tool = get_tool("az_policy_check")
        with _patch_az_logged_in():
            result = tool.execute({"action": "invalid"}, _USER)
        assert "Error" in result

    def test_requires_no_approval(self):
        tool = get_tool("az_policy_check")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# az_advisor
# ══════════════════════════════════════════════════════════════════════════════

class TestAzAdvisorTool:
    def test_not_logged_in(self):
        tool = get_tool("az_advisor")
        with _patch_az_not_logged_in():
            result = tool.execute({}, _USER)
        assert "not logged in" in result

    @patch("app.tools.az_advisor.subprocess.run")
    def test_list_all_recommendations(self, mock_run):
        tool = get_tool("az_advisor")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"category": "Cost", "impact": "High", "shortDescription": {"problem": "Resize VM"}},
        ]))
        with _patch_az_logged_in():
            result = tool.execute({}, _USER)
        assert "Resize VM" in result

    @patch("app.tools.az_advisor.subprocess.run")
    def test_filter_by_category(self, mock_run):
        tool = get_tool("az_advisor")
        mock_run.return_value = _mock_subprocess_success("[]")
        with _patch_az_logged_in():
            tool.execute({"category": "Security"}, _USER)
        cmd = mock_run.call_args[0][0]
        assert "--category" in cmd
        assert "Security" in cmd

    @patch("app.tools.az_advisor.subprocess.run")
    def test_filter_by_resource_group(self, mock_run):
        tool = get_tool("az_advisor")
        mock_run.return_value = _mock_subprocess_success("[]")
        with _patch_az_logged_in():
            tool.execute({"resource_group": "rg-prod"}, _USER)
        cmd = mock_run.call_args[0][0]
        assert "--resource-group" in cmd
        assert "rg-prod" in cmd

    @patch("app.tools.az_advisor.subprocess.run")
    def test_no_recommendations(self, mock_run):
        tool = get_tool("az_advisor")
        mock_run.return_value = _mock_subprocess_success("")
        with _patch_az_logged_in():
            result = tool.execute({}, _USER)
        assert "No recommendations" in result

    @patch("app.tools.az_advisor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="az", timeout=60))
    def test_timeout(self, mock_run):
        tool = get_tool("az_advisor")
        with _patch_az_logged_in():
            result = tool.execute({}, _USER)
        assert "timed out" in result

    def test_requires_no_approval(self):
        tool = get_tool("az_advisor")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# network_test
# ══════════════════════════════════════════════════════════════════════════════

class TestNetworkTestTool:
    def test_dns_lookup_localhost(self):
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "dns_lookup",
            "hostname": "localhost",
        }, _USER)
        assert "DNS resolution" in result
        assert "127.0.0.1" in result or "::1" in result

    def test_dns_lookup_missing_hostname(self):
        tool = get_tool("network_test")
        result = tool.execute({"action": "dns_lookup"}, _USER)
        assert "Error" in result
        assert "hostname" in result

    def test_dns_lookup_invalid_hostname(self):
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "dns_lookup",
            "hostname": "invalid host name with spaces!",
        }, _USER)
        assert "Error" in result or "Invalid" in result

    def test_dns_lookup_nonexistent(self):
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "dns_lookup",
            "hostname": "this.domain.does.not.exist.xyzzy",
        }, _USER)
        assert "failed" in result.lower() or "error" in result.lower()

    @patch("app.tools.network_test.socket.create_connection")
    def test_port_check_success(self, mock_conn):
        tool = get_tool("network_test")
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        result = tool.execute({
            "action": "port_check",
            "hostname": "example.com",
            "port": 443,
        }, _USER)
        assert "SUCCESS" in result
        mock_sock.close.assert_called_once()

    @patch("app.tools.network_test.socket.create_connection", side_effect=ConnectionRefusedError)
    def test_port_check_refused(self, mock_conn):
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "port_check",
            "hostname": "example.com",
            "port": 12345,
        }, _USER)
        assert "REFUSED" in result

    @patch("app.tools.network_test.socket.create_connection", side_effect=socket.timeout)
    def test_port_check_timeout(self, mock_conn):
        import socket
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "port_check",
            "hostname": "example.com",
            "port": 443,
        }, _USER)
        assert "TIMEOUT" in result

    def test_port_check_missing_hostname(self):
        tool = get_tool("network_test")
        result = tool.execute({"action": "port_check"}, _USER)
        assert "Error" in result

    def test_port_check_invalid_port(self):
        tool = get_tool("network_test")
        result = tool.execute({
            "action": "port_check",
            "hostname": "example.com",
            "port": 0,
        }, _USER)
        assert "Error" in result or "out of range" in result

    def test_nsg_rules_requires_params(self):
        tool = get_tool("network_test")
        with _patch_az_logged_in():
            result = tool.execute({"action": "nsg_rules"}, _USER)
        assert "Error" in result
        assert "resource_group" in result

    @patch("app.tools.network_test.subprocess.run")
    def test_nsg_rules_success(self, mock_run):
        tool = get_tool("network_test")
        mock_run.return_value = _mock_subprocess_success(json.dumps([
            {"name": "AllowHTTPS", "priority": 100, "access": "Allow"},
        ]))
        with _patch_az_logged_in():
            result = tool.execute({
                "action": "nsg_rules",
                "resource_group": "rg-1",
                "nsg_name": "nsg-1",
            }, _USER)
        assert "AllowHTTPS" in result

    def test_nsg_rules_not_logged_in(self):
        tool = get_tool("network_test")
        with _patch_az_not_logged_in():
            result = tool.execute({
                "action": "nsg_rules",
                "resource_group": "rg-1",
                "nsg_name": "nsg-1",
            }, _USER)
        assert "not logged in" in result

    def test_unknown_action(self):
        tool = get_tool("network_test")
        result = tool.execute({"action": "invalid"}, _USER)
        assert "Error" in result

    def test_requires_no_approval(self):
        tool = get_tool("network_test")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# diagram_gen
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagramGenTool:
    def test_with_mermaid_code(self):
        tool = get_tool("diagram_gen")
        result = tool.execute({
            "diagram_type": "flowchart",
            "mermaid_code": "flowchart LR\n  A --> B --> C",
        }, _USER)
        assert "```mermaid" in result
        assert "A --> B --> C" in result

    def test_with_description_only(self):
        tool = get_tool("diagram_gen")
        result = tool.execute({
            "diagram_type": "architecture",
            "description": "Show VNet with 3 subnets",
        }, _USER)
        assert "Diagram request received" in result
        assert "architecture" in result

    def test_no_description_or_code(self):
        tool = get_tool("diagram_gen")
        result = tool.execute({"diagram_type": "flowchart"}, _USER)
        assert "Error" in result

    def test_mermaid_code_with_description(self):
        tool = get_tool("diagram_gen")
        result = tool.execute({
            "diagram_type": "sequence",
            "description": "Auth flow",
            "mermaid_code": "sequenceDiagram\n  Client->>Server: Request",
        }, _USER)
        assert "```mermaid" in result
        assert "Auth flow" in result or "N/A" not in result

    def test_requires_no_approval(self):
        tool = get_tool("diagram_gen")
        assert tool.requires_approval is False

    def test_schema_has_diagram_type_enum(self):
        tool = get_tool("diagram_gen")
        schema = tool.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "diagram_type" in props
        assert "enum" in props["diagram_type"]


# ══════════════════════════════════════════════════════════════════════════════
# web_fetch
# ══════════════════════════════════════════════════════════════════════════════

class TestWebFetchTool:
    def test_empty_url(self):
        tool = get_tool("web_fetch")
        result = tool.execute({"url": ""}, _USER)
        assert "Error" in result

    def test_http_url_blocked(self):
        tool = get_tool("web_fetch")
        result = tool.execute({"url": "http://example.com"}, _USER)
        assert "Error" in result
        assert "HTTPS" in result

    def test_http_localhost_allowed(self):
        """HTTP to localhost should be allowed."""
        tool = get_tool("web_fetch")
        with patch("app.tools.web_fetch.httpx.Client") as mock_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "OK"
            mock_response.reason_phrase = "OK"
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_cls.return_value = mock_client

            result = tool.execute({"url": "http://localhost:8080/health"}, _USER)
        assert "Error" not in result or "HTTPS" not in result

    def test_ftp_url_blocked(self):
        tool = get_tool("web_fetch")
        result = tool.execute({"url": "ftp://files.example.com/data"}, _USER)
        assert "Error" in result

    def test_invalid_url(self):
        tool = get_tool("web_fetch")
        result = tool.execute({"url": "not a url at all"}, _USER)
        assert "Error" in result

    @patch("app.tools.web_fetch.httpx.Client")
    def test_fetch_success_text_mode(self, mock_cls):
        tool = get_tool("web_fetch")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.reason_phrase = "OK"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_cls.return_value = mock_client

        result = tool.execute({"url": "https://example.com"}, _USER)
        assert "Hello World" in result
        assert "<html>" not in result  # HTML should be stripped

    @patch("app.tools.web_fetch.httpx.Client")
    def test_fetch_raw_mode(self, mock_cls):
        tool = get_tool("web_fetch")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Raw Content</body></html>"
        mock_response.reason_phrase = "OK"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_cls.return_value = mock_client

        result = tool.execute({
            "url": "https://example.com",
            "extract_mode": "raw",
        }, _USER)
        assert "<html>" in result  # HTML should NOT be stripped in raw mode

    @patch("app.tools.web_fetch.httpx.Client")
    def test_fetch_headers_only(self, mock_cls):
        tool = get_tool("web_fetch")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html", "server": "nginx"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_cls.return_value = mock_client

        result = tool.execute({
            "url": "https://example.com",
            "extract_mode": "headers_only",
        }, _USER)
        assert "Status: 200" in result
        assert "content-type" in result

    @patch("app.tools.web_fetch.httpx.Client")
    def test_http_error_status(self, mock_cls):
        tool = get_tool("web_fetch")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.reason_phrase = "Not Found"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_cls.return_value = mock_client

        result = tool.execute({"url": "https://example.com/missing"}, _USER)
        assert "Error" in result
        assert "404" in result

    @patch("app.tools.web_fetch.httpx.Client")
    def test_connection_error(self, mock_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_cls.return_value = mock_client

        tool = get_tool("web_fetch")
        result = tool.execute({"url": "https://nonexistent.example.com"}, _USER)
        assert "Error" in result

    @patch("app.tools.web_fetch.httpx.Client")
    def test_timeout_error(self, mock_cls):
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.TimeoutException("Timed out")
        mock_cls.return_value = mock_client

        tool = get_tool("web_fetch")
        result = tool.execute({"url": "https://slow.example.com"}, _USER)
        assert "timed out" in result.lower()

    def test_text_extraction(self):
        tool = get_tool("web_fetch")
        html = (
            "<html><head><script>var x=1;</script><style>.a{}</style></head>"
            "<body><h1>Title</h1><p>Paragraph text.</p></body></html>"
        )
        text = tool._extract_text(html)
        assert "Title" in text
        assert "Paragraph text" in text
        assert "<script>" not in text
        assert "<style>" not in text
        assert "<h1>" not in text

    def test_requires_no_approval(self):
        tool = get_tool("web_fetch")
        assert tool.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator _tool_needs_approval integration
# ══════════════════════════════════════════════════════════════════════════════

class TestToolNeedsApproval:
    """Test the dynamic approval function in the orchestrator."""

    def test_static_approval_tools(self):
        from app.agent.orchestrator import _tool_needs_approval

        az_cli = get_tool("az_cli")
        shell = get_tool("run_shell")
        kb = get_tool("read_kb_file")

        assert _tool_needs_approval(az_cli, {}) is True
        assert _tool_needs_approval(shell, {}) is True
        assert _tool_needs_approval(kb, {}) is False

    def test_dynamic_az_rest_api(self):
        from app.agent.orchestrator import _tool_needs_approval

        rest = get_tool("az_rest_api")
        assert _tool_needs_approval(rest, {"method": "GET"}) is False
        assert _tool_needs_approval(rest, {"method": "PUT"}) is True
        assert _tool_needs_approval(rest, {"method": "DELETE"}) is True

    def test_dynamic_az_devops(self):
        from app.agent.orchestrator import _tool_needs_approval

        devops = get_tool("az_devops")
        assert _tool_needs_approval(devops, {"action": "list_pipelines"}) is False
        assert _tool_needs_approval(devops, {"action": "trigger_build"}) is True
        assert _tool_needs_approval(devops, {"action": "create_pr"}) is True
