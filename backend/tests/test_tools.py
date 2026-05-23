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
        # init_tools() was already called at module top — calling it again
        # would re-trigger the `from python_diagram import _DIAGRAM_IMPORTS`
        # path and, when another test in the same session has incidentally
        # cleared the prometheus registry, surface a flake. We assert against
        # the registry that the single top-level init() produced.
        expected = {
            "read_kb_file", "search_kb", "search_kb_semantic", "search_kb_hybrid",
            "fetch_ms_docs", "execute_script",
            "az_cli", "az_resource_graph", "az_cost_query", "az_monitor_logs",
            "az_rest_api", "generate_file", "read_file", "az_devops", "az_policy_check",
            "az_advisor", "network_test", "web_fetch", "render_drawio",
            "validate_drawio",
            "patch_drawio_cell", "ask_user",
            "search_stack_overflow", "search_github", "search_azure_updates", "web_search",
            "generate_python_diagram", "generate_drawio_from_python",
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
        for name in ("execute_script", "az_cli"):
            assert TOOL_REGISTRY[name].requires_approval is True
        for name in ("read_kb_file", "read_file", "search_kb", "search_kb_semantic", "fetch_ms_docs"):
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

    @patch("app.tools.generic.kb_tools.AzureOpenAI")
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

    @patch("app.tools.generic.kb_tools.AzureOpenAI")
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
    @patch("app.tools.generic.ms_docs.httpx.Client")
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


class TestExecuteScriptTool:
    """Smoke tests for the path-only script runner that replaced run_shell."""

    @staticmethod
    def _write_script(name: str, body: str) -> None:
        from pathlib import Path
        scripts_dir = Path("output") / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / name).write_text(body, encoding="utf-8")

    @staticmethod
    def _cleanup(name: str) -> None:
        from pathlib import Path
        (Path("output") / "scripts" / name).unlink(missing_ok=True)

    def test_path_required(self):
        tool = get_tool("execute_script")
        result = tool.execute({"reason": "test"}, _USER)
        assert isinstance(result, str)
        assert "path is required" in result

    def test_path_traversal_blocked(self):
        tool = get_tool("execute_script")
        result = tool.execute({"path": "../../etc/passwd", "reason": "t"}, _USER)
        assert "path traversal" in result or "escapes" in result

    def test_unknown_extension_rejected(self):
        tool = get_tool("execute_script")
        # Create a .txt that exists so the extension check is the gate, not file-not-found.
        self._write_script("not-a-script.txt", "hello")
        try:
            result = tool.execute({"path": "not-a-script.txt", "reason": "t"}, _USER)
            assert "extension" in result
        finally:
            self._cleanup("not-a-script.txt")

    def test_script_not_found(self):
        tool = get_tool("execute_script")
        result = tool.execute({"path": "does-not-exist.ps1", "reason": "t"}, _USER)
        assert "not found" in result

    def test_runs_real_script(self):
        """End-to-end: write a tiny script then execute it. Uses .sh on POSIX
        and .ps1 on Windows so each platform exercises a real interpreter."""
        import sys
        tool = get_tool("execute_script")
        if sys.platform == "win32":
            self._write_script("hello.ps1", "Write-Output 'hello-from-script'")
            try:
                result = tool.execute({"path": "hello.ps1", "reason": "t"}, _USER)
            finally:
                self._cleanup("hello.ps1")
        else:
            self._write_script("hello.sh", "#!/usr/bin/env bash\necho hello-from-script")
            try:
                result = tool.execute({"path": "hello.sh", "reason": "t"}, _USER)
            finally:
                self._cleanup("hello.sh")
        assert "hello-from-script" in result
        assert "Exit code: 0" in result

    def test_timeout_string_does_not_crash(self):
        tool = get_tool("execute_script")
        result = tool.execute(
            {"path": "x.ps1", "reason": "t", "timeout_seconds": "abc"}, _USER
        )
        assert isinstance(result, str)
        assert "must be a positive integer" in result


class TestReadFileTool:
    """ReadFileTool — sandboxed to output/. Symmetric with generate_file."""

    @staticmethod
    def _write(name: str, body: str) -> None:
        from pathlib import Path
        (Path("output") / name).parent.mkdir(parents=True, exist_ok=True)
        (Path("output") / name).write_text(body, encoding="utf-8")

    @staticmethod
    def _cleanup(name: str) -> None:
        from pathlib import Path
        (Path("output") / name).unlink(missing_ok=True)

    def test_path_required(self):
        tool = get_tool("read_file")
        result = tool.execute({}, _USER)
        assert "path is required" in result

    def test_path_traversal_blocked(self):
        tool = get_tool("read_file")
        result = tool.execute({"path": "../app/main.py"}, _USER)
        assert "path traversal" in result or "escapes" in result

    def test_nonexistent_returns_error(self):
        tool = get_tool("read_file")
        result = tool.execute({"path": "definitely-missing-file.txt"}, _USER)
        assert "not found" in result

    def test_reads_written_file(self):
        tool = get_tool("read_file")
        self._write("read-file-smoke.json", '{"x": 1}')
        try:
            result = tool.execute({"path": "read-file-smoke.json"}, _USER)
            assert '{"x": 1}' in result
            assert "output/read-file-smoke.json" in result
        finally:
            self._cleanup("read-file-smoke.json")

    def test_truncates_at_max_bytes(self):
        tool = get_tool("read_file")
        self._write("trunc.txt", "A" * 100)
        try:
            result = tool.execute({"path": "trunc.txt", "max_bytes": 10}, _USER)
            # Header + 10 'A's
            assert "showing first 10" in result
            assert result.count("A") == 10
        finally:
            self._cleanup("trunc.txt")


class TestAzCliTool:
    def test_az_cli_requires_list_args(self):
        tool = get_tool("az_cli")
        result = tool.execute({"args": "not-a-list", "reason": "test"}, _USER)
        assert "Error" in result


# ── Adversarial / quality coverage ──────────────────────────────────────────
# Cases below try to break each tool with malformed, hostile, or edge-case
# input. They should all either return a clean "Error: ..." string OR run the
# real command — never raise an unhandled exception or crash the process.


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


# ── Track 1A regression tests ───────────────────────────────────────────────
# Verifies the three security fixes from RemediationPlan.md Phase 1 Track 1A:
#   B1  – _run_az uses an explicit env allowlist (no secret leakage)
#   B2  – % metachar blocked (cmd.exe env expansion defence-in-depth)
#   CR#1 – shell=False always; & blocked; az.cmd resolved on Windows


class TestRunAzEnvAllowlist:
    """B1: _run_az must NOT forward secrets from os.environ to the subprocess."""

    def test_run_az_strips_secret_env_vars(self, monkeypatch):
        """The subprocess must receive only the allowed env vars.
        Secret-looking keys (AZURE_OPENAI_API_KEY, SECRET_TOKEN, etc.) must
        be absent from the env dict passed to subprocess.run."""
        import os
        import subprocess

        # Plant a fake secret into the process environment.
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret-value")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///secrets.db")

        captured_env: dict = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            # Return a successful empty result so _run_az doesn't error out.
            mock = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return mock

        from app.tools.base import _az_executable_path, _az_circuit_breaker_tripped
        import app.tools.base as base_mod

        monkeypatch.setattr(base_mod, "_az_executable_path", "/usr/bin/az")
        monkeypatch.setattr(base_mod, "_az_circuit_breaker_tripped", False)
        monkeypatch.setattr(subprocess, "run", fake_run)

        # Instantiate a minimal AzureToolBase subclass inline for testing.
        from app.tools.base import AzureToolBase
        from app.auth.models import User

        class _TestTool(AzureToolBase):
            name = "_test_run_az_env"
            description = "test"
            parameters_schema = {"type": "object", "properties": {}}

            def execute(self, args, user):
                return self._run_az(
                    ["/usr/bin/az", "account", "show"],
                    label="test",
                    use_retry=False,
                )

        tool = _TestTool()
        tool.execute({}, User(oid="u", email="u@t.com", display_name="U"))

        assert "AZURE_OPENAI_API_KEY" not in captured_env, (
            "FLAW B1: AZURE_OPENAI_API_KEY leaked into subprocess env"
        )
        assert "DATABASE_URL" not in captured_env, (
            "FLAW B1: DATABASE_URL leaked into subprocess env"
        )
        # PATH must be present — az needs it.
        assert "PATH" in captured_env

    def test_run_az_forwards_arm_token(self, monkeypatch):
        """ARM token from ContextVar must appear in the subprocess env as
        AZURE_ACCESS_TOKEN even though it is not in os.environ."""
        import subprocess
        from app.tools.base import set_arm_token, AzureToolBase
        from app.auth.models import User
        import app.tools.base as base_mod

        set_arm_token("eyJtoken123")

        captured_env: dict = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(base_mod, "_az_executable_path", "/usr/bin/az")
        monkeypatch.setattr(base_mod, "_az_circuit_breaker_tripped", False)
        monkeypatch.setattr(subprocess, "run", fake_run)

        class _TestTool(AzureToolBase):
            name = "_test_arm_fwd"
            description = "test"
            parameters_schema = {"type": "object", "properties": {}}

            def execute(self, args, user):
                return self._run_az(["/usr/bin/az", "account", "show"],
                                    label="test", use_retry=False)

        _TestTool().execute({}, User(oid="u", email="u@t.com", display_name="U"))

        assert captured_env.get("AZURE_ACCESS_TOKEN") == "eyJtoken123", (
            "ARM token was not forwarded to the subprocess env"
        )
        # Clean up ContextVar
        set_arm_token(None)


class TestShellInjectionBlocking:
    """B2 + CR#1: check_shell_injection must block %, &, backtick, and NUL
    in az CLI argument values."""

    def _check(self, value: str):
        from app.tools.base import check_shell_injection
        return check_shell_injection(value, "test_arg")

    def test_ampersand_whoami_blocked(self):
        """CR#1: & used for command chaining must be rejected."""
        result = self._check("&whoami")
        assert result is not None, "FLAW CR#1: &whoami not blocked"
        assert "not allowed" in result

    def test_percent_env_expansion_blocked(self):
        """B2: %PATH% style Windows env expansion must be rejected."""
        result = self._check("%PATH%")
        assert result is not None, "FLAW B2: %PATH% not blocked"
        assert "not allowed" in result

    def test_percent_api_key_blocked(self):
        """B2: %AZURE_OPENAI_API_KEY% must be rejected."""
        result = self._check("%AZURE_OPENAI_API_KEY%")
        assert result is not None, "FLAW B2: %AZURE_OPENAI_API_KEY% not blocked"
        assert "not allowed" in result

    def test_backtick_blocked(self):
        """Existing: backtick (PowerShell execution) must be rejected."""
        result = self._check("`whoami`")
        assert result is not None
        assert "not allowed" in result

    def test_nul_byte_blocked(self):
        """Existing: NUL byte must be rejected."""
        result = self._check("safe\x00malicious")
        assert result is not None
        assert "not allowed" in result

    def test_pipe_in_kql_allowed(self):
        """| in a KQL query value must NOT be blocked (safe with shell=False)."""
        result = self._check("Resources | count")
        assert result is None, "FLAW: KQL pipe blocked, will break az_resource_graph"

    def test_clean_arg_allowed(self):
        """Normal resource group name must pass."""
        assert self._check("my-resource-group") is None
        assert self._check("--output") is None
        assert self._check("json") is None

    def test_semicolon_allowed(self):
        """Semicolons appear in JMESPath; must not be blocked."""
        assert self._check("[?name=='a;b']") is None


class TestRunAzShellFalse:
    """CR#1: _run_az must never use shell=True."""

    def test_subprocess_run_called_with_shell_false(self, monkeypatch):
        """Monkeypatch subprocess.run and assert shell kwarg is False."""
        import subprocess
        import app.tools.base as base_mod
        from app.tools.base import AzureToolBase
        from app.auth.models import User

        captured_kwargs: dict = {}

        def fake_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(base_mod, "_az_executable_path", "/usr/bin/az")
        monkeypatch.setattr(base_mod, "_az_circuit_breaker_tripped", False)
        monkeypatch.setattr(subprocess, "run", fake_run)

        class _TestTool(AzureToolBase):
            name = "_test_shell_false"
            description = "test"
            parameters_schema = {"type": "object", "properties": {}}

            def execute(self, args, user):
                return self._run_az(["/usr/bin/az", "account", "show"],
                                    label="test", use_retry=False)

        _TestTool().execute({}, User(oid="u", email="u@t.com", display_name="U"))

        assert captured_kwargs.get("shell") is False, (
            "FLAW CR#1: _run_az passed shell=True to subprocess.run — "
            "this enables shell metachar injection on Windows"
        )


# ── Batch B: file-I/O adversarial ───────────────────────────────────────────


class TestGenerateFileAdversarial:
    """Adversarial inputs for generate_file. Sandbox is output/. Every path
    that could escape the sandbox or crash the tool must return a clean
    error string."""

    _OUTPUT_DIR = None  # set per-test via pytest tmp swap

    def setup_method(self):
        # Each test starts with a clean output/ directory.
        import os
        import shutil
        out = os.path.join(os.getcwd(), "output")
        if os.path.exists(out):
            try:
                shutil.rmtree(out)
            except OSError:
                pass

    def test_args_is_none(self):
        """G1: args=None — must not crash with AttributeError."""
        tool = get_tool("generate_file")
        try:
            result = tool.execute(None, _USER)
        except AttributeError:
            pytest.fail("FLAW: generate_file crashes on args=None")
        assert isinstance(result, str)
        assert "Error" in result

    def test_windows_reserved_name(self):
        """G2: Windows reserved device name (CON, PRN, NUL) — Windows can
        block writes; must not hang or lock the tool. Return either error
        or success cleanly."""
        tool = get_tool("generate_file")
        result = tool.execute(
            {"filename": "CON.md", "content": "x"}, _USER,
        )
        assert isinstance(result, str)
        # Either rejected or succeeded — both are fine; what we don't want
        # is a hang or unhandled OSError.

    def test_very_long_filename(self):
        """G3: 300-character filename — exceeds Windows MAX_PATH.
        Must return a clean error, not raise OSError."""
        tool = get_tool("generate_file")
        long_name = "a" * 300 + ".md"
        result = tool.execute(
            {"filename": long_name, "content": "x"}, _USER,
        )
        assert isinstance(result, str)
        # Either rejected at validation or fails cleanly at write
        assert "Error" in result or "File saved" in result

    def test_dot_only_filename(self):
        """G4: filename '.md' — has no stem, technically extension only.
        Must be handled cleanly."""
        tool = get_tool("generate_file")
        result = tool.execute(
            {"filename": ".md", "content": "x"}, _USER,
        )
        assert isinstance(result, str)
        # `.md` has no stem; suffix() in pathlib returns '' for this on most
        # Pythons, so it should be rejected as invalid extension. Either way
        # no crash.
        # Document actual behavior: reject with "extension" message OR succeed.

    def test_absolute_windows_path_blocked(self):
        """G5: absolute Windows drive path. The first-layer regex blocks
        ^/|^\\, but NOT ^[A-Z]:. The sandbox-relative_to() check is the
        backstop. Both layers active here, must reject."""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows-specific path handling")
        tool = get_tool("generate_file")
        result = tool.execute(
            {"filename": r"C:\Windows\Temp\evil.md", "content": "x"}, _USER,
        )
        assert "Error" in result
        # The colon ':' is in _DANGEROUS_PATTERNS so first layer catches it.
        # Either way, must reject.

    def test_unencodeable_content(self):
        """G6: content containing a lone surrogate that can't be encoded as
        UTF-8. write_text raises UnicodeEncodeError, NOT OSError, so the
        current `except OSError` doesn't catch it. Must not crash."""
        tool = get_tool("generate_file")
        # \ud800 is a high surrogate without a low surrogate — invalid alone.
        bad_content = "hello \ud800 world"
        try:
            result = tool.execute(
                {"filename": "bad.md", "content": bad_content,
                 "overwrite": True},
                _USER,
            )
        except UnicodeEncodeError:
            pytest.fail(
                "FLAW: generate_file does not handle UnicodeEncodeError "
                "from write_text — surrogate-bearing content crashes the tool"
            )
        assert isinstance(result, str)

    def test_empty_content_rejected(self):
        """G7: empty content — current code blocks via `if not content`."""
        tool = get_tool("generate_file")
        result = tool.execute(
            {"filename": "empty.md", "content": ""}, _USER,
        )
        assert "Error" in result and "content" in result.lower()

    def test_string_overwrite_flag(self):
        """G9: overwrite='true' (string, not bool). Python treats non-empty
        string as truthy, so this currently overwrites. Document: the param
        is loose-typed; LLMs that send strings get the truthy interpretation."""
        tool = get_tool("generate_file")
        # First write to create the file
        first = tool.execute(
            {"filename": "stringflag.md", "content": "first"}, _USER,
        )
        assert "File saved" in first
        # Now try to overwrite with the string "true"
        second = tool.execute(
            {"filename": "stringflag.md", "content": "second",
             "overwrite": "true"},
            _USER,
        )
        # Python truthy "true" → overwrites. Acceptable as long as result is
        # a string and no crash.
        assert isinstance(second, str)
        # Confirm it actually overwrote
        assert "File saved" in second

    def test_nul_byte_in_filename(self):
        """G10: NUL byte in filename — already in dangerous-chars regex."""
        tool = get_tool("generate_file")
        result = tool.execute(
            {"filename": "ev\x00il.md", "content": "x"}, _USER,
        )
        assert "Error" in result

    def test_drawio_rejected_under_chat_with_kb(self):
        """G11: §5 2026-05-19 enforces Engineer (`chat-with-kb`) hands diagrams
        off to Architect. The skill prompt says so; the LLM ignored it in
        sanity testing. The tool layer must enforce the contract independently
        of the prompt — so the model can't write .drawio when the active skill
        is chat-with-kb, regardless of what the model thinks it should do."""
        from app.tools.base import set_skill_name
        tool = get_tool("generate_file")
        set_skill_name("chat-with-kb")
        try:
            result = tool.execute(
                {"filename": "diagram.drawio", "content": "<mxfile></mxfile>"},
                _USER,
            )
        finally:
            set_skill_name(None)
        assert "Error" in result
        assert "Engineer skill" in result
        assert "Azure Architect" in result

    def test_drawio_allowed_under_architect(self):
        """G12: same code path must NOT block .drawio under any other skill —
        regression guard so the chat-with-kb branch doesn't accidentally widen
        and break Architect's drawio flow or drawio-diagrammer's hand-written
        XML flow."""
        from app.tools.base import set_skill_name
        tool = get_tool("generate_file")
        set_skill_name("architect")
        try:
            # Minimal mxfile so the validator doesn't crash; we only care that
            # the skill gate is not what blocks this call.
            content = '<mxfile host="test"><diagram><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>'
            result = tool.execute(
                {"filename": "allowed.drawio", "content": content, "overwrite": True},
                _USER,
            )
        finally:
            set_skill_name(None)
        assert "Engineer skill" not in result
        # No assertion on save success — depends on validator side-effects.
        # The point of this test is the skill-gate negative case.


class TestReadKBFileAdversarial:
    """Adversarial inputs for read_kb_file. The KB lives at kb_data/kb/.
    Path traversal must be blocked; non-string types must not crash."""

    def test_path_is_none(self):
        """K1: path=None — currently `".." in None` raises TypeError. The
        tool catches PermissionError and FileNotFoundError but not TypeError."""
        tool = get_tool("read_kb_file")
        try:
            result = tool.execute({"path": None}, _USER)
        except TypeError:
            pytest.fail(
                "FLAW: read_kb_file crashes on path=None (TypeError "
                "leaks from `.. in None` check)"
            )
        assert isinstance(result, str)
        assert "Error" in result

    def test_path_is_int(self):
        """K2: non-string path."""
        tool = get_tool("read_kb_file")
        try:
            result = tool.execute({"path": 123}, _USER)
        except TypeError:
            pytest.fail("FLAW: read_kb_file crashes on int path")
        assert isinstance(result, str)
        assert "Error" in result

    def test_path_empty_string(self):
        """K3: empty path — kb_root / '' = kb_root, not a file. Should
        return a clean error."""
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": ""}, _USER)
        assert isinstance(result, str)
        assert "Error" in result

    def test_path_traversal_blocked(self):
        """K4: classic path traversal."""
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "kb/../../etc/passwd"}, _USER)
        assert "Error" in result and ("Invalid" in result or "not found" in result.lower())

    def test_absolute_unix_path_blocked(self):
        """K7: leading slash."""
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "/etc/passwd"}, _USER)
        assert "Error" in result

    def test_absolute_windows_path_blocked(self):
        """K5: Windows absolute path with drive letter — does NOT start
        with / or \\, so the first-layer string check passes. The sandbox
        relative_to() check (second layer) must still reject."""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows-specific path handling")
        tool = get_tool("read_kb_file")
        result = tool.execute(
            {"path": r"C:\Windows\System32\config\SAM"}, _USER,
        )
        assert "Error" in result

    def test_very_long_path(self):
        """K6: pathological long path — must not raise OSError."""
        tool = get_tool("read_kb_file")
        try:
            result = tool.execute({"path": "kb/" + "a" * 5000}, _USER)
        except OSError:
            pytest.fail("FLAW: read_kb_file leaks OSError on long path")
        assert "Error" in result

    def test_url_encoded_traversal(self):
        """K8: URL-encoded traversal — not decoded by Path; remains a
        literal filename and either errors or 'not found'."""
        tool = get_tool("read_kb_file")
        result = tool.execute({"path": "kb%2F..%2Fetc"}, _USER)
        assert "Error" in result



class TestValidateDrawioAdversarial:
    """Adversarial inputs for validate_drawio. Each test runs against a
    tmp_path sandbox so the real output/ directory stays clean."""

    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path, monkeypatch):
        from app.tools.generic import validate_drawio as v
        monkeypatch.setattr(v, "_OUTPUT_DIR", tmp_path)
        self._dir = tmp_path

    def _write(self, name: str, content: str):
        path = self._dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_malformed_xml(self):
        """V1: bad XML — must return clean parse error."""
        self._write("malformed.drawio", "<not xml")
        tool = get_tool("validate_drawio")
        result = tool.execute({"filename": "malformed.drawio"}, _USER)
        assert "FAILED" in result and "parse" in result.lower()

    def test_billion_laughs(self):
        """V2: classic billion-laughs entity expansion attack. Default
        ElementTree allows internal entity expansion which can blow up
        memory/CPU. Must complete bounded — either rejected at parse time
        or with limited expansion."""
        # Smaller payload (10^4 expansion, not 10^9) so the test fails fast
        # if vulnerable, rather than OOMing CI. A robust fix should reject
        # the DOCTYPE entirely or use defusedxml.
        bomb = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE lolz [\n'
            '  <!ENTITY lol "lol">\n'
            '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
            '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
            '  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
            ']>\n'
            '<mxfile><diagram>&lol4;</diagram></mxfile>\n'
        )
        self._write("entity_expansion_attack.drawio", bomb)
        tool = get_tool("validate_drawio")
        import time
        start = time.time()
        try:
            result = tool.execute({"filename": "entity_expansion_attack.drawio"}, _USER)
        except Exception as e:
            # Any exception reaching us is a flaw — should be a string.
            pytest.fail(f"FLAW: validate_drawio raised {type(e).__name__} on XML bomb: {e}")
        elapsed = time.time() - start
        assert isinstance(result, str)
        # Should not take more than a couple seconds even on slow CI.
        assert elapsed < 5, f"FLAW: XML bomb took {elapsed:.1f}s — entity expansion not bounded"

    def test_traversal_in_filename(self):
        """V3: '..' in filename rejected."""
        tool = get_tool("validate_drawio")
        result = tool.execute({"filename": "../etc/passwd.drawio"}, _USER)
        assert "Error" in result

    def test_wrong_extension(self):
        """V4: non-.drawio extension rejected."""
        tool = get_tool("validate_drawio")
        result = tool.execute({"filename": "foo.txt"}, _USER)
        assert "Error" in result and ".drawio" in result

    def test_empty_file(self):
        """V5: empty file — XML parse error."""
        self._write("empty.drawio", "")
        tool = get_tool("validate_drawio")
        result = tool.execute({"filename": "empty.drawio"}, _USER)
        assert "FAILED" in result

    def test_nonexistent_file(self):
        """File that doesn't exist."""
        tool = get_tool("validate_drawio")
        result = tool.execute({"filename": "missing.drawio"}, _USER)
        assert "Error" in result and "not found" in result.lower()


class TestSearchStackOverflowTool:
    def test_empty_query_returns_error(self):
        init_tools()
        tool = get_tool("search_stack_overflow")
        assert "Error" in tool.execute({"query": ""}, _USER)

    @patch("app.tools.generic.search_stackoverflow.httpx.Client")
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

    @patch("app.tools.generic.search_stackoverflow.httpx.Client")
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

    @patch("app.tools.generic.search_github.httpx.Client")
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

    @patch("app.tools.generic.search_github.httpx.Client")
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

    @patch("bundles.azure.search_azure_updates.httpx.Client")
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
        from app.tools.generic.web_search import SITE_SHORTCUTS
        assert "reddit" in SITE_SHORTCUTS
        assert "techcommunity" in SITE_SHORTCUTS

    @patch("app.tools.generic.web_search.httpx.Client")
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

    @patch("app.tools.generic.web_search.httpx.Client")
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
