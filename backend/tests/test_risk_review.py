"""Tests for the advisory approval risk review (DESIGN.md §5 2026-06-04)."""

import types

import pytest

from app.agent import risk_review
from app.agent.risk_review import (
    CAUTION,
    DESTRUCTIVE,
    SAFE,
    assess_risk,
    deterministic_floor,
    _shell_floor,
)
from app.config import get_settings

# Importing these modules registers their tools in TOOL_REGISTRY (via
# __init_subclass__), so the deterministic_floor / render_command hooks resolved
# through the registry are reachable here even though conftest does not call
# init_tools(). See DESIGN.md §5 2026-06-12.
import bundles.azure.az_cli  # noqa: E402,F401
import bundles.azure.az_rest  # noqa: E402,F401
import app.tools.generic.execute_script  # noqa: E402,F401


# ── Deterministic floor ───────────────────────────────────────────────────────


class TestDeterministicFloor:
    def test_az_delete_is_destructive(self):
        assert deterministic_floor("az_cli", {"args": ["group", "delete", "-n", "rg1"]}) == DESTRUCTIVE

    def test_az_purge_is_destructive(self):
        assert deterministic_floor("az_cli", {"args": ["keyvault", "purge", "-n", "kv"]}) == DESTRUCTIVE

    def test_az_list_is_safe(self):
        assert deterministic_floor("az_cli", {"args": ["group", "list"]}) == SAFE

    def test_az_show_is_safe(self):
        assert deterministic_floor("az_cli", {"args": ["vm", "show", "-n", "x", "-g", "rg"]}) == SAFE

    def test_az_create_is_caution(self):
        assert deterministic_floor("az_cli", {"args": ["group", "create", "-n", "rg", "-l", "eastus"]}) == CAUTION

    def test_destructive_token_after_global_flags_still_caught(self):
        # global flags must not be usable as a shield (mirrors _is_blocked behaviour)
        floor = deterministic_floor("az_cli", {"args": ["--only-show-errors", "group", "delete", "-n", "rg"]})
        assert floor == DESTRUCTIVE

    def test_rest_delete_is_destructive(self):
        assert deterministic_floor("az_rest_api", {"method": "DELETE", "url": "/x"}) == DESTRUCTIVE

    def test_rest_get_is_safe(self):
        assert deterministic_floor("az_rest_api", {"method": "GET", "url": "/x"}) == SAFE

    def test_rest_patch_is_caution(self):
        assert deterministic_floor("az_rest_api", {"method": "PATCH", "url": "/x"}) == CAUTION

    def test_unknown_tool_is_caution(self):
        assert deterministic_floor("some_new_tool", {}) == CAUTION


class TestRemoteExecFloor:
    """Remote-exec az commands floor to ⛔ via the tool's risk_floor hook
    (DESIGN.md §5 2026-06-12). Requires the Azure bundle to be loaded so
    get_tool('az_cli') resolves the instance carrying the hook."""

    @pytest.mark.parametrize("args", [
        ["vm", "run-command", "invoke", "-g", "rg", "-n", "vm1",
         "--command-id", "RunShellScript", "--scripts", "echo hi"],
        ["vm", "run-command", "create", "-g", "rg", "--vm-name", "vm1"],
        ["vmss", "run-command", "invoke", "-g", "rg", "-n", "vmss1"],
        ["aks", "command", "invoke", "-g", "rg", "-n", "aks1", "-c", "kubectl get pods"],
        ["container", "exec", "-g", "rg", "-n", "ci1", "--exec-command", "/bin/sh"],
        ["containerapp", "exec", "-g", "rg", "-n", "app1"],
        ["webapp", "ssh", "-g", "rg", "-n", "web1"],
        ["webapp", "create-remote-connection", "-g", "rg", "-n", "web1"],
        ["ssh", "vm", "-g", "rg", "-n", "vm1"],
        ["acr", "run", "-r", "reg", "-f", "task.yaml", "."],
        ["acr", "build", "-r", "reg", "-t", "img:1", "."],
    ])
    def test_remote_exec_is_destructive(self, args):
        assert deterministic_floor("az_cli", {"args": args}) == DESTRUCTIVE

    def test_remote_exec_survives_global_flag_shield(self):
        floor = deterministic_floor(
            "az_cli",
            {"args": ["--only-show-errors", "vm", "run-command", "invoke",
                      "-g", "rg", "-n", "vm1", "--scripts", "whoami"]},
        )
        assert floor == DESTRUCTIVE

    @pytest.mark.parametrize("args", [
        ["vm", "run-command", "list", "-g", "rg", "-n", "vm1"],
        ["vm", "run-command", "show", "-g", "rg", "-n", "vm1", "--run-command-name", "x"],
    ])
    def test_run_command_reads_stay_safe(self, args):
        # The action verb is part of the matched sequence, so list/show forms of
        # the same command group must NOT trip the destructive floor.
        assert deterministic_floor("az_cli", {"args": args}) == SAFE

    def test_unenumerated_verb_falls_to_caution_not_destructive(self):
        # A remote-ish verb we did not enumerate is left for the LLM reviewer to
        # escalate; the deterministic floor itself stays at caution, not ⛔.
        assert deterministic_floor("az_cli", {"args": ["webapp", "deploy", "-g", "rg", "-n", "w"]}) == CAUTION

    def test_hook_tier_matches_risk_review_destructive(self):
        # The tool returns a literal "destructive"; guard it against drift from
        # risk_review.DESTRUCTIVE (the hook contract).
        from bundles.azure.az_cli import AzCliTool
        tier = AzCliTool().risk_floor(
            {"args": ["vm", "run-command", "invoke", "--scripts", "x"]}
        )
        assert tier == DESTRUCTIVE


class TestRestBodyReview:
    """az_rest_api mutations are reviewed on their resolved body, and an
    oversized (unreviewable) body floors to ⛔ (DESIGN.md §5 2026-06-12)."""

    @pytest.fixture
    def output_file(self):
        from pathlib import Path
        created = []

        def _make(name: str, content: str) -> str:
            p = Path("output") / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            created.append(p)
            return name

        yield _make
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass

    def test_inline_body_shown_to_reviewer(self):
        from app.agent.risk_review import render_command
        out = render_command(
            "az_rest_api",
            {"method": "PATCH", "url": "/x", "body": '{"publicNetworkAccess":"Enabled"}'},
        )
        assert "publicNetworkAccess" in out

    def test_body_file_content_shown_not_just_filename(self, output_file):
        from app.agent.risk_review import render_command
        name = output_file("review_patch.json", '{"roleDefinition":"Owner"}')
        out = render_command(
            "az_rest_api", {"method": "PUT", "url": "/x", "body_file": name}
        )
        assert "Owner" in out          # the payload, not just the pointer
        assert name not in out or "Owner" in out

    def test_oversized_inline_body_floors_destructive(self):
        big = '{"x":"' + "a" * 20000 + '"}'
        assert deterministic_floor(
            "az_rest_api", {"method": "PATCH", "url": "/x", "body": big}
        ) == DESTRUCTIVE

    def test_oversized_body_file_floors_destructive(self, output_file):
        name = output_file("big_body.json", '{"x":"' + "a" * 20000 + '"}')
        assert deterministic_floor(
            "az_rest_api", {"method": "PUT", "url": "/x", "body_file": name}
        ) == DESTRUCTIVE

    def test_small_mutation_stays_caution(self):
        assert deterministic_floor(
            "az_rest_api", {"method": "PATCH", "url": "/x", "body": '{"a":1}'}
        ) == CAUTION

    def test_delete_stays_destructive(self):
        assert deterministic_floor("az_rest_api", {"method": "DELETE", "url": "/x"}) == DESTRUCTIVE

    def test_get_not_escalated_by_stray_large_body(self):
        # reads carry no body; a GET stays safe even with a stray large body arg
        assert deterministic_floor(
            "az_rest_api", {"method": "GET", "url": "/x", "body": "a" * 20000}
        ) == SAFE

    def test_truncation_marker_present_on_oversized(self):
        from app.agent.risk_review import render_command
        big = '{"x":"' + "a" * 20000 + '"}'
        out = render_command("az_rest_api", {"method": "PATCH", "url": "/x", "body": big})
        assert "truncated" in out.lower()

    def test_hook_tier_matches_risk_review_destructive(self):
        from bundles.azure.az_rest import AzRestApiTool
        tier = AzRestApiTool().risk_floor(
            {"method": "PUT", "url": "/x", "body": "a" * 20000}
        )
        assert tier == DESTRUCTIVE


class TestScriptBodyReview:
    """execute_script: reviewer sees the resolved body (16 KB window), and an
    over-window script floors to ⛔ so its unseen tail can't pass on the LLM's
    partial view (DESIGN.md §5 2026-06-12). _shell_floor still scans the full
    body for destructive substrings independent of the window."""

    @pytest.fixture
    def script_file(self):
        from pathlib import Path
        created = []

        def _make(name: str, content: str) -> str:
            p = Path("output") / "scripts" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            created.append(p)
            return name

        yield _make
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass

    def test_script_body_shown_to_reviewer(self, script_file):
        from app.agent.risk_review import render_command
        name = script_file("diag.ps1", "Get-AzVM | Format-Table")
        out = render_command("execute_script", {"path": name, "reason": "x"})
        assert "Get-AzVM" in out

    def test_small_benign_script_stays_caution(self, script_file):
        name = script_file("small.ps1", "Get-Date")
        assert deterministic_floor("execute_script", {"path": name}) == CAUTION

    def test_destructive_substring_still_caught(self, script_file):
        # full-body _shell_floor catches this regardless of the window
        name = script_file("danger.ps1", "Remove-Item -Path C:\\data -Recurse -Force")
        assert deterministic_floor("execute_script", {"path": name}) == DESTRUCTIVE

    def test_oversized_script_floors_destructive(self, script_file):
        # ~24 KB of benign padding: no destructive substring, but past the window
        name = script_file("big.ps1", "Write-Host start\n" + "# pad line\n" * 4000)
        assert deterministic_floor("execute_script", {"path": name}) == DESTRUCTIVE

    def test_oversized_script_render_has_marker(self, script_file):
        from app.agent.risk_review import render_command
        name = script_file("big2.ps1", "Write-Host start\n" + "# pad line\n" * 4000)
        out = render_command("execute_script", {"path": name, "reason": "x"})
        assert "truncated" in out.lower()

    def test_hook_tier_matches_risk_review_destructive(self, script_file):
        from app.tools.generic.execute_script import ExecuteScriptTool
        name = script_file("big3.ps1", "x\n" + "# pad line\n" * 4000)
        assert ExecuteScriptTool().risk_floor({"path": name}) == DESTRUCTIVE


class TestHumanRender:
    """render_for_human / render_command_full: deterministic (NO LLM), 64 KB
    human window vs the reviewer's 16 KB, truncated flag drives the card's
    download button (DESIGN.md §5 2026-06-12)."""

    def test_small_command_not_truncated(self):
        from app.agent.risk_review import render_for_human
        text, truncated = render_for_human(
            "az_rest_api", {"method": "PUT", "url": "/x", "body": '{"a":1}'}
        )
        assert "/x" in text and truncated is False

    def test_over_64kb_is_truncated(self):
        from app.agent.risk_review import render_for_human
        big = '{"x":"' + "a" * 70000 + '"}'
        text, truncated = render_for_human(
            "az_rest_api", {"method": "PUT", "url": "/x", "body": big}
        )
        assert truncated is True
        assert "truncated" in text.lower()

    def test_human_sees_more_than_reviewer(self):
        # 32 KB body: truncated for the 16 KB reviewer, but shown in full to the
        # 64 KB human card — the whole point of the separate window.
        from app.agent.risk_review import render_for_human, render_command
        mid = '{"x":"' + "a" * 32000 + '"}'
        args = {"method": "PUT", "url": "/x", "body": mid}
        htext, htrunc = render_for_human("az_rest_api", args)
        assert htrunc is False and "truncated" not in htext.lower()
        assert "truncated" in render_command("az_rest_api", args).lower()

    def test_full_render_is_uncapped(self):
        from app.agent.risk_review import render_command_full
        big = "a" * 70000
        out = render_command_full("az_rest_api", {"method": "PUT", "url": "/x", "body": big})
        assert "truncated" not in out.lower()
        assert out.count("a") >= 70000

    def test_short_command_not_truncated(self):
        from app.agent.risk_review import render_for_human
        text, truncated = render_for_human("az_cli", {"args": ["group", "list"]})
        assert text.startswith("az ") and truncated is False


class TestAzClassification:
    """#16/#17 — AzCliTool.risk_floor owns the full az classification; core's
    deterministic_floor defers to it (DESIGN.md §5 2026-06-13)."""

    def test_privesc_grant_floors_destructive(self):
        # role assignment create (grant self Owner) — floored ⛔, not blocked.
        assert deterministic_floor(
            "az_cli",
            {"args": ["role", "assignment", "create", "--role", "Owner",
                      "--assignee", "me", "--scope", "/subscriptions/x"]},
        ) == DESTRUCTIVE
        assert deterministic_floor(
            "az_cli", {"args": ["role", "definition", "update", "--role-definition", "x"]}
        ) == DESTRUCTIVE

    def test_credential_read_floors_caution_not_safe(self):
        # `secret show` uses the read verb `show` but returns a secret — it must
        # NOT hit the read-verb SAFE shortcut.
        assert deterministic_floor(
            "az_cli", {"args": ["keyvault", "secret", "show", "--name", "db-pw", "--vault-name", "kv"]}
        ) == CAUTION
        assert deterministic_floor(
            "az_cli", {"args": ["storage", "account", "keys", "list", "-n", "sa"]}
        ) == CAUTION
        assert deterministic_floor(
            "az_cli", {"args": ["cosmosdb", "list-keys", "-n", "c", "-g", "rg"]}
        ) == CAUTION

    def test_plain_read_still_safe(self):
        assert deterministic_floor("az_cli", {"args": ["vm", "list"]}) == SAFE
        assert deterministic_floor("az_cli", {"args": ["keyvault", "list"]}) == SAFE

    def test_power_state_is_caution_via_default(self):
        # stop/deallocate are reversible — caution, not destructive.
        assert deterministic_floor("az_cli", {"args": ["vm", "deallocate", "-n", "x", "-g", "rg"]}) == CAUTION
        assert deterministic_floor("az_cli", {"args": ["vm", "stop", "-n", "x", "-g", "rg"]}) == CAUTION

    def test_core_defers_entirely_to_hook(self):
        # deterministic_floor's az_cli branch is `_tool_risk_floor(...) or CAUTION`
        # — its verdict must equal the tool hook's for every shape.
        from bundles.azure.az_cli import AzCliTool
        tool = AzCliTool()
        for args in (
            ["group", "delete", "-n", "rg"],
            ["group", "list"],
            ["role", "assignment", "create", "--role", "Owner"],
            ["keyvault", "secret", "show", "-n", "s"],
            ["vm", "deallocate", "-n", "x"],
        ):
            assert deterministic_floor("az_cli", {"args": args}) == tool.risk_floor({"args": args})

    def test_hook_tiers_match_risk_review_constants(self):
        # Guard the literal strings the hook returns against drift.
        from bundles.azure.az_cli import AzCliTool
        tool = AzCliTool()
        assert tool.risk_floor({"args": ["role", "assignment", "create"]}) == DESTRUCTIVE
        assert tool.risk_floor({"args": ["keyvault", "secret", "show", "-n", "s"]}) == CAUTION
        assert tool.risk_floor({"args": ["vm", "list"]}) == SAFE


class TestSecretMasking:
    """#16 Surface A — secret-bearing args are masked everywhere a human or the
    judge LLM sees the rendered command (DESIGN.md §5 2026-06-13)."""

    def test_render_masks_sensitive_flag_value(self):
        from app.agent.risk_review import render_command
        out = render_command(
            "az_cli",
            {"args": ["keyvault", "secret", "set", "--name", "x", "--value", "hunter2"]},
        )
        assert "hunter2" not in out
        assert "***" in out
        assert "--value" in out  # flag still visible; only the value masked

    def test_render_masks_admin_password_and_inline_form(self):
        from app.agent.risk_review import render_command
        out = render_command(
            "az_cli",
            {"args": ["vm", "create", "-n", "v", "--admin-password=P@ssw0rd!", "--admin-username", "az"]},
        )
        assert "P@ssw0rd!" not in out
        assert "az" in out  # non-secret value preserved

    def test_sensitive_flag_masks_whole_value_even_if_connection_string(self):
        from app.agent.risk_review import render_command
        cs = "DefaultEndpointsProtocol=https;AccountName=sa;AccountKey=abc123XYZ==;"
        out = render_command("az_cli", {"args": ["storage", "blob", "list", "--connection-string", cs]})
        assert "abc123XYZ==" not in out
        assert "--connection-string ***" in out  # flag-guarded → whole value masked

    def test_connection_string_shape_masked_without_sensitive_flag(self):
        # A secret shape that isn't behind a sensitive flag (e.g. a bare token)
        # is still caught by the value-shape regex.
        from app.agent.risk_review import render_command
        out = render_command(
            "az_cli", {"args": ["storage", "blob", "list", "--query", "AccountKey=abc123XYZ==;tail"]}
        )
        assert "abc123XYZ==" not in out
        assert "AccountKey=***" in out

    def test_render_for_human_masks_too(self):
        from app.agent.risk_review import render_for_human
        text, _ = render_for_human(
            "az_cli", {"args": ["keyvault", "secret", "set", "-n", "x", "--value", "topsecret"]}
        )
        assert "topsecret" not in text and "***" in text

    def test_masking_is_display_only_execution_args_unchanged(self):
        # render is a pure read; the caller's func_args must be untouched so the
        # executed command still carries the real value.
        from app.agent.risk_review import render_command
        func_args = {"args": ["keyvault", "secret", "set", "-n", "x", "--value", "live-secret"]}
        render_command("az_cli", func_args)
        assert func_args["args"][-1] == "live-secret"


class TestRedactOutput:
    """#16 Surface B — credential-read OUTPUT is redacted before persistence /
    replay; the hook is resolved generically through the registry."""

    def test_credential_read_output_redacted(self):
        from app.tools.base import redact_tool_output
        out = redact_tool_output(
            "az_cli",
            {"args": ["keyvault", "secret", "show", "-n", "db-pw"]},
            '{"value": "super-secret-password"}',
        )
        assert "super-secret-password" not in out
        assert "redacted" in out.lower()

    def test_non_credential_output_passes_through(self):
        from app.tools.base import redact_tool_output
        payload = '{"name": "myvm", "powerState": "running"}'
        assert redact_tool_output("az_cli", {"args": ["vm", "show", "-n", "myvm"]}, payload) == payload

    def test_unknown_tool_passes_through(self):
        from app.tools.base import redact_tool_output
        assert redact_tool_output("no_such_tool", {}, "data") == "data"


class TestShellFloor:
    def test_unreadable_body_is_caution(self):
        assert _shell_floor(None) == CAUTION

    def test_benign_script_is_caution_not_safe(self):
        assert _shell_floor("Get-AzResourceGroup | Format-Table") == CAUTION

    @pytest.mark.parametrize("body", [
        "rm -rf /tmp/x",
        "Remove-Item -Path . -Recurse -Force",
        "DROP TABLE users;",
        "dd if=/dev/zero of=/dev/sda",
    ])
    def test_destructive_bodies(self, body):
        assert _shell_floor(body) == DESTRUCTIVE


# ── assess_risk: gating, escalate-only, fail-closed ───────────────────────────


def _fake_response(content: str):
    msg = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(choices=[choice])


class _FakeClient:
    def __init__(self, content=None, raises=False):
        self._content = content
        self._raises = raises
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raises:
            raise RuntimeError("boom")
        return _fake_response(self._content)


class TestAssessRisk:
    def test_disabled_returns_floor_only(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", False)
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE
        assert v.description is None
        assert v.source == "floor"

    def test_llm_can_escalate_above_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(
            risk_review, "_get_client",
            lambda: _FakeClient('{"risk": "destructive", "description": "Deletes all RGs"}'),
        )
        # floor for `group list` is safe; the LLM escalates to destructive
        v = assess_risk("az_cli", {"args": ["group", "list"]})
        assert v.risk_level == DESTRUCTIVE
        assert v.description == "Deletes all RGs"
        assert v.source == "llm"

    def test_llm_cannot_downgrade_below_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(
            risk_review, "_get_client",
            lambda: _FakeClient('{"risk": "safe", "description": "just a delete"}'),
        )
        # floor for `group delete` is destructive; a model "safe" must not win
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE

    def test_failclosed_to_at_least_caution(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(risk_review, "_get_client", lambda: _FakeClient(raises=True))
        v = assess_risk("az_cli", {"args": ["group", "list"]})  # floor=safe
        assert v.risk_level == CAUTION  # never "safe" on review failure
        assert v.source == "fallback"
        assert v.description and "unavailable" in v.description.lower()

    def test_failclosed_keeps_destructive_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(risk_review, "_get_client", lambda: _FakeClient(raises=True))
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE


# ── az_cli @file indirection (DESIGN.md §5 2026-06-15) ────────────────────────


class TestAzCliAtFile:
    """az's `@file` convention loads an arg value from disk at execution. Every
    @file must resolve inside the output/ sandbox (hard reject otherwise),
    rewrite to an absolute path, be fingerprinted for the #20 TOCTOU re-check,
    and be shown as content to the human but only as a pointer to the judge/DB."""

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        """Point the shared resolver's output/ root at a tmp dir so tests don't
        touch the real sandbox. `resolve_output_file` reads `_OUTPUT_DIR` at call
        time, so patching the module attribute is enough."""
        import bundles.azure._az_base as az_base
        monkeypatch.setattr(az_base, "_OUTPUT_DIR", tmp_path)
        return tmp_path

    # ── boundary guard ──────────────────────────────────────────────────────
    @pytest.mark.parametrize("bad", ["@../secret.txt", "@../../backend/.env", "@/etc/passwd"])
    def test_escape_is_hard_rejected(self, sandbox, bad):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        out, resolved, err = _resolve_and_rewrite_at_files(
            ["vm", "run-command", "invoke", "--scripts", bad]
        )
        assert out is None and resolved == []
        assert err and err.startswith("Error")

    def test_unreadable_at_file_rejected(self, sandbox):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        out, _resolved, err = _resolve_and_rewrite_at_files(["x", "--scripts", "@ghost.ps1"])
        assert out is None
        assert err and "not found" in err

    def test_valid_at_file_resolved_and_rewritten_to_abspath(self, sandbox):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        (sandbox / "x.ps1").write_text("Write-Host hi", encoding="utf-8")
        out, resolved, err = _resolve_and_rewrite_at_files(["vm", "--scripts", "@x.ps1"])
        assert err is None
        assert len(resolved) == 1
        scripts_val = out[out.index("--scripts") + 1]
        assert scripts_val.startswith("@")
        # rewritten to an absolute path under the sandbox, not the relative form
        from pathlib import Path
        assert Path(scripts_val[1:]).is_absolute()
        assert scripts_val.endswith("x.ps1")
        assert scripts_val != "@x.ps1"

    def test_output_prefixed_form_accepted(self, sandbox):
        # The natural az cwd-relative form `@output/x.ps1` resolves the same file
        # as `@x.ps1` rather than double-prefixing to output/output/x.ps1.
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        (sandbox / "x.ps1").write_text("hi", encoding="utf-8")
        out, resolved, err = _resolve_and_rewrite_at_files(["vm", "--scripts", "@output/x.ps1"])
        assert err is None and len(resolved) == 1

    def test_inline_flag_form_resolved(self, sandbox):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        (sandbox / "p.json").write_text("{}", encoding="utf-8")
        out, resolved, err = _resolve_and_rewrite_at_files(["deployment", "--parameters=@p.json"])
        assert err is None and len(resolved) == 1
        val = out[-1]
        assert val.startswith("--parameters=@") and val.endswith("p.json")

    # ── JMESPath carve-out ──────────────────────────────────────────────────
    def test_query_at_node_not_treated_as_file(self, sandbox):
        # `--query @.name` is JMESPath current-node, not an @file — must pass
        # through untouched (no error even though output/.name doesn't exist).
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        out, resolved, err = _resolve_and_rewrite_at_files(["vm", "list", "--query", "@.name"])
        assert err is None and resolved == []
        assert out == ["vm", "list", "--query", "@.name"]

    def test_query_inline_at_not_treated_as_file(self, sandbox):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        out, resolved, err = _resolve_and_rewrite_at_files(["vm", "list", "--query=@"])
        assert err is None and resolved == []

    def test_bare_at_not_treated_as_file(self, sandbox):
        from bundles.azure.az_cli import _resolve_and_rewrite_at_files
        out, resolved, err = _resolve_and_rewrite_at_files(["vm", "list", "--query", "@"])
        assert err is None and resolved == []

    # ── #20 fingerprint ─────────────────────────────────────────────────────
    def test_fingerprint_none_without_at_file(self, sandbox):
        from bundles.azure.az_cli import AzCliTool
        assert AzCliTool().review_fingerprint({"args": ["group", "list"]}) is None

    def test_fingerprint_changes_on_swap(self, sandbox):
        from bundles.azure.az_cli import AzCliTool
        tool = AzCliTool()
        f = sandbox / "s.ps1"
        f.write_text("Get-Date", encoding="utf-8")
        fp1 = tool.review_fingerprint({"args": ["vm", "--scripts", "@s.ps1"]})
        assert fp1 is not None
        f.write_text("Remove-Item -Recurse -Force C:\\", encoding="utf-8")
        fp2 = tool.review_fingerprint({"args": ["vm", "--scripts", "@s.ps1"]})
        assert fp1 != fp2

    def test_fingerprint_multi_file_order_independent(self, sandbox):
        from bundles.azure.az_cli import AzCliTool
        tool = AzCliTool()
        (sandbox / "a.ps1").write_text("AAA", encoding="utf-8")
        (sandbox / "b.ps1").write_text("BBB", encoding="utf-8")
        fp_ab = tool.review_fingerprint({"args": ["x", "@a.ps1", "@b.ps1"]})
        fp_ba = tool.review_fingerprint({"args": ["x", "@b.ps1", "@a.ps1"]})
        assert fp_ab == fp_ba and fp_ab is not None

    def test_fingerprint_resolved_via_registry(self, sandbox):
        # The orchestrator reaches the hook generically through the registry.
        from app.agent.risk_review import review_fingerprint
        (sandbox / "s.ps1").write_text("x", encoding="utf-8")
        assert review_fingerprint("az_cli", {"args": ["vm", "--scripts", "@s.ps1"]}) is not None

    # ── surface split: human=content, judge/DB=pointer ──────────────────────
    def test_judge_render_shows_pointer_not_content(self, sandbox):
        from app.agent.risk_review import render_command
        (sandbox / "s.ps1").write_text("SECRET-SCRIPT-BODY", encoding="utf-8")
        out = render_command("az_cli", {"args": ["vm", "run-command", "invoke", "--scripts", "@s.ps1"]})
        assert "SECRET-SCRIPT-BODY" not in out   # judge never sees file content
        assert "@s.ps1" in out                   # only the pointer

    def test_human_render_shows_content(self, sandbox):
        from app.agent.risk_review import render_for_human
        (sandbox / "s.ps1").write_text("SECRET-SCRIPT-BODY", encoding="utf-8")
        text, _trunc = render_for_human(
            "az_cli", {"args": ["vm", "run-command", "invoke", "--scripts", "@s.ps1"]}
        )
        assert "SECRET-SCRIPT-BODY" in text       # the human (the gate) sees what runs

    def test_judge_render_budget_below_human_window(self):
        # Drift guard: the judge default budget must stay below the content
        # threshold and the human window above it, or the surface split inverts.
        from bundles.azure.az_cli import _JUDGE_RENDER_MAX
        from app.agent.risk_review import _HUMAN_COMMAND_WINDOW
        assert 16384 <= _JUDGE_RENDER_MAX < _HUMAN_COMMAND_WINDOW
