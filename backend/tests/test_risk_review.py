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

    def test_hookless_tool_falls_back_not_truncated(self):
        from app.agent.risk_review import render_for_human
        text, truncated = render_for_human("az_cli", {"args": ["group", "list"]})
        assert text.startswith("az ") and truncated is False


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
